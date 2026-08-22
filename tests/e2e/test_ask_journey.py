"""The Build 3 demo journey, run verbatim, as the Build Definition of Done.

v6.1 §6 Build 3's demo block, line for line:

    adopt ask "why does the approval step exist on refunds?"   -> KNOWN, cited
    adopt ask "how do I rotate the API key?"                    -> UNKNOWN
    adopt ask ... --escalate                                    -> question stored
    adopt answer <id> --text "..."                              -> confirmed knowledge
    adopt ask "how do I rotate the API key?"                    -> KNOWN, citing it
    <map rerun after the identity a doc depends on moved>
    adopt ask "why does the approval step exist on refunds?"    -> STALE, cause named

**A fixture repository, not a pinned clone.** The plan's own budget note says one
repository, and this journey needs something the map, ingest and *move* stories
all agree on: a two-file tree we own, copied into a git checkout per test, whose
`Dockerfile` can be moved to produce a real identity move. A pinned upstream
clone would make the STALE leg depend on somebody else's directory layout, and
the map/ingest journeys already exercise real corpora against the same commands.

**The documents are authored here rather than found.** This is a test of the
*branch*, and a branch test needs a corpus where the right answer is known: one
document that answers the refund question and none that answers the key-rotation
one. Reusing a real README would make UNKNOWN depend on what somebody upstream
happened not to write about, which is a fact about their repository rather than
about our code -- and S3.1 already found that retrieval breadth is exactly where
UNKNOWN silently becomes unreachable.

**Every step goes through the CLI as a subprocess**, the same entry-point module
the release binary compiles (CR-56), and every assertion reads the store file
directly rather than through `adopt_store` -- so one bug cannot both write the
wrong row and vouch for it.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from adopt_obs import ExitCode

ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "web" / "fastapi_orders"
SCOPE = "northwind/acme-erp/orders-api/prod"
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}

REFUND_QUESTION = "why does the approval step exist on refunds?"
KEY_QUESTION = "how do I rotate the API key?"

#: The identity the document is about, addressed canonically. `Dockerfile` is a
#: path-keyed `metadata_component`, so moving the file is a genuine identity
#: move -- the one F3 staleness signal that exists pre-B6.
DOCKERFILE_URI = f"onboard-v1://{SCOPE}/metadata_component/file/Dockerfile"

#: The one document the corpus holds, naming its identity by **canonical URI**.
#:
#: The first draft wrote ``the image built from `Dockerfile` `` and ingest bound
#: nothing -- correctly. A bare `Dockerfile` has no separator and no suffix, so
#: `adopt_knowledge.matchers` reads it as a *name*, and a name match is a review
#: suggestion and never a binding (Build 2's H2). That is the rule working, and
#: the fix is to make the document say what it means: a URI is the one thing an
#: author can write that unambiguously addresses a referent, which is why the URI
#: tier auto-binds and the name tier does not.
REFUND_DOC = f"""# Refund approvals

The approval step exists on refunds because chargebacks were disputed twice in
the first quarter and the acquirer required a documented human decision before
any money moves back.

The container image `{DOCKERFILE_URI}` carries the approval worker, so a change
to that image is a change to how approvals run.
"""


def _run(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One CLI invocation, through the module the release binary compiles."""
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        parsed: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover -- only on a broken envelope
        pytest.fail(f"stdout was not the JSON envelope:\n{completed.stdout[:2000]}")
    return parsed


def _sql(store: Path, query: str, *args: object) -> list[tuple[Any, ...]]:
    """Straight at the file. Asking `adopt_store` what `adopt_store` wrote would
    let one bug hide itself, which is `test_knowledge_journey`'s rule."""
    with sqlite3.connect(store) as connection:
        return list(connection.execute(query, args).fetchall())


