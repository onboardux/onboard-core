"""Harvest against a real store: candidates, idempotence, and the queue closing it.

`test_harvest_miners` asks *what qualifies*; this file asks *what ends up in the
store*. The split matters because every defect below is invisible to a pure
miner test -- idempotence is a property of two runs against rows, and the
provenance class is a property of a chain.
"""

from collections.abc import Sequence

import pytest
from adopt_knowledge import IdentityView, PendingItem, confirm, edit, reject, run_harvest
from adopt_knowledge.gitlog import Commit
from adopt_knowledge.harvest import HARVEST_EXTRACTOR, batch_key, mine

from adopt_scope import Scope
from adopt_store.api import SqliteStoreHandle

pytestmark = pytest.mark.unit

KEY = batch_key("v0", "f" * 40)


def _commit(
    sha: str,
    subject: str = "Hold refunds for approval",
    body: str = "The provider settles asynchronously.",
    files: tuple[str, ...] = (),
) -> Commit:
    return Commit(
        sha=sha,
        parents=("b" * 40,),
        authored_at="2026-08-21T09:00:00+00:00",
        subject=subject,
        body=body,
        files=files,
    )


@pytest.fixture
def registry(s4_store: SqliteStoreHandle, s4_scope: Scope) -> list[IdentityView]:
    """One identity extracted from one file, so a path resolves uniquely."""
    identity = s4_store.identities().observe(
        scope=s4_scope,
        kind="symbol",
        namespace=None,
        key="refund",
        extractor="test",
        extractor_version="1",
    )
    return [
        IdentityView(
            identity_id=identity.id,
            uri=identity.uri,
            source_paths=("src/payments/refund.ts",),
        )
    ]


def _harvest(
    store: SqliteStoreHandle,
    scope: Scope,
    identities: Sequence[IdentityView],
    commits: Sequence[Commit],
    *,
    known: dict[str, str] | None = None,
    bound: frozenset[tuple[str, str]] = frozenset(),
    actor: str | None = None,
) -> object:
    return run_harvest(
        mine(commits),
        scope=scope,
        identities=identities,
        known=known or {},
        knowledge=store.items(),
        bindings=store.bindings(),
        reviews=store.governance(),
        key=KEY,
        bound_pairs=bound,
        actor_id=actor,
    )


def _rows(store: SqliteStoreHandle, sql: str, *args: object) -> list[dict[str, object]]:
    return [dict(row) for row in store.backend.query(sql, tuple(args))]


def test_a_candidate_lands_unverified_with_its_commit_cited(
    s4_store: SqliteStoreHandle, s4_scope: Scope, registry: list[IdentityView]
) -> None:
    """The shape v6.1 §6 F7 requires, asserted against rows.

    *Fails when* a mined candidate lands `verified`, or lands with no commit
    provenance. *Matters because* `unverified` is the whole reason harvest is
    safe to run on four hundred commits -- coverage counts none of it -- and the
    commit citation is the whole reason a reviewer can check it. A candidate
    that is verified is canon nobody agreed to; one with no citation is an
    assertion with no evidence. *No other instrument catches it because* both
    defects produce a perfectly well-formed store: every foreign key resolves,
    `store doctor` is clean, and the only visible symptom of the first is a
    coverage number that went up on its own.
    """
    _harvest(s4_store, s4_scope, registry, [_commit("a" * 40, files=("src/payments/refund.ts",))])

    revisions = _rows(
        s4_store,
        "SELECT verification, authority_class, source_version FROM knowledge_revision",
    )
    assert [row["verification"] for row in revisions] == ["unverified"]
    assert [row["authority_class"] for row in revisions] == ["artifact_observed"]
    assert [row["source_version"] for row in revisions] == ["a" * 40]

    provenance = _rows(s4_store, "SELECT source_type, source_ref FROM provenance")
    assert provenance == [{"source_type": "commit", "source_ref": "a" * 40}]
    assert [row["kind"] for row in _rows(s4_store, "SELECT kind FROM knowledge_item")] == [
        "rationale"
    ]


