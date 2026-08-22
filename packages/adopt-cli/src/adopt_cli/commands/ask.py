"""Build 3's verb: `adopt ask`.

**Every `adopt_ask` import happens inside the command body**, exactly as
`map_command` and `knowledge` do it and for the same reason: v6.1 §2.1 requires
new verbs to register lazily so `CLI_COLD_START_MS` holds. `adopt version` must
not pay for an FTS index opener it never uses.

**The pipeline itself lives in `_ask_support.answer_question`**, because v6.1
says `adopt serve` exposes *the same* paths -- and two copies of it would be two
places the freshness check could be skipped and two answers to one question
depending on which door the asker came through. This module is the terminal:
options in, store open, answer printed.

**All three branches exit 0** (plan decision D4). An honest UNKNOWN is a correct
answer, not a failure -- the `MAP_EXPECTED_IDENTITY_MISSING` precedent, where
turning "your map is incomplete" into "the command failed" was rejected for the
same reason. Scripts branch on the `--json` payload's `branch` field. Typed
errors keep their existing nonzero mapping, so `ASK_OUTSIDE_BOUNDARY` exits 3
with every other policy refusal and stays distinguishable from an UNKNOWN.

**The store is opened writable** because answering may rebuild the retrieval
index, which lives in the annex beside it. Answering alone still writes no
canon; `--escalate` does, and it is the only thing here that does.

**Consent is decided before anything is stored, and never inside a prompt
helper.** `adopt_ask.escalate.consented` takes the flag, whether a human is
actually at the terminal, and how to ask -- and returns `False` for every path
where nobody chose. `--json` is non-interactive by definition, so escalation
there is flag-only (v6.1 F2, and the plan's own note that CI must never meet a
prompt).
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store

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
EscalateOption = Annotated[
    bool,
    typer.Option(
        "--escalate",
        help="Record an unanswered or stale question as open, storing its text.",
    ),
]


def ask(
    question: QuestionArgument,
    scope: ScopeOption = None,
    store: StoreOption = None,
    reindex: ReindexOption = False,
    escalate_flag: EscalateOption = False,
    json_output: JsonOption = False,
) -> None:
    """Answer from the store: KNOWN with citations, STALE with the cause, or UNKNOWN."""
    from adopt_cli.commands._ask_support import answer_question

    handle = open_configured_store(store, read_only=False)
    try:
        outcome = answer_question(
            handle,
            question,
            scope=scope,
            reindex=reindex,
            escalate_flag=escalate_flag,
            # `--json` is non-interactive by definition: a machine-readable run
            # that stopped to ask a question would hang whatever is reading it.
            interactive=not json_output and _at_a_terminal(),
            confirm=_confirm,
        )
    finally:
        handle.close()

    if json_output:
        emit(outcome.payload, as_json=True)
        return
    typer.echo(outcome.human)


def _at_a_terminal() -> bool:
    """Whether a human can actually see and answer a prompt.

    Both streams, not just stdin: a run whose output is piped to a file has
    nobody reading the question, so prompting would block a pipeline forever on
    a sentence no one will ever see.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm(prompt: str) -> bool:
    """Ask, defaulting to **no**.

    The default is the F2 posture in one keyword: someone who hits Enter without
    reading has not consented to their question being stored, and the cost of
    the wrong default here is a stored transcript of what an FDE was unsure
    about on a client engagement.
    """
    return typer.confirm(prompt, default=False)
