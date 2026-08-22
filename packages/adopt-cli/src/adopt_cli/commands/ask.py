"""Build 3's verb: `adopt ask`.

**Every `adopt_ask` import happens inside the command body**, exactly as
`map_command` and `knowledge` do it and for the same reason: v6.1 §2.1 requires
new verbs to register lazily so `CLI_COLD_START_MS` holds. `adopt version` must
not pay for an FTS index opener it never uses.

**This module is the composition root for the serve path**, and the order below
is the contract rather than an implementation detail:

    refresh the derived index -> retrieve candidates -> resolve freshness for
    every one -> compose -> guard against the boundary -> print

`compose` accepts only candidates already paired with a resolution, so the third
step cannot be skipped by editing this file: the worst a future edit can do is
fail to type-check. That is critical semantic invariant #5, and
`adopt_ask.branch` records why it is a type rather than a check.

**All three branches exit 0** (plan decision D4). An honest UNKNOWN is a correct
answer, not a failure -- the `MAP_EXPECTED_IDENTITY_MISSING` precedent, where
turning "your map is incomplete" into "the command failed" was rejected for the
same reason. Scripts branch on the `--json` payload's `branch` field. Typed
errors keep their existing nonzero mapping, so `ASK_OUTSIDE_BOUNDARY` exits 3
with every other policy refusal and stays distinguishable from an UNKNOWN.

**The store is opened writable** because answering may rebuild the retrieval
index, which lives in the annex beside it. Nothing here writes canon.
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import configured_search, open_configured_store

__all__ = ["ask"]

QuestionArgument = Annotated[str, typer.Argument(help="The question, in plain language.")]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]
ReindexOption = Annotated[
    bool,
    typer.Option(
        "--reindex",
        help="Rebuild the retrieval index before answering, even if it looks current.",
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def ask(
    question: QuestionArgument,
    scope: ScopeOption = None,
    store: StoreOption = None,
    reindex: ReindexOption = False,
    json_output: JsonOption = False,
) -> None:
    """Answer from the store: KNOWN with citations, STALE with the cause, or UNKNOWN."""
    from adopt_ask import compose, guard, json_payload, render, retrieve
    from adopt_ask.branch import Resolved

    from adopt_cli.commands._map_support import resolve_scope
    from adopt_detect import BoundaryView
    from adopt_freshness import resolve_freshness
    from adopt_obs import SystemClock

    handle = open_configured_store(store, read_only=False)
    try:
        resolved_scope = resolve_scope(handle, scope)
        clock = handle.clock if handle.clock is not None else SystemClock()

        with configured_search(handle, clock=clock) as search:
            search.refresh(force=reindex)
            candidates = retrieve(search, question)
            verified = search.verified_in_store(
                [candidate.passage.revision_id for candidate in candidates]
            )

        freshness_records = handle.freshness_records()
        resolved = tuple(
            Resolved(
                candidate=candidate,
                freshness=resolve_freshness(
                    freshness_records, candidate.passage.item_id, clock=clock
                ),
            )
            for candidate in candidates
        )

        answer = compose(resolved, verified, question)

        system = resolved_scope.system
        environment = resolved_scope.environment
        row = (
            None
            if system is None
            else handle.boundary().current(
                system_id=system.id,
                environment_id=None if environment is None else environment.id,
            )
        )
        guard(
            answer,
            None if row is None else BoundaryView.of(row, archetype=None),
            scope=resolved_scope,
            occurred_at=clock.now(),
        )

        payload = dict(json_payload(answer))
        human = render(answer)
    finally:
        handle.close()

    if json_output:
        emit(payload, as_json=True)
        return
    typer.echo(human)
