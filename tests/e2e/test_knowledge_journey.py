"""The Build 2 demo journey, run verbatim on two real repositories.

*Fails when* any line of v6.1 §6's Build 2 demo stops working end to end: ingest
stops binding, harvest mines nothing or mines the same range twice, the queue
loses a population, confirming does not move coverage, rejecting does, or a
`git mv` orphans a binding. *Matters because* this is the **Build Definition of
Done** -- v6.1 §4 R1 makes every build end in a verb an FDE runs on a real
engagement and gets value from that day. *No other instrument catches it
because* every unit fixture in this suite is a tree we wrote: `test_gitlog`'s
repository has three commits we authored, and `test_harvest_write`'s registry
has one identity pointing at one path. Neither can show what happens when a
matcher meets four hundred real commits, and the S2.2 session found exactly one
defect that way -- a candidate with no `audience_tag` could be mined, bound and
confirmed and still count for nothing, because `recompute_coverage` input 4
refuses an untagged item. Every row in the store was correct.

**Reuses `test_map_journey`'s harness rather than copying it.** The pinned-clone
check and the anti-skip rule are the same rules, and two copies is two places
for the rule that makes this job evidence to drift.

**The range is `HEAD~<N>`, not a tag.** The demo block writes `--since <tag>`,
and a tag is one kind of ref -- but `chat-langchain` has **no tags at all** at
its pinned commit, so a tag-only journey would run on one repository and skip
the other, which is the shape of every measurement this pack has caught going
blind. A fixed commit count is the same range on every machine because the
commit is pinned, and it is the same rule for both repositories.
"""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from adopt_obs import ExitCode
from tests.e2e.test_map_journey import (
    ANSWERS,
    Reference,
    _absent,
    _payload,
    _run,
    assert_pinned,
    reference,  # noqa: F401 -- the parametrised fixture, used by every test here
)

#: How far back the demo mines. Big enough that both repositories yield
#: candidates *and* bindings -- a range that mined nothing would pass every
#: assertion below while proving nothing -- and small enough that the job stays
#: a demo rather than a benchmark.
SINCE_DEPTH = 40
SINCE = f"HEAD~{SINCE_DEPTH}"

#: Documents to ingest per repository. Named rather than globbed: a `docs/`
#: directory that does not exist in one of the two would silently ingest nothing
#: there, and "no documents" and "no bindings" are indistinguishable downstream.
DOCUMENTS = {
    "fullstack-fastapi": ("README.md", "development.md", "deployment.md"),
    "chat-langchain": ("README.md",),
}


def _sql(store: Path, query: str, *args: object) -> list[tuple[Any, ...]]:
    """Straight at the file, no facade involved.

    Deliberately not through `adopt_store`: the point of an end-to-end test is
    to check what the commands *left behind*, and asking the same library that
    wrote it would make one bug capable of hiding itself.
    """
    with sqlite3.connect(store) as connection:
        return list(connection.execute(query, args).fetchall())


def _count(store: Path, table: str) -> int:
    return int(_sql(store, f"SELECT COUNT(*) FROM {table}")[0][0])  # noqa: S608 -- fixed names


