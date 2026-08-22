"""Build 3's second verb: `adopt answer` -- the capture ratchet's one command.

`adopt ask` said UNKNOWN, `--escalate` recorded the question, a human knows the
answer. This turns that into confirmed, bound, provenance-carrying knowledge so
the *next* asker gets KNOWN -- in one command, because capture that costs four
commands loses to a Slack reply and the store stays ignorant.

**Every `adopt_ask` import happens inside the command body**, as `ask` and
`map_command` do it, so `CLI_COLD_START_MS` holds.

**The adapter below is the whole of this module's own logic**, and it is
deliberately dumb: `adopt_ask.capture` decides what a captured answer is --
human-authored, verified, bound structurally, stamped last -- and this class only
knows which facade performs each write over the one open store. A decision that
migrated down here would be a decision `adopt serve` and every later caller
could not share.
"""

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Annotated, Protocol, cast

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_model._enums import AuthorityClass, ItemKind, SourceType, Verification
from adopt_scope import Scope

__all__ = ["answer"]


class _Backend(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...


class _Items(Protocol):
    def record(
        self,
        *,
        scope: Scope,
        kind: ItemKind,
        title: str,
        body_md: str,
        authority_class: AuthorityClass,
        verification: Verification | None = None,
        confidence: float | None = None,
        source_version: str | None = None,
        actor_id: str | None = None,
    ) -> tuple[str, str]: ...

    def record_provenance(
        self, *, revision_id: str, source_type: SourceType, source_ref: str
    ) -> str: ...


class _Bindings(Protocol):
    def create(
        self, *, item_id: str, identity_id: str, is_load_bearing: bool
    ) -> tuple[str, str]: ...


class _Governance(Protocol):
    def answer_escalation(
        self, *, escalation_id: str, candidate_revision_id: str, answered_by: str | None = None
    ) -> object: ...


class CaptureStoreView(Protocol):
    """The slice of the store handle the adapter drives.

    Structural rather than imported, for `_knowledge_support.KnowledgeStoreView`'s
    reason (CR-36): `adopt_cli.store_option` is the only module `no-raw-sqlite`
    exempts, so every other CLI module reaches the store through a shape.
    """

    @property
    def backend(self) -> _Backend:
        """Read-only on purpose: a mutable protocol attribute is invariant, so a
        handle whose `backend` is a concrete `SqliteStore` would not satisfy it.
        Nothing here assigns to it either, which is the honest shape."""
        ...

    def items(self) -> _Items: ...
    def bindings(self) -> _Bindings: ...
    def governance(self) -> _Governance: ...


EscalationArgument = Annotated[
    str, typer.Argument(help="The question id `adopt ask --escalate` printed.")
]
TextOption = Annotated[str, typer.Option("--text", help="The answer, in plain language.")]
UriOption = Annotated[
    list[str] | None,
    typer.Option("--uri", help="Bind the answer to this identity. Repeatable."),
]
TitleOption = Annotated[
    str | None,
    typer.Option("--title", help="Item title. Defaults to the question that was asked."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
ActorOption = Annotated[str | None, typer.Option("--actor", help="Who answered.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def answer(
    escalation_id: EscalationArgument,
    text: TextOption,
    uri: UriOption = None,
    title: TitleOption = None,
    scope: ScopeOption = None,
    store: StoreOption = None,
    actor: ActorOption = None,
    json_output: JsonOption = False,
) -> None:
    """Bank a human's answer as confirmed knowledge and resolve the question."""
    from adopt_ask.capture import capture_answer, identities_to_bind

    from adopt_cli.commands._knowledge_support import identity_views
    from adopt_cli.commands._map_support import resolve_scope
    from adopt_obs import AdoptError, ErrorCode

    handle = open_configured_store(store, read_only=False)
    try:
        resolved_scope = resolve_scope(handle, scope)
        existing = handle.governance().get_escalation(escalation_id)
        if existing is None:
            raise AdoptError(
                ErrorCode.ESCALATION_NOT_FOUND,
                message=f"no escalation {escalation_id!r} in this store",
                hint="`adopt ask --escalate` prints the id it created, and ids are per "
                "store. Check --store if you have more than one.",
            )

        identity_ids, unmatched = identities_to_bind(
            text, identity_views(handle, resolved_scope), explicit_uris=uri or []
        )
        result = capture_answer(
            _StoreAdapter(handle),
            escalation_id=escalation_id,
            scope=resolved_scope,
            # The question is the title, so the captured row is findable by the
            # words somebody actually asked. Falling back to the answer's first
            # line would title it with whatever the human happened to type
            # first, which is not what the next asker will search for.
            title=title or existing.question or f"Answer to {escalation_id}",
            body_md=text,
            identity_ids=identity_ids,
            actor_id=actor,
            unmatched_uris=unmatched,
        )

        payload = {
            "escalation_id": result.escalation_id,
            "item_id": result.item_id,
            "revision_id": result.revision_id,
            "provenance_id": result.provenance_id,
            "binding_ids": list(result.binding_ids),
            "unmatched_uris": list(result.unmatched_uris),
        }
        lines = [
            f"Captured {result.revision_id} as confirmed knowledge.",
            f"  item:       {result.item_id}",
            f"  bindings:   {len(result.binding_ids)}",
            f"  escalation: {result.escalation_id} -> answered",
        ]
        if result.unmatched_uris:
            lines.append("  no identity in scope matches: " + ", ".join(result.unmatched_uris))
        lines.append("The next `adopt ask` on this question answers KNOWN, citing it.")
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    typer.echo("\n".join(lines))


class _StoreAdapter:
    """Realizes `adopt_ask.CaptureStore` over one open store handle.

    Every facade below is built from the same `SqliteStoreHandle`, which caches
    them over one connection -- so `transaction()` really does enclose all four
    writes. That is not incidental: `capture_answer` promises atomicity, and the
    promise is only true because these are not four independent connections.
    """

    def __init__(self, handle: CaptureStoreView) -> None:
        self._handle = handle

    def transaction(self) -> AbstractContextManager[None]:
        return self._handle.backend.transaction()

    def create_item(
        self,
        *,
        scope: Scope,
        kind: str,
        title: str,
        body_md: str,
        authority_class: str,
        verification: str,
        actor_id: str | None,
    ) -> tuple[str, str]:
        # The three casts are the manifest's `Literal` enums meeting `adopt_ask`'s
        # plain `str` signature. `adopt_ask` types them as `str` deliberately: a
        # `Literal` there would make the port carry the manifest's shape, and a
        # Postgres realization would then be typed against SQLite's generated
        # model. The values themselves are `adopt_ask` constants, so the cast can
        # only widen a name this repository already generated.
        return self._handle.items().record(
            scope=scope,
            kind=cast("ItemKind", kind),
            title=title,
            body_md=body_md,
            authority_class=cast("AuthorityClass", authority_class),
            verification=cast("Verification", verification),
            actor_id=actor_id,
        )

    def record_provenance(self, *, revision_id: str, source_type: str, source_ref: str) -> str:
        return self._handle.items().record_provenance(
            revision_id=revision_id,
            source_type=cast("SourceType", source_type),
            source_ref=source_ref,
        )

    def bind(self, *, item_id: str, identity_id: str, is_load_bearing: bool) -> str:
        binding_id, _revision_id = self._handle.bindings().create(
            item_id=item_id, identity_id=identity_id, is_load_bearing=is_load_bearing
        )
        return binding_id

    def answer_escalation(self, *, escalation_id: str, candidate_revision_id: str) -> None:
        self._handle.governance().answer_escalation(
            escalation_id=escalation_id, candidate_revision_id=candidate_revision_id
        )