def test_a_commits_files_bind_structurally_and_ambiguity_does_not(
    s4_store: SqliteStoreHandle, s4_scope: Scope, registry: list[IdentityView]
) -> None:
    """Plan D4, and the ambiguity rule that bounds it.

    *Fails when* a commit binds to every identity its files touch. *Matters
    because* a commit touching `pyproject.toml` names every dependency
    identity in it, and binding one sentence about one library to forty is a
    false binding at scale -- invariant #2's failure arriving through the door
    the plan opened for real evidence. *No other instrument catches it because*
    every one of those bindings is structurally justified by the letter of the
    rule: the file really was touched.
    """
    ambiguous = IdentityView(
        identity_id="id-a", uri="onboard-v1://f/e/s/p/dep/-/left", source_paths=("pyproject.toml",)
    )
    also_ambiguous = IdentityView(
        identity_id="id-b", uri="onboard-v1://f/e/s/p/dep/-/right", source_paths=("pyproject.toml",)
    )

    report = _harvest(
        s4_store,
        s4_scope,
        [*registry, ambiguous, also_ambiguous],
        [_commit("a" * 40, files=("src/payments/refund.ts", "pyproject.toml"))],
    )

    bindings = _rows(s4_store, "SELECT identity_id FROM binding")
    assert [row["identity_id"] for row in bindings] == [registry[0].identity_id]
    assert report.ambiguous_paths == ("pyproject.toml",)  # type: ignore[attr-defined]
    assert [
        row["extractor"] for row in _rows(s4_store, "SELECT extractor FROM binding_revision")
    ] == [HARVEST_EXTRACTOR]


def test_a_second_harvest_of_one_range_writes_nothing(
    s4_store: SqliteStoreHandle, s4_scope: Scope, registry: list[IdentityView]
) -> None:
    """Idempotence, measured against the rows rather than the report.

    *Fails when* a re-harvest creates a second item, revision, provenance row or
    review item for a commit already mined. *Matters because* `--since <tag>` is
    a range an FDE re-runs -- after a fetch, after a rebase, out of habit -- and
    a queue that grows a duplicate set every time is a queue they stop opening.
    *No other instrument catches it because* the first run is correct and the
    second exits `0` with a plausible report; only counting rows across two runs
    can tell them apart.
    """
    commits = [_commit("a" * 40, files=("src/payments/refund.ts",))]
    first = _harvest(s4_store, s4_scope, registry, commits)
    before = {
        table: len(_rows(s4_store, f"SELECT id FROM {table}"))  # noqa: S608 -- fixed names
        for table in ("knowledge_item", "knowledge_revision", "provenance", "review_item")
    }

    known = {"a" * 40: first.created[0]}  # type: ignore[attr-defined]
    bound = frozenset(
        (str(row["item_id"]), str(row["identity_id"]))
        for row in _rows(s4_store, "SELECT item_id, identity_id FROM binding")
    )
    second = _harvest(s4_store, s4_scope, registry, commits, known=known, bound=bound)

    after = {
        table: len(_rows(s4_store, f"SELECT id FROM {table}"))  # noqa: S608 -- fixed names
        for table in ("knowledge_item", "knowledge_revision", "provenance", "review_item")
    }
    assert after == before
    assert second.created == []  # type: ignore[attr-defined]
    assert second.review_batch_id is None  # type: ignore[attr-defined]
    assert len(second.known) == 1  # type: ignore[attr-defined]


def _pending(store: SqliteStoreHandle, key: str = KEY) -> PendingItem:
    """The one open queue entry, assembled the way the CLI assembles it."""
    item = _rows(store, "SELECT id, item_id, proposed_revision_id FROM review_item")[0]
    revision = _rows(
        store,
        "SELECT id, body_md, source_version FROM knowledge_revision WHERE id = ?",
        item["proposed_revision_id"],
    )[0]
    return PendingItem(
        review_item_id=str(item["id"]),
        review_batch_id="rb-unused",
        batch_key=key,
        item_id=str(item["item_id"]),
        title="Hold refunds for approval",
        suggestions=(),
        body_md=str(revision["body_md"]),
        head_revision_id=str(revision["id"]),
        source_version=str(revision["source_version"]),
    )