@pytest.fixture
def journey(reference: Reference, tmp_path: Path) -> dict[str, Any]:  # noqa: F811
    """`init` → `map` → `ingest` → `harvest` → `harvest` again, once per repository.

    One fixture for the whole sequence rather than one test per line: the demo
    **is** a sequence, and its sharpest assertion -- that a second harvest of
    one range writes nothing -- exists only relative to the first.
    """
    assert_pinned(reference)
    if not shutil.which("git"):  # pragma: no cover -- CI runners all ship git
        _absent("git is not on PATH, so there is no history to mine")

    store = tmp_path / "store.db"
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")

    init = _run(
        "init",
        str(reference.checkout),
        "--scope",
        reference.scope,
        "--answers",
        str(answers),
        "--archetype",
        reference.archetype,
        "--store",
        str(store),
        "--json",
        cwd=tmp_path,
    )
    assert init.returncode == ExitCode.SUCCESS, init.stderr
    mapped = _run("map", str(reference.checkout), "--store", str(store), "--json", cwd=tmp_path)
    assert mapped.returncode == ExitCode.SUCCESS, mapped.stderr

    documents = DOCUMENTS[reference.slug]
    for name in documents:
        assert (reference.checkout / name).is_file(), (
            f"{reference.slug} has no {name}; the corpus this journey ingests is named "
            "rather than globbed, so a missing file is a failure and never an empty run"
        )
    # Run from inside the checkout: `provenance.source_ref` records the path
    # relative to the working directory, and an absolute path would cite this
    # machine rather than the repository.
    ingested = _run("ingest", *documents, "--store", str(store), "--json", cwd=reference.checkout)
    harvested = _run(
        "harvest", "--since", SINCE, "--store", str(store), "--json", cwd=reference.checkout
    )
    again = _run(
        "harvest", "--since", SINCE, "--store", str(store), "--json", cwd=reference.checkout
    )
    return {
        "reference": reference,
        "store": store,
        "tmp": tmp_path,
        "mapped": mapped,
        "ingested": ingested,
        "harvested": harvested,
        "again": again,
    }


@pytest.mark.e2e
def test_documents_become_knowledge_and_only_structural_matches_bind(
    journey: dict[str, Any],
) -> None:
    """Demo line 1, and **critical semantic invariant #2** on a real corpus.

    *Fails when* a name match binds. *Matters because* a false binding makes
    `recompute_coverage` report the identity as covered, so `adopt gaps` stops
    asking for the knowledge that is genuinely missing -- silent, and
    self-reinforcing. *No other instrument catches it because* the unit test for
    this uses prose we wrote to be adversarial; only a real README against a
    real registry shows the ratio, and here it is roughly one binding per
    fifteen suggestions.
    """
    ingested = journey["ingested"]
    assert ingested.returncode == ExitCode.SUCCESS, ingested.stderr
    payload = _payload(ingested)

    assert payload["created"] == payload["documents"] > 0
    assert payload["suggestions"] > 0, "no suggestions means the matcher did no work"
    assert payload["review_batch"], "suggestions must reach the queue, not the store"
    assert all(binding["tier"] in {"uri", "path"} for binding in payload["bindings"]), payload[
        "bindings"
    ]

    tiers = {
        str(row[0])
        for row in _sql(journey["store"], "SELECT DISTINCT extractor FROM binding_revision")
    }
    assert "ingest-name-confirmed" not in tiers, (
        "a name match created a binding without a human confirming it -- invariant #2"
    )


@pytest.mark.e2e
def test_harvest_mines_candidates_that_carry_their_commit(journey: dict[str, Any]) -> None:
    """Demo line 2: local mining into unverified candidates with evidence.

    *Fails when* harvest mines nothing on a real repository, lands a candidate
    `verified`, or lands one with no commit citation. *Matters because*
    `unverified` is what makes it safe to mine forty commits unattended -- none
    of it counts -- and the citation is what makes a candidate checkable at all.
    *No other instrument catches it because* the fixture repository in
    `test_gitlog` has three commits we wrote to be minable; only a real history
    shows whether the six signals fire on commits people actually write.
    """
    harvested = journey["harvested"]
    assert harvested.returncode == ExitCode.SUCCESS, harvested.stderr
    payload = _payload(harvested)

    assert payload["commits_read"] == SINCE_DEPTH
    assert payload["created"] > 0, "forty real commits yielded no decision at all"
    assert payload["already_known"] == 0

    verifications = {
        str(row[0])
        for row in _sql(
            journey["store"],
            "SELECT DISTINCT kr.verification FROM knowledge_revision kr "
            "JOIN provenance p ON p.revision_id = kr.id WHERE p.source_type = 'commit'",
        )
    }
    assert verifications == {"unverified"}, verifications

    cited = _count(journey["store"], "provenance")
    assert cited >= payload["created"], "every mined revision cites the commit it came from"
    authorities = {
        str(row[0])
        for row in _sql(
            journey["store"],
            "SELECT DISTINCT kr.authority_class FROM knowledge_revision kr "
            "JOIN provenance p ON p.revision_id = kr.id WHERE p.source_type = 'commit'",
        )
    }
    assert authorities == {"artifact_observed"}


