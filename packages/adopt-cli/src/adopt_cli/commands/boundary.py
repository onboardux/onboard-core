"""`adopt boundary` -- contracts §14.

Negotiate a tier from the three qualification answers (CR-38) and report the
boundary it implies.

**Persisting is optional, and that is the workflow rather than a convenience.**
With `--scope` and a store, the boundary row is written and its id reported. With
neither, the negotiation is computed and reported and nothing is written --
because PRD F10.6's `T0` case is exactly the one an FDE reaches *before* there is
a firm, an engagement or a store: they are deciding whether to take the
engagement at all. Requiring a store in order to be told "decline" inverts the
order the product is used in.

**Exit `4`, not `0`, whenever there is a finding** -- a `T0` decline or an `ai`
system below its floor. Contracts §14 gives this command exits `0` and `4`, and
`4` is *degraded success with findings*: the boundary is real and reportable, and
something about it needs a human.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store
from adopt_detect import (
    ARCHETYPES,
    DEFAULT_OUTBOUND_CATEGORIES,
    BoundaryView,
    declare_boundary,
    negotiate,
    parse_answers,
    render_markdown,
    unavailable_capabilities,
    violates_archetype_floor,
)
from adopt_detect.negotiate import TierDecision
from adopt_detect.render import render_json
from adopt_model._enums import Archetype
from adopt_obs import AdoptError, ErrorCode, ExitCode, now
from adopt_scope import ScopePath

__all__ = ["answers_from_file", "boundary", "build_payload"]

AnswersOption = Annotated[
    Path,
    typer.Option(
        "--answers",
        help="JSON file carrying the three qualification answers "
        "(artifact_access, deploy_signal, safe_interaction).",
        show_default=False,
    ),
]
ScopeOption = Annotated[
    str | None,
    typer.Option(
        "--scope",
        help="firm/engagement/system[/environment]. Given, the boundary is written to "
        "the store; omitted, it is computed and reported without writing.",
    ),
]
ArchetypeOption = Annotated[
    str | None,
    typer.Option("--archetype", help="The detected archetype, for the ai-below-T3 floor check."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
MarkdownOption = Annotated[
    Path | None,
    typer.Option(
        "--write-statement",
        help="Also write the human-readable statement here. Same row, same facts.",
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def answers_from_file(path: Path) -> dict[str, Any]:
    """Read and parse the answers document.

    Raises:
        AdoptError: ``TIER_ANSWERS_INVALID`` when the file is absent or is not
            JSON. Both are the caller's mistake and neither is defaulted -- an
            unreadable answers file must never resolve to a tier.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AdoptError(
            ErrorCode.TIER_ANSWERS_INVALID,
            message=f"cannot read the answers file {path}",
            hint="Point --answers at a JSON file carrying the three answers.",
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise AdoptError(
            ErrorCode.TIER_ANSWERS_INVALID,
            message=f"{path} is not valid JSON: {error}",
            hint="The file is a JSON object with three boolean keys.",
        ) from error
    if not isinstance(document, dict):
        raise AdoptError(
            ErrorCode.TIER_ANSWERS_INVALID,
            message=f"{path} is a {type(document).__name__}, not a JSON object",
            hint="The file is a JSON object with three boolean keys.",
        )
    return document


def build_payload(view: BoundaryView) -> dict[str, Any]:
    """Contracts §14's `adopt boundary` shape, from the one rendering function."""
    return render_json(view)


def _unpersisted_view(decision: TierDecision, archetype: Archetype | None) -> BoundaryView:
    """The view for a negotiation that was not written.

    `boundary_id` is empty rather than invented. A synthetic id here would be a
    string that looks like a stored boundary and resolves to nothing, which is
    worse than an obviously absent one.
    """
    return BoundaryView(
        boundary_id="",
        system_id="",
        environment_id=None,
        tier=decision.tier,
        archetype=archetype,
        knowledge_plane_location="customer",
        control_plane_location="vendor",
        permitted_outbound_categories=DEFAULT_OUTBOUND_CATEGORIES,
        unavailable_capabilities=unavailable_capabilities(decision.tier),
        contractual_approval_ref=None,
        declared_at=now(),
        decline_recommended=decision.decline_recommended,
        archetype_floor_violated=violates_archetype_floor(archetype, decision.tier),
    )


def boundary(
    answers: AnswersOption,
    scope: ScopeOption = None,
    archetype: ArchetypeOption = None,
    store: StoreOption = None,
    write_statement: MarkdownOption = None,
    json_output: JsonOption = False,
) -> None:
    """Negotiate the observability boundary and report what it permits."""
    decision = negotiate(parse_answers(answers_from_file(answers)))
    typed_archetype: Archetype | None = None
    if archetype is not None:
        typed_archetype = _require_archetype(archetype)

    if scope is None:
        view = _unpersisted_view(decision, typed_archetype)
    else:
        with open_configured_store(store, read_only=False) as handle:
            resolved = handle.scope().resolve(ScopePath.parse(scope))
            view = declare_boundary(
                handle.boundary(),
                scope=resolved,
                decision=decision,
                archetype=typed_archetype,
            )

    payload = build_payload(view)
    payload["persisted"] = bool(view.boundary_id)
    if write_statement is not None:
        write_statement.write_text(render_markdown(view), encoding="utf-8", newline="\n")
        payload["statement_path"] = str(write_statement)

    emit(payload, as_json=json_output, title="adopt boundary")
    if view.decline_recommended or view.archetype_floor_violated:
        raise typer.Exit(ExitCode.DEGRADED_WITH_FINDINGS)


def _require_archetype(value: str) -> Archetype:
    """`value` as a declared archetype, or a usage refusal naming the set."""
    for archetype in ARCHETYPES:
        if value == archetype:
            return archetype
    raise AdoptError(
        ErrorCode.ADOPT_CONFIG_UNRESOLVED,
        message=f"{value!r} is not a declared archetype",
        hint=f"Archetypes are exactly {list(ARCHETYPES)} (contracts §2.1). Detection "
        "returns one of these; nothing else can be scored against the tier floors.",
    )