def _git(*argv: str, cwd: Path) -> None:
    done = subprocess.run(["git", *argv], cwd=str(cwd), check=False, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.fixture
def journey(tmp_path: Path) -> dict[str, Any]:
    """`init` -> `map` -> `ingest`, once. The demo's own starting state.

    One fixture for the whole prefix rather than one per test, because the demo
    **is** a sequence: "the next ask serves it as KNOWN" has no meaning except
    relative to the ask before it.
    """
    if not shutil.which("git"):  # pragma: no cover -- every CI runner ships git
        pytest.skip("git is not on PATH, and the STALE leg needs a real move")

    checkout = tmp_path / "orders-api"
    shutil.copytree(FIXTURE, checkout)
    (checkout / "docs").mkdir()
    (checkout / "docs" / "refunds.md").write_text(REFUND_DOC, encoding="utf-8")
    _git("init", "-q", cwd=checkout)
    _git("add", "-A", cwd=checkout)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "initial", cwd=checkout)

    store = tmp_path / "store.db"
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")

    init = _run(
        "init",
        ".",
        "--scope",
        SCOPE,
        "--answers",
        str(answers),
        "--archetype",
        "web",
        "--store",
        str(store),
        "--json",
        cwd=checkout,
    )
    assert init.returncode == ExitCode.SUCCESS, init.stderr
    mapped = _run("map", ".", "--store", str(store), "--json", cwd=checkout)
    assert mapped.returncode == ExitCode.SUCCESS, mapped.stderr
    ingested = _run("ingest", "docs/refunds.md", "--store", str(store), "--json", cwd=checkout)
    assert ingested.returncode == ExitCode.SUCCESS, ingested.stderr

    return {"checkout": checkout, "store": store, "tmp": tmp_path}


def _ask(journey: dict[str, Any], question: str, *flags: str) -> dict[str, Any]:
    completed = _run(
        "ask",
        question,
        "--store",
        str(journey["store"]),
        *flags,
        "--json",
        cwd=journey["checkout"],
    )
    assert completed.returncode == ExitCode.SUCCESS, (
        f"every branch exits 0 (plan D4); got {completed.returncode}\n{completed.stderr}"
    )
    return _payload(completed)


@pytest.mark.e2e
def test_demo_line_1_a_known_answer_cites_revisions_uris_and_provenance(
    journey: dict[str, Any],
) -> None:
    """*Fails when* a question the corpus answers stops answering KNOWN, or
    answers it uncited. *Matters because* this is the product: an FDE asks the
    store instead of the departed build team, and an answer they cannot trace to
    a revision is one they have to verify by hand -- which is the out-of-band
    conversation this build exists to delete. *No other instrument catches it
    because* every unit test here composes an `Answer` from candidates a test
    constructed; only the journey shows that a real `ingest` produces knowledge a
    real `ask` can find.
    """
    payload = _ask(journey, REFUND_QUESTION)

    assert payload["branch"] == "known", payload
    assert payload["citations"], "a KNOWN answer with no citations is the guess this build bans"
    citation = payload["citations"][0]
    assert citation["revision_id"].startswith("krev_")
    assert "chargebacks" in citation["body_md"]

    # The cited revision is real, verified, and carries provenance -- read from
    # the file rather than from the payload that claims it.
    rows = _sql(
        journey["store"],
        "SELECT kr.verification, (SELECT COUNT(*) FROM provenance p WHERE p.revision_id = kr.id) "
        "FROM knowledge_revision kr WHERE kr.id = ?",
        citation["revision_id"],
    )
    assert rows and rows[0][0] == "verified"
    assert rows[0][1] >= 1, "a served answer must be traceable to where its claim came from"


@pytest.mark.e2e
def test_demo_line_2_an_unanswerable_question_refuses(journey: dict[str, Any]) -> None:
    """*Fails when* UNKNOWN becomes unreachable. *Matters because* S3.1 found
    exactly this: retrieval OR-s its terms, one shared function word made every
    document a candidate for every question, and a branch that can always find
    something verified always answers KNOWN -- the unqualified guess, arriving
    through retrieval rather than generation. *No other instrument catches it
    because* a two-document unit fixture cannot show it and every unit test
    passed throughout."""
    payload = _ask(journey, KEY_QUESTION)

    assert payload["branch"] == "unknown", payload
    assert payload["citations"] == []