@pytest.mark.e2e
def test_a_second_harvest_of_the_same_range_writes_nothing(journey: dict[str, Any]) -> None:
    """Demo line 2, re-run: idempotence measured against rows.

    *Fails when* a re-harvest creates a second item, revision or review item for
    a commit already mined. *Matters because* `--since` is a range an FDE re-runs
    after every fetch, and a queue that grows a duplicate set each time is a
    queue they stop opening -- which takes the whole build's value with it. *No
    other instrument catches it because* the first run is correct and the second
    exits `0` with a plausible report; only counting rows across both can tell
    them apart.
    """
    again = journey["again"]
    assert again.returncode == ExitCode.SUCCESS, again.stderr
    payload = _payload(again)
    first = _payload(journey["harvested"])

    assert payload["created"] == 0
    assert payload["already_known"] == first["created"]
    assert payload["review_batch"] is None, "nothing new to review means no batch at all"
    assert payload["bindings_created"] == 0


@pytest.mark.e2e
def test_one_queue_carries_both_populations(journey: dict[str, Any]) -> None:
    """Demo line 3: candidates and suggested bindings in **one** surface (F5).

    *Fails when* either population stops appearing, or a candidate arrives with
    no evidence to read it by. *Matters because* F5 exists precisely because
    v6.0 had specified two `adopt review` commands, and two queues means one a
    reviewer stops opening. *No other instrument catches it because* each
    population's own tests pass with the other missing entirely -- a queue
    holding only suggestions is a working queue.
    """
    listed = _run("review", "--store", str(journey["store"]), "--json", cwd=journey["tmp"])
    assert listed.returncode == ExitCode.SUCCESS, listed.stderr
    payload = _payload(listed)

    assert payload["candidates"] > 0
    assert payload["suggested_items"] > 0
    assert payload["open_items"] == payload["candidates"] + payload["suggested_items"]
    assert len(payload["batches"]) == 2, "one batch per producing run, both open"

    sources = {row["source"] for row in payload["queue"]}
    assert sources == {"ingest", "harvest"}
    commit_evidence = [row for row in payload["evidence"] if row["source_type"] == "commit"]
    assert commit_evidence, "a candidate a reviewer cannot check is a candidate they will reject"


@pytest.mark.e2e
def test_confirming_serves_coverage_and_rejecting_never_does(journey: dict[str, Any]) -> None:
    """Demo lines 3 and 4 together -- the honesty rule, before and after.

    *Fails when* an unconfirmed candidate counts toward coverage, or a confirmed
    one does not. *Matters because* v6.1 §6 B2 makes "unconfirmed knowledge and
    bindings do NOT count as coverage" the honesty invariant of this build, and
    a gap report that counts machine guesses stops asking for the knowledge that
    is actually missing. *No other instrument catches it because* the store is
    consistent either way and the wrong answer reports *more* coverage --
    which is the direction nobody investigates.
    """
    store = journey["store"]
    blocked = _run("gaps", "--store", str(store), "--json", cwd=journey["tmp"])
    assert blocked.returncode == ExitCode.SUCCESS, blocked.stderr
    before = _payload(blocked)
    unverified = [
        gap for gap in before["gaps"] if "verification_unverified" in gap["reasons"].split(", ")
    ]
    assert unverified, (
        "no identity was blocked by an unverified candidate, so this test would pass "
        "whether the rule worked or not -- the harvest bound nothing checkable"
    )

    candidates = _sql(
        store,
        "SELECT DISTINCT ri.id FROM review_item ri "
        "JOIN binding b ON b.item_id = ri.item_id "
        "JOIN knowledge_item ki ON ki.id = ri.item_id "
        "JOIN knowledge_revision kr ON kr.id = ki.current_revision_id "
        "WHERE ri.resolution IS NULL AND kr.verification = 'unverified' ORDER BY ri.id",
    )
    assert candidates, "no bound candidate to confirm"

    confirmed = _run(
        "review",
        "--confirm",
        str(candidates[0][0]),
        "--actor",
        "alice",
        "--store",
        str(store),
        "--json",
        cwd=journey["tmp"],
    )
    assert confirmed.returncode == ExitCode.SUCCESS, confirmed.stderr
    resolution = _payload(confirmed)["resolutions"][0]
    assert resolution["source"] == "harvest"
    assert resolution["revision"], "confirming a candidate appends the verified revision"

    for row in candidates[1:]:
        rejected = _run(
            "review", "--reject", str(row[0]), "--store", str(store), "--json", cwd=journey["tmp"]
        )
        assert rejected.returncode == ExitCode.SUCCESS, rejected.stderr

    after = _payload(_run("gaps", "--store", str(store), "--json", cwd=journey["tmp"]))
    assert after["covered"] > before["covered"], (
        f"confirming a bound candidate did not move coverage: "
        f"{before['covered']} -> {after['covered']}"
    )
    still_unverified = {
        str(row[0])
        for row in _sql(
            store, "SELECT verification FROM knowledge_revision WHERE verification IS NOT NULL"
        )
    }
    assert "unverified" in still_unverified, (
        "the rejected candidates must stay unverified forever -- rejecting deletes nothing"
    )


