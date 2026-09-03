"""Build 4's second verb: `adopt draft <identity-uri>` -- draft one section.

The single-target door onto the same pass `adopt pack --draft-missing` runs in
bulk. It exists because the bulk pass is ranked and capped: an FDE who knows
which endpoint needs writing up should not have to wait for it to come up the
queue, and should not have to draft nineteen other things to reach it.

**A draft is never confirmed by this command**, whichever door it came through.
It lands `unverified`, bound to the identity, queued in `adopt review`, and it
renders under an UNVERIFIED banner until a human confirms it. There is no flag
here that skips that, which is v6.1 D11 as a missing parameter rather than a
paragraph.

**Every import is inside the body**, as every verb since Build 1 does it: v6.1
§2.1 keeps new verbs off the startup path so `CLI_COLD_START_MS` holds.
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

__all__ = ["draft"]

UriArgument = Annotated[
    str, typer.Argument(help="The canonical identity URI to draft a section about.")
]
AudienceOption = Annotated[
    str,
    typer.Option("--audience", help="Which audience the drafted section is written for."),
]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Path to the store.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def draft(
    uri: UriArgument,
    audience: AudienceOption = "technical",
    scope: ScopeOption = None,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Draft one handover section from what the store knows about an identity.

    Grounded strictly on store facts: the identity's attributes, where it was
    observed, and any knowledge already bound to it. A draft that cites none of
    them is discarded and nothing is written. What survives lands as
    **unverified** knowledge for `adopt review`.
    """
    from adopt_cli.commands._drafting import draft_one_identity
    from adopt_cli.commands._knowledge_support import resolve_identity
    from adopt_cli.commands._map_support import resolve_scope
    from adopt_obs import AdoptError, ErrorCode

    handle = open_configured_store(store, read_only=False, verb="draft")
    try:
        resolved = resolve_scope(handle, scope)
        identity = resolve_identity(handle, uri)
        if identity is None:
            # The same code and the same sentence `adopt bind` uses for the same
            # mistake. A second code for "drafting could not find it" would be a
            # second name for one situation an operator fixes the same way.
            raise AdoptError(
                ErrorCode.BIND_TARGET_NOT_FOUND,
                message=f"no identity in this store has the URI {uri!r}",
                hint="Run `adopt map` first, or check the URI with `adopt identity parse`. "
                "A moved identity still resolves through its alias.",
            )

        payload = draft_one_identity(handle, scope=resolved, audience=audience, identity=identity)
    finally:
        handle.close()

    emit(payload, as_json=json_output, title="adopt draft")