def test_confirming_a_candidate_appends_a_verified_revision_and_keeps_the_mined_one(
    s4_store: SqliteStoreHandle, s4_scope: Scope, registry: list[IdentityView]
) -> None:
    """Confirmation promotes without erasing -- the append-only rule doing its job.

    *Fails when* confirming mutates the mined revision in place, or appends one
    that is still unverified. *Matters because* "what did the machine say before
    a human touched it" has to stay answerable forever: it is how the §9
    promotion trigger measures a heuristic, and how a client's auditor
    distinguishes a mined claim from an agreed one. *No other instrument catches
    it because* an in-place update leaves a store that reads perfectly -- one
    revision, verified, with a commit citation -- and looks exactly like a
    candidate somebody confirmed.
    """
    _harvest(s4_store, s4_scope, registry, [_commit("a" * 40, files=("src/payments/refund.ts",))])
    item = _pending(s4_store)

    outcome = confirm(
        item,
        reviews=s4_store.governance(),
        bindings=s4_store.bindings(),
        knowledge=s4_store.items(),
        actor_id="alice",
    )

    revisions = _rows(
        s4_store,
        "SELECT id, verification, authority_class, created_by_actor_id "
        "FROM knowledge_revision ORDER BY id",
    )
    assert len(revisions) == 2, "confirmation appends; it never edits"
    mined, agreed = revisions
    assert (mined["verification"], mined["authority_class"]) == ("unverified", "artifact_observed")
    assert (agreed["verification"], agreed["authority_class"]) == ("verified", "human_confirmed")
    assert agreed["created_by_actor_id"] == "alice"
    assert outcome.revision_id == agreed["id"]
    assert outcome.bindings == (), "a candidate's bindings were made at harvest"


def test_nothing_a_human_writes_can_claim_artifact_observed(
    s4_store: SqliteStoreHandle, s4_scope: Scope, registry: list[IdentityView]
) -> None:
    """The provenance-class rule, asserted on the path that would break it.

    *Fails when* an `--edit` revision lands `artifact_observed`, or its
    provenance is recorded as anything but `human`. *Matters because*
    `artifact_observed` is a claim that text was read out of the client's own
    system -- v6.1 §6 B2 says a human's or a summariser's words "can never claim
    `artifact_observed`" -- and Build 3's `adopt ask` will cite that class to a
    client as the difference between what their code says and what somebody
    thinks. *No other instrument catches it because* the edited revision is
    well-formed either way, and the field is only ever read by code that trusts
    it.
    """
    _harvest(s4_store, s4_scope, registry, [_commit("a" * 40, files=("src/payments/refund.ts",))])
    item = _pending(s4_store)

    edit(
        item,
        reviews=s4_store.governance(),
        knowledge=s4_store.items(),
        body_md="Refunds are held because the provider settles T+2.",
        source_ref="corrections/refund.md",
        actor_id="alice",
    )

    revisions = _rows(
        s4_store,
        "SELECT id, authority_class, body_md FROM knowledge_revision ORDER BY id",
    )
    authored = revisions[-1]
    assert authored["authority_class"] == "human_confirmed"
    assert authored["body_md"] == "Refunds are held because the provider settles T+2."

    provenance = {
        (str(row["source_type"]), str(row["revision_id"]))
        for row in _rows(s4_store, "SELECT source_type, revision_id FROM provenance")
    }
    assert ("human", str(authored["id"])) in provenance
    assert ("commit", str(authored["id"])) not in provenance
    assert ("commit", str(revisions[0]["id"])) in provenance, (
        "the superseded revision keeps its own citation -- provenance belongs to a "
        "revision, so promoting one can never rewrite another"
    )
    assert [row["resolution"] for row in _rows(s4_store, "SELECT resolution FROM review_item")] == [
        "corrected"
    ]


def test_a_rejected_candidate_stays_unverified_forever(
    s4_store: SqliteStoreHandle, s4_scope: Scope, registry: list[IdentityView]
) -> None:
    """*Fails when* rejecting a candidate writes a revision, or deletes one.

    *Matters because* a rejected candidate must never count toward coverage and
    must never be served as canon -- and the record that it was mined and
    refused is itself evidence, so it is stamped rather than removed. *No other
    instrument catches it because* both wrong answers look tidy: a deleted
    candidate leaves a clean store, and a verified one leaves a store that
    reports more coverage.
    """
    _harvest(s4_store, s4_scope, registry, [_commit("a" * 40, files=("src/payments/refund.ts",))])
    item = _pending(s4_store)

    outcome = reject(item, reviews=s4_store.governance())

    assert outcome.resolution == "rejected"
    assert [
        row["verification"]
        for row in _rows(s4_store, "SELECT verification FROM knowledge_revision")
    ] == ["unverified"]
    assert len(_rows(s4_store, "SELECT id FROM knowledge_item")) == 1
    assert [row["resolution"] for row in _rows(s4_store, "SELECT resolution FROM review_item")] == [
        "rejected"
    ]