@pytest.mark.e2e
def test_an_edit_can_never_claim_artifact_observed(journey: dict[str, Any]) -> None:
    """Demo line 3's `edit` action, and the provenance rule it would break.

    *Fails when* a corrected revision lands `artifact_observed` or gets anything
    but `human` provenance. *Matters because* `artifact_observed` is a claim
    that text was read out of the client's own system, and Build 3's `adopt ask`
    cites that class to a client as the difference between what their code says
    and what somebody thinks. *No other instrument catches it because* the
    edited revision is well-formed either way, and only code that already trusts
    the field ever reads it.
    """
    store = journey["store"]
    open_candidate = _sql(
        store,
        "SELECT ri.id FROM review_item ri JOIN review_batch rb ON rb.id = ri.review_batch_id "
        "WHERE ri.resolution IS NULL AND rb.batch_key LIKE 'harvest:%' ORDER BY ri.id LIMIT 1",
    )
    assert open_candidate, "no open candidate left to correct"

    correction = journey["tmp"] / "correction.md"
    correction.write_text("A human wrote this sentence.\n", encoding="utf-8")
    edited = _run(
        "review",
        "--edit",
        str(open_candidate[0][0]),
        "--file",
        str(correction),
        "--actor",
        "alice",
        "--store",
        str(store),
        "--json",
        cwd=journey["tmp"],
    )
    assert edited.returncode == ExitCode.SUCCESS, edited.stderr
    revision = _payload(edited)["resolutions"][0]["revision"]
    assert revision

    authority = _sql(store, "SELECT authority_class FROM knowledge_revision WHERE id = ?", revision)
    assert str(authority[0][0]) == "human_confirmed"
    kinds = {
        str(row[0])
        for row in _sql(store, "SELECT source_type FROM provenance WHERE revision_id = ?", revision)
    }
    assert kinds == {"human"}, kinds
    assert (
        _sql(store, "SELECT resolution FROM review_item WHERE id = ?", open_candidate[0][0])[0][0]
        == "corrected"
    )


