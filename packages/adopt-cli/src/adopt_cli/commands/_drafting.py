"""One drafting pass, shared by `adopt pack --draft-missing` and `adopt draft`.

Both verbs do the same thing to different targets: build the facts, open a
`Runner`, call `run_drafting`, report what landed. Two copies of that would be
two places the offline refusal could be handled differently and two places a
future budget change would have to be made -- the same argument
`_ask_support.answer_question` records for `adopt ask` and `adopt serve`.

**No adapter is not an error here.** `adapter_settings()` returning no adapter
means the operator never configured one, which is the default posture of this
product; the caller renders its pack regardless and prints the sentence that
says how to enable drafting. Only a *configured* adapter that then fails is
worth a non-zero exit, and the seam raises for that on its own.
"""

from typing import Any

__all__ = ["DRAFTING_UNAVAILABLE", "draft_gaps", "draft_one_identity"]

#: What `--draft-missing` reports when nothing could call a model. Not a failure
#: and not a warning: R3 makes the no-model mode a complete product, so this is
#: a statement about configuration printed beside a pack that is already whole.
DRAFTING_UNAVAILABLE: str = (
    "no model adapter is configured, so nothing was drafted -- the pack below is complete "
    "without one. Set ADOPT_ADAPTER (and ADOPT_MODEL) and pass --allow-network to draft."
)


def draft_gaps(
    handle: Any,
    *,
    scope: Any,
    audience: str,
    system_id: str,
    environment_id: str | None,
) -> dict[str, Any]:
    """Draft the uncovered identities, ranked, capped. Returns the `--json` slice.

    The targets are the gap list the pack itself will render, minus anything a
    human has **waived**: a waiver is somebody deciding this gap will not be
    closed, and spending a model call on it afterwards would be the tool
    ignoring the decision it asked for.
    """
    from adopt_knowledge import rank_gaps

    from adopt_cli.commands import _draft_support as support
    from adopt_coverage import recompute_coverage

    coverage = recompute_coverage(handle.coverage_records(), system_id, environment_id)
    dispositions = handle.governance().gap_dispositions()
    ranked = [
        gap
        for gap in rank_gaps(coverage.identities)
        if getattr(dispositions.get(gap.gap_key), "status", None) != "waived"
    ]
    targets = support.targets_for(handle, ranked)
    return _run(handle, targets, scope=scope, audience=audience)


def draft_one_identity(
    handle: Any,
    *,
    scope: Any,
    audience: str,
    identity: Any,
) -> dict[str, Any]:
    """Draft exactly the identity an operator named. Returns the `--json` slice."""
    from adopt_cli.commands import _draft_support as support

    return _run(handle, [support.target_for(handle, identity)], scope=scope, audience=audience)


def _run(
    handle: Any,
    targets: list[Any],
    *,
    scope: Any,
    audience: str,
) -> dict[str, Any]:
    """Open the seam and run the pass. The one place a drafting `Runner` is built."""
    from adopt_knowledge import run_drafting

    from adopt_agent import Runner
    from adopt_cli.commands import _draft_support as support
    from adopt_cli.commands.agent import adapter_settings, prompts_root
    from adopt_cli.store_option import configured_annex

    offline, adapter_id, model, endpoint = adapter_settings()
    if not adapter_id:
        return {"available": False, "note": DRAFTING_UNAVAILABLE, "drafted": 0, "targets": 0}

    with configured_annex() as annex:
        runner = Runner(
            annex=annex,
            scope_ref=audience,
            skills_root=prompts_root(),
            offline=offline,
            adapter_id=adapter_id,
            model=model,
            endpoint=endpoint,
        )
        report = run_drafting(
            targets,
            runner=runner,
            store=support.DraftStoreAdapter(handle),
            scope=scope,
            audience=audience,
            already_drafted=support.already_drafted(handle),
        )

    return {
        "available": True,
        "targets": len(targets),
        "drafted": len(report.landed),
        "review_batch": report.review_batch_id,
        # Every target and what became of it. A run that drafted nothing and a
        # run that was never asked to must not look the same to whoever reads
        # this, which is why the discards are listed rather than counted.
        "outcomes": [
            {
                "uri": outcome.uri,
                "revision": outcome.revision_id,
                "reason": outcome.reason,
            }
            for outcome in report.outcomes
        ],
    }