@pytest.mark.e2e
def test_demo_lines_3_to_5_escalate_answer_and_the_next_ask_is_known(
    journey: dict[str, Any],
) -> None:
    """The capture ratchet, end to end -- the heart of S3.2.

    *Fails when* any link breaks: the escalation does not store the question, the
    capture does not land verified knowledge, or the next ask cannot find it.
    *Matters because* this is the loop v6.1 says must be cheaper than answering
    out of band -- if the next asker still gets UNKNOWN, the FDE learns the
    capture was wasted effort and stops. *No other instrument catches it because*
    each unit test proves one link: only the journey proves the chain.
    """
    escalated = _ask(journey, KEY_QUESTION, "--escalate")
    assert escalated["branch"] == "unknown"
    escalation_id = escalated["escalation_id"]
    assert escalation_id.startswith("esc_")

    # F2: the text is stored, because escalating *is* the consent.
    stored = _sql(
        journey["store"],
        "SELECT question, branch, status FROM escalation WHERE id = ?",
        escalation_id,
    )
    assert stored == [(KEY_QUESTION, "ungrounded", "open")]

    captured = _run(
        "answer",
        escalation_id,
        "--text",
        "Rotate the API key in the vault, then restart the orders service so it "
        "rereads the secret. The old key stays valid for one hour.",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert captured.returncode == ExitCode.SUCCESS, captured.stderr
    capture = _payload(captured)

    # One transaction: knowledge, provenance and the stamp, or none of them.
    revision = _sql(
        journey["store"],
        "SELECT verification, authority_class FROM knowledge_revision WHERE id = ?",
        capture["revision_id"],
    )
    assert revision == [("verified", "human_confirmed")], (
        "a captured answer is human-authored and confirmed -- never artifact_observed"
    )
    assert _sql(
        journey["store"],
        "SELECT status, candidate_revision_id FROM escalation WHERE id = ?",
        escalation_id,
    ) == [("answered", capture["revision_id"])]

    reasked = _ask(journey, KEY_QUESTION)

    assert reasked["branch"] == "known", (
        "the whole ratchet: the next asker gets the answer a human just banked"
    )
    assert capture["revision_id"] in {citation["revision_id"] for citation in reasked["citations"]}


@pytest.mark.e2e
def test_demo_line_6_a_map_rerun_after_a_move_serves_stale_with_its_cause(
    journey: dict[str, Any],
) -> None:
    """The F3 leg, on a **real** move rather than a planted freshness row.

    *Fails when* a moved identity stops staling the knowledge bound to it, or
    stales it without naming why. *Matters because* pre-B6 this is the only
    staleness signal the product has (v6.1 F3), and a STALE answer that does not
    say what changed is an answer an FDE cannot act on -- they know it might be
    wrong and not where to look. *No other instrument catches it because* the
    unit tests inject a `freshness_state` directly; only a real `git mv` plus a
    real `adopt map` shows that the move is actually detected and actually
    reaches the binding.

    A **move**, not a deletion: Build 1 decided absence is not death (its plan
    D6) and defers retirement to Build 6, so a removed identity stays `active`
    and stales nothing. This is the one F3 path that works today.
    """
    before = _ask(journey, REFUND_QUESTION)
    assert before["branch"] == "known", "the STALE leg means nothing without a KNOWN before it"

    checkout = journey["checkout"]
    (checkout / "deploy").mkdir()
    _git("mv", "Dockerfile", "deploy/Dockerfile", cwd=checkout)
    remapped = _run("map", ".", "--store", str(journey["store"]), "--json", cwd=checkout)
    assert remapped.returncode == ExitCode.SUCCESS, remapped.stderr

    after = _ask(journey, REFUND_QUESTION)

    assert after["branch"] == "stale", after
    assert after["cause"], "a STALE answer must name the rule that decided it"
    assert after["citations"], (
        "STALE serves the prior answer rather than refusing -- refusing would discard "
        "knowledge the store genuinely holds"
    )
    assert "chargebacks" in after["citations"][0]["body_md"]


@pytest.mark.e2e
def test_the_whole_journey_runs_with_no_adapter_configured(journey: dict[str, Any]) -> None:
    """R3: the no-model mode is not a degraded mode, it is the product.

    *Fails when* any part of the demo starts needing a model. *Matters because*
    v6.1 makes offline-by-default and BYO-model permanent, and a journey that
    quietly required an adapter would make every client environment without one
    a broken install. *No other instrument catches it because* a developer
    machine with `ADOPT_ADAPTER` set would pass either way -- so this asserts the
    absence rather than relying on it."""
    from adopt_cli.config import resolve_all

    configured = {item.key: item.value for item in resolve_all()}
    assert not configured.get("ADOPT_ADAPTER"), (
        "this journey must run with no adapter; unset ADOPT_ADAPTER to run it honestly"
    )
    assert _ask(journey, REFUND_QUESTION)["branch"] == "known"
    assert _ask(journey, KEY_QUESTION)["branch"] == "unknown"