@pytest.mark.e2e
def test_a_mapped_move_repoints_bindings_through_the_alias(journey: dict[str, Any]) -> None:
    """Demo lines 5 and 6 -- **critical semantic invariant #3**, the G2 check.

    *Fails when* moving a file that bound knowledge orphans the binding, or
    rewrites the old URI. *Matters because* every binding, probe and piece of
    bound knowledge in every later build hangs off the URI, and a cosmetic
    `git mv` that detaches them detaches them permanently -- a URI is never
    rewritten. *No other instrument catches it because* Build 1 proved a *map*
    survives a move on a real tree, and nothing was bound to those identities at
    the time; only a store that has both a move and bindings can show the two
    interacting.
    """
    repo: Reference = journey["reference"]
    store = journey["store"]
    bound = _sql(
        store,
        "SELECT i.id, i.uri, ir.source_ref FROM binding b "
        "JOIN identity i ON i.id = b.identity_id "
        "JOIN identity_revision ir ON ir.identity_id = i.id "
        "WHERE ir.source_ref IS NOT NULL ORDER BY i.id",
    )
    subject = _movable(repo, bound)
    if subject is None:
        pytest.fail(
            "no bound identity resolves to a file that can be moved; this test would "
            "otherwise report a passing G2 check having moved nothing at all"
        )
    identity_id, old_uri, relative = subject

    before = _count(store, "binding")
    source = repo.checkout / relative
    moved_dir = repo.checkout / "adopt-moved"
    moved_dir.mkdir(exist_ok=True)
    _git(repo.checkout, "mv", relative, f"adopt-moved/{source.name}")
    try:
        remapped = _run(
            "map", str(repo.checkout), "--store", str(store), "--json", cwd=journey["tmp"]
        )
        assert remapped.returncode == ExitCode.SUCCESS, remapped.stderr
        moves = _payload(remapped)["moves"]
        assert any(move["from"] == old_uri for move in moves), moves
    finally:
        _git(repo.checkout, "mv", f"adopt-moved/{source.name}", relative)
        moved_dir.rmdir()
        _git(repo.checkout, "reset", "--quiet", "HEAD", ".")

    assert _count(store, "binding") == before, "a move must never add or remove a binding row"
    assert _sql(store, "SELECT uri FROM identity WHERE id = ?", identity_id)[0][0] == old_uri, (
        "the old URI was rewritten; it must stay resolvable forever"
    )
    assert (
        int(_sql(store, "SELECT COUNT(*) FROM binding WHERE identity_id = ?", identity_id)[0][0])
        > 0
    ), "the moved identity lost its bindings"
    alias = _sql(
        store,
        "SELECT alias_of_identity_id FROM identity_revision "
        "WHERE identity_id = ? AND status = 'moved'",
        identity_id,
    )
    assert alias and alias[0][0], "the move recorded no alias, so nothing re-points"
    orphans = _sql(
        store,
        "SELECT COUNT(*) FROM binding b LEFT JOIN identity i ON i.id = b.identity_id "
        "WHERE i.id IS NULL",
    )
    assert int(orphans[0][0]) == 0

    doctor = _run("store", "doctor", "--store", str(store), "--json", cwd=journey["tmp"])
    codes = {finding["code"] for finding in _payload(doctor)["findings"]}
    # `COVERAGE_CACHE_DISAGREEMENT` is Build 0's designed alarm firing because
    # bindings were created since the last recompute; the remedy is
    # `adopt coverage recompute --rebuild`. What the G2 line asserts is the
    # absence of the two findings a detached binding would produce.
    assert "REVISION_HEAD_DANGLING" not in codes, codes
    assert "REVISION_CHAIN_FORK" not in codes, codes


def _movable(repo: Reference, bound: list[tuple[Any, ...]]) -> tuple[str, str, str] | None:
    """`(identity_id, uri, path)` for a bound identity whose **URI moves with the file**.

    Chosen from the store rather than hard-coded, because the demo's
    `src/payments/refund.ts` exists in neither reference repository -- and a
    hard-coded path that is absent turns this check into a skip.

    The directory test is the load-bearing half, and the first version of this
    test lacked it. Not every identity extracted from a file is *named* after
    it: a CI job's key is the job's own name, so
    `job/ci/Deploy%20Production%20Agent` is the same URI wherever the workflow
    file lives. Moving that file is a perfectly good demonstration of a binding
    surviving -- and a perfectly useless demonstration of **G2**, which is about
    the alias that fires when a URI *does* change. Requiring the parent
    directory to appear in the URI selects an identity the move actually
    relocates.
    """
    for identity_id, uri, source_ref in bound:
        relative = str(source_ref).rsplit(":", 1)[0]
        candidate = repo.checkout / relative
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        # Top-level files are excluded twice over: they have no parent to look
        # for, and moving `README.md` would change what the documents ingested
        # above cite -- confounding this assertion with a different one.
        if parent and candidate.is_file() and parent in str(uri):
            return str(identity_id), str(uri), relative
    return None


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args], cwd=str(root), check=False, capture_output=True, text=True
    )
    assert completed.returncode == 0, f"git {' '.join(args)}: {completed.stderr}"
