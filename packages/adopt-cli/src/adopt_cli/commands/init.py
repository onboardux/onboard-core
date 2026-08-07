"""`adopt init` -- contracts §14.

The one command that takes an FDE from "here is a client repository" to "here is
a store with a scope, an archetype and an honest boundary", offline, in one step.

**Scope is given, never inferred** -- the argument CR-32 already made for
`adopt identity build`. Slugs are permanent (CR-05) and a URI built from a
guessed scope is not a wrong answer but a *different referent*, which nothing
downstream can detect. There is no scope key in `03` §3's config registry for the
same reason.

**Every scope level is idempotent on its slug.** Re-running `init` reuses a firm,
engagement, system or environment that already exists rather than failing -- a
slug is never reissued (CR-05), so resolving one is the correct reading of "it is
already there". What is *not* reused is the boundary: re-running appends a new
boundary row, because the answers may have changed and a boundary is a
declaration with a date on it.

**Exit codes carry the finding** (contracts §14 gives `0, 2, 3, 4`):

* `2` -- detection was ambiguous. **Nothing is written**; `04` §4 forbids a guess.
* `3` -- `T0`. The store, the scope and the boundary are recorded and `init` then
  **refuses to proceed to capability planning** (PRD F10.6). A policy refusal,
  because proceeding is precisely what is being declined.
* `4` -- degraded with findings: an `ai` system below its `T3` floor. The setup
  is usable and something needs a human.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, get_args

import typer

from adopt_cli.commands.boundary import answers_from_file
from adopt_cli.json_out import emit
from adopt_cli.store_option import configured_store_path, open_or_create_store
from adopt_detect import declare_boundary, negotiate, parse_answers, render_markdown
from adopt_detect import detect as run_detect
from adopt_detect.detect import DISAMBIGUATION_FLAG
from adopt_model._enums import Archetype
from adopt_obs import AdoptError, ErrorCode, ExitCode
from adopt_scope import Scope, ScopeFacade, ScopePath

__all__ = ["init"]

PathArgument = Annotated[Path, typer.Argument(help="The tree to classify. Read, never executed.")]
ScopeOption = Annotated[
    str,
    typer.Option(
        "--scope",
        help="firm/engagement/system/environment, as immutable slugs. All four are "
        "required: a boundary is declared for one environment of one system.",
        show_default=False,
    ),
]
AnswersOption = Annotated[
    Path,
    typer.Option(
        "--answers",
        help="JSON file carrying the three qualification answers.",
        show_default=False,
    ),
]
ArchetypeOption = Annotated[
    str | None,
    typer.Option(
        "--archetype",
        help="Accept an archetype explicitly, when detection was ambiguous. This is the "
        "human decision PRD §8 requires before an archetype is written -- there is no "
        "confidence exemption and no flag that lets a proposal write itself.",
    ),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
StatementOption = Annotated[
    Path | None,
    typer.Option("--write-statement", help="Also write the human-readable boundary statement."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


@dataclass(frozen=True, slots=True)
class _FullScope:
    """Four slugs, all present. `ScopePath` allows a prefix; `init` does not."""

    firm: str
    engagement: str
    system: str
    environment: str

    def prefix(self, depth: int) -> ScopePath:
        """The path naming the first `depth` levels."""
        levels: tuple[str | None, ...] = (self.firm, self.engagement, self.system, self.environment)
        padded = tuple(level if index < depth else None for index, level in enumerate(levels))
        return ScopePath(
            firm=self.firm,
            engagement=padded[1],
            system=padded[2],
            # const-sync: ok -- positional indexes into the four scope levels.
            environment=padded[3],
        )


def _require_full_scope(scope: str) -> _FullScope:
    parsed = ScopePath.parse(scope)
    if parsed.engagement is None or parsed.system is None or parsed.environment is None:
        raise AdoptError(
            ErrorCode.SCOPE_SLUG_INVALID,
            message=f"--scope {scope!r} does not name all four levels",
            hint="A boundary is declared for one environment of one system, and every "
            "identity URI carries an environment (contracts §4). Pass "
            "`firm/engagement/system/environment`.",
        )
    return _FullScope(parsed.firm, parsed.engagement, parsed.system, parsed.environment)


def _resolves(scopes: ScopeFacade, path: ScopePath) -> Scope | None:
    """The resolved scope, or `None` when a level in it does not exist yet.

    Existence is tested by resolving and catching, because `ScopeFacade` exposes
    no `find_*`. Widening the facade for a CLI convenience would put a second
    lookup path beside the one the slug rules are enforced on, and the rules --
    validity, immutability, no reissue -- all live on the creation path.
    """
    try:
        return scopes.resolve(path)
    except AdoptError as error:
        if error.code is ErrorCode.SCOPE_SLUG_INVALID:
            return None
        raise


def _ensure_scope(scopes: ScopeFacade, wanted: _FullScope, archetype: Archetype | None) -> Scope:
    """Create whichever levels are missing, reuse the rest, return the full scope."""
    if _resolves(scopes, wanted.prefix(1)) is None:
        scopes.create_firm(slug=wanted.firm, name=wanted.firm)
    firm_scope = scopes.resolve(wanted.prefix(1))

    if _resolves(scopes, wanted.prefix(2)) is None:
        scopes.create_engagement(
            firm_id=firm_scope.firm.id, slug=wanted.engagement, name=wanted.engagement
        )
    engagement_scope = scopes.resolve(wanted.prefix(2))
    assert engagement_scope.engagement is not None  # noqa: S101 -- just resolved at depth 2

    # const-sync: ok -- scope depth 3 (firm/engagement/system), not SCHEMA_VERSION.
    if _resolves(scopes, wanted.prefix(3)) is None:
        scopes.create_system(
            engagement_id=engagement_scope.engagement.id,
            slug=wanted.system,
            name=wanted.system,
            archetype=archetype,
        )
    # const-sync: ok -- scope depth 3 (firm/engagement/system), not SCHEMA_VERSION.
    system_scope = scopes.resolve(wanted.prefix(3))
    assert system_scope.system is not None  # noqa: S101 -- just resolved at depth 3

    if _resolves(scopes, wanted.prefix(4)) is None:
        scopes.create_environment(
            system_id=system_scope.system.id, slug=wanted.environment, name=wanted.environment
        )
    return scopes.resolve(wanted.prefix(4))


def _accepted_archetype(named: str | None) -> Archetype | None:
    """Validate an operator-supplied archetype against the canonical vocabulary.

    Checked against the **generated** `Archetype` literal rather than a list held
    here, so the vocabulary has one home (`02` §2.1) and adding an archetype to the
    manifest cannot leave this command rejecting it.

    A typo is refused rather than stored: `system.archetype` is what decides which
    extractors a downstream item runs, so `--archetype wbe` writing an unknown
    value would be a *different referent* rather than a slightly wrong one -- the
    argument CR-32 made for scope, applied to the one other field an operator
    types by hand.
    """
    if named is None:
        return None
    canonical = get_args(Archetype)
    if named not in canonical:
        raise AdoptError(
            ErrorCode.DETECT_AMBIGUOUS,
            message=f"--archetype {named!r} is not one of {', '.join(canonical)}",
            hint="`02` §2.1 fixes the archetype vocabulary, and a value outside it "
            "would decide which extractors run for this system. Nothing was created.",
        )
    accepted: Archetype = named  # type: ignore[assignment]  # membership just checked
    return accepted


def init(
    path: PathArgument = Path(),
    scope: ScopeOption = "",
    answers: AnswersOption = Path("answers.json"),
    archetype: ArchetypeOption = None,
    store: StoreOption = None,
    write_statement: StatementOption = None,
    json_output: JsonOption = False,
) -> None:
    """Create a store, resolve a scope, detect the archetype and declare the boundary."""
    wanted = _require_full_scope(scope)
    decision = negotiate(parse_answers(answers_from_file(answers)))

    # Detection runs **before** the store is created, so an ambiguous tree leaves
    # nothing behind. A store created and then abandoned is exactly the empty
    # database beside the real one that `store_option` already refuses to make.
    result = run_detect(path)
    accepted = _accepted_archetype(archetype)
    if result.ambiguous and accepted is None:
        raise AdoptError(
            ErrorCode.DETECT_AMBIGUOUS,
            message=f"confidence {result.confidence} is below the threshold; nothing was created",
            hint=f"Run `adopt detect {path}` for the ranked scores and the rules that "
            f"fired. Narrow the path to one system, or set {DISAMBIGUATION_FLAG}=1 for a "
            f"proposal and re-run with `--archetype <a>` to accept it. "
            f"Detection does not guess: a wrong archetype is a different set of "
            f"extractors, not a slightly wrong answer.",
        )
    # **The operator's value wins, and that is the whole human-accept step.** PRD §8
    # requires human approval for writing an archetype with no confidence
    # exemption, so a named `--archetype` overrules detection rather than merely
    # unblocking it -- an operator who has read a proposal, or who simply knows the
    # system, is the authority the matrix names. `archetype_source` records which
    # of the two the row came from, because "who decided this" is exactly the
    # question an audit asks of a system that can propose.
    effective = accepted if accepted is not None else result.archetype
    archetype_source = "operator" if accepted is not None else "detected"

    store_path = configured_store_path(store)
    with open_or_create_store(store) as handle:
        resolved = _ensure_scope(handle.scope(), wanted, effective)
        view = declare_boundary(
            handle.boundary(), scope=resolved, decision=decision, archetype=effective
        )
        payload: dict[str, Any] = {
            "store_path": str(store_path),
            "scope": {
                "firm": wanted.firm,
                "engagement": wanted.engagement,
                "system": wanted.system,
                "environment": wanted.environment,
            },
            "archetype": effective,
            "archetype_source": archetype_source,
            "tier": view.tier,
            "schema_version": handle.schema_version,
            "boundary_id": view.boundary_id,
            "unavailable_capabilities": list(view.unavailable_capabilities),
        }

    if write_statement is not None:
        write_statement.write_text(render_markdown(view), encoding="utf-8", newline="\n")
        payload["statement_path"] = str(write_statement)

    emit(payload, as_json=json_output, title="adopt init")

    if view.decline_recommended:
        raise AdoptError(
            ErrorCode.TIER_DECLINE_RECOMMENDED,
            message="T0: no artifact access, so no claim this platform makes could be "
            "supported by evidence from this system",
            hint="The store, the scope and the boundary are recorded so the decision is "
            "auditable, and setup stops here. Re-run with different answers if "
            "artifact access is arranged.",
        )
    if view.archetype_floor_violated:
        raise typer.Exit(ExitCode.DEGRADED_WITH_FINDINGS)
