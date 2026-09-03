"""The Build 9 demo journey -- v6.1 §6's six steps, run verbatim.

*Fails when* any line of the Build 9 demo stops working end to end: a step stops
being recorded, the order stops being enforced, a failed verification task stops
becoming an answerable question, the acceptance digest stops being reproducible
from the client's own copy, or the close stops transferring ownership and the
open items with it. *Matters because* this is the **Build Definition of Done** --
v6.1 §4 R1 requires every build to end in a verb an FDE runs on a real engagement
and gets value from that day, and for Build 9 that day is the last day of the
engagement. *No other instrument catches it because* every unit test in this
suite hands the rules values it constructed; only this one proves the CLI reads
a real store, writes real rows, and produces two files a client can check
without us.

**The client-side check is the one that could not be faked.** Test 8 imports the
delivered bundle into a *fresh* store, re-exports it, and recomputes the
acceptance digest from those bytes -- with a `written_at` that necessarily
differs. That is exactly what a client does to verify what they were handed, and
it is the only assertion here that would fail if the digest quietly depended on
anything but the byte-stable table files.

**Every step goes through the CLI as a subprocess**, the same entry-point module
the release binary compiles (CR-56), and assertions read the store file directly
rather than through `adopt_store`, so one bug cannot both write the wrong row and
vouch for it.
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

pytestmark = pytest.mark.e2e

ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "web" / "fastapi_orders"
SCOPE = "northwind/acme-erp/orders-api/prod"
SYSTEM = "orders-api"
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}
RECEIVING_OWNER = "client-platform"

REFUND_DOC = """---
audience: client_ops
kind: procedure
---
# Refund approvals

The approval step exists on refunds because chargebacks were disputed twice in
the first quarter and the acquirer required a documented human decision.
"""

CHECKLIST = """audience: client_ops
tasks:
  - id: find-approval
    task: "Explain why refunds need approval, using only the pack"
    outcome: pass
    performed_by: bob
  - id: rotate-key
    task: "Rotate the orders API key using only the pack and adopt ask"
    outcome: fail
    note: "The pack names no rotation procedure"
    performed_by: bob
"""


def _run(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The `--json` envelope from stdout.

    Structured log lines are single-line JSON on stdout too, and `emit` writes
    the payload indented -- so the envelope begins at the last line that is a
    bare `{`, or at the first brace when the command logged nothing.
    """
    text = completed.stdout
    try:
        start = text.rindex("\n{\n") + 1 if "\n{\n" in text else text.index("{")
        return dict(json.loads(text[start:]))
    except (ValueError, json.JSONDecodeError):
        pytest.fail(f"stdout was not the JSON envelope:\n{completed.stdout[:2000]}")


def _sql(store: Path, query: str, *args: object) -> list[tuple[Any, ...]]:
    with sqlite3.connect(store) as connection:
        return list(connection.execute(query, args).fetchall())


def _git(*argv: str, cwd: Path) -> None:
    done = subprocess.run(["git", *argv], cwd=str(cwd), check=False, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.fixture
def journey(tmp_path: Path) -> dict[str, Any]:
    """`init` -> `map` -> `ingest`: the state a handover is run against.

    Everything the event writes goes **outside** the checkout, for the pack
    journey's reason: `adopt map` walks the repository, so a pack or a bundle
    written into it becomes source on the next run.
    """
    if not shutil.which("git"):  # pragma: no cover -- every CI runner ships git
        pytest.skip("git is not on PATH, and ingest reads a real checkout")

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
    checklist = tmp_path / "checklist.yaml"
    checklist.write_text(CHECKLIST, encoding="utf-8")

    for argv in (
        ("init", ".", "--scope", SCOPE, "--answers", str(answers), "--archetype", "web"),
        ("map", "."),
        ("ingest", "docs"),
    ):
        done = _run(*argv, "--store", str(store), "--json", cwd=checkout)
        assert done.returncode == ExitCode.SUCCESS, f"{argv[0]} failed: {done.stderr}"

    return {
        "checkout": checkout,
        "store": store,
        "out": tmp_path / "handover",
        "acceptance": tmp_path / "acceptance",
        "checklist": checklist,
        "tmp": tmp_path,
    }


def _handover(
    journey: dict[str, Any], *argv: str, expect: int = ExitCode.SUCCESS
) -> dict[str, Any]:
    done = _run(
        "handover", *argv, "--store", str(journey["store"]), "--json", cwd=journey["checkout"]
    )
    assert done.returncode == expect, (
        f"`handover {argv[0]}` exited {done.returncode}: {done.stderr}"
    )
    return _payload(done)


def _through_snapshot(journey: dict[str, Any]) -> dict[str, Any]:
    """Steps 1-5, so a test about `close` does not restate the whole demo."""
    started = _handover(
        journey,
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        RECEIVING_OWNER,
        "--actor",
        "alice",
    )
    _handover(
        journey, "elicit", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice"
    )
    _handover(journey, "pack", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice")
    _handover(
        journey,
        "verify",
        "--checklist",
        str(journey["checklist"]),
        "--system",
        SYSTEM,
        "--actor",
        "alice",
        expect=ExitCode.DEGRADED_WITH_FINDINGS,
    )
    _handover(
        journey,
        "snapshot",
        "--system",
        SYSTEM,
        "--out",
        str(journey["acceptance"]),
        "--actor",
        "alice",
    )
    return started


# -- demo line 1: the six steps, in order -----------------------------------


def test_the_whole_event_runs_and_every_step_is_recorded(journey: dict[str, Any]) -> None:
    """v6.1 §6 Build 9's demo block, run line by line."""
    started = _handover(
        journey,
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        RECEIVING_OWNER,
        "--actor",
        "alice",
    )
    handover_id = started["handover_id"]
    assert started["opening"]["identities"] > 0, "the store was not mapped"
    assert started["receiving_owner"] == RECEIVING_OWNER

    # 2 -- the agenda names the uncovered identities and nothing else.
    elicited = _handover(
        journey, "elicit", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice"
    )
    agenda = Path(elicited["agenda"]).read_text(encoding="utf-8")
    assert elicited["gaps"] > 0
    uncovered = {
        row[0]
        for row in _sql(
            journey["store"],
            "SELECT uri FROM identity WHERE id NOT IN (SELECT identity_id FROM binding)",
        )
    }
    assert uncovered, "the fixture produced no uncovered identity to elicit about"
    for uri in uncovered:
        assert uri in agenda, f"{uri} is uncovered and absent from the agenda"

    # 3 -- one pack per audience, every section stamped.
    packed = _handover(
        journey, "pack", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice"
    )
    assert len(packed["packs"]) == 4, "the four declared audiences were not all emitted"
    for entry in packed["packs"]:
        document = Path(entry["markdown_path"]).read_text(encoding="utf-8")
        assert "## System overview" in document
        assert "## Coverage gaps" in document
        assert entry["markdown_sha256"], "the pack was not digested"

    # 4 -- verification: one failure, recorded, and answerable.
    verified = _handover(
        journey,
        "verify",
        "--checklist",
        str(journey["checklist"]),
        "--system",
        SYSTEM,
        "--actor",
        "alice",
        expect=ExitCode.DEGRADED_WITH_FINDINGS,
    )
    assert (verified["passed"], verified["failed"]) == (1, 1)
    escalation_id = verified["escalations"][0]["escalation_id"]
    stored = _sql(
        journey["store"], "SELECT question, status FROM escalation WHERE id = ?", escalation_id
    )
    assert stored, "the failed task recorded no open question"
    assert "Rotate the orders API key" in stored[0][0], "the question lost its task text"
    assert stored[0][1] == "open"

    # ... and answered in the room, through the verb that already exists.
    answered = _run(
        "answer",
        escalation_id,
        "--text",
        "Rotate it in the vault, then restart.",
        "--actor",
        "alice",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert answered.returncode == ExitCode.SUCCESS, answered.stderr
    assert _sql(journey["store"], "SELECT status FROM escalation WHERE id = ?", escalation_id) == [
        ("answered",)
    ]

    # 5 -- the acceptance snapshot.
    snapshot = _handover(
        journey,
        "snapshot",
        "--system",
        SYSTEM,
        "--out",
        str(journey["acceptance"]),
        "--actor",
        "alice",
    )
    assert Path(snapshot["bundle_path"]).is_dir()
    assert len(snapshot["bundle_digest"]) == 64

    # 6 -- ownership transfer, and the open items with it.
    closed = _handover(
        journey, "close", "--accepted-by", "bob", "--system", SYSTEM, "--actor", "alice"
    )
    assert closed["current_owner"] == RECEIVING_OWNER
    assignment = _sql(
        journey["store"],
        "SELECT scope, assignment_reason, actor_or_group_id FROM ownership_assignment WHERE id = ?",
        closed["ownership_assignment_id"],
    )
    assert assignment == [("system", "handover", RECEIVING_OWNER)]

    # Every transferred gap carries an owner. This is honesty rule B.
    for gap_key in closed["transferred"]["gaps_assigned"]:
        owner = _sql(
            journey["store"],
            "SELECT owner_actor_id, status FROM coverage_gap WHERE gap_key = ?",
            gap_key,
        )
        assert owner == [(RECEIVING_OWNER, "acknowledged")], f"{gap_key} transferred unowned"

    # `status` is the record: six steps, dated, attributed, resumable.
    status = _handover(journey, "status", "--system", SYSTEM)
    assert status["state"] == "closed"
    assert status["handover_id"] == handover_id
    assert [step["step"] for step in status["steps"] if step["done"]] == [
        "handover_started",
        "handover_elicited",
        "handover_pack_emitted",
        "handover_verified",
        "handover_snapshot_taken",
        "handover_closed",
    ]
    assert all(step["by"] == "alice" for step in status["steps"] if step["done"])
    assert status["next_step"] is None
    assert status["record"]["snapshot"]["bundle_digest"] == snapshot["bundle_digest"]

    # One `handover.generated` value event per emitted pack, and no other build
    # has ever written this vocabulary.
    assert _sql(
        journey["store"],
        "SELECT COUNT(*) FROM value_event WHERE event_type = 'handover.generated'",
    ) == [(4,)]


# -- the handover's packs are `adopt pack`'s packs --------------------------


def test_the_emitted_pack_is_byte_identical_to_a_direct_adopt_pack(
    journey: dict[str, Any],
) -> None:
    """One assembler, two callers -- asserted rather than claimed.

    A second assembly path would eventually differ, and the difference would
    reach a client as a document that does not match the one the FDE reviewed.
    """
    _handover(
        journey,
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        RECEIVING_OWNER,
        "--actor",
        "alice",
    )
    _handover(
        journey, "elicit", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice"
    )
    _handover(journey, "pack", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice")

    direct = journey["tmp"] / "direct"
    done = _run(
        "pack",
        "--audience",
        "client_ops",
        "--out",
        str(direct),
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert done.returncode == ExitCode.SUCCESS, done.stderr

    assert (journey["out"] / "client_ops.md").read_bytes() == (
        direct / "client_ops.md"
    ).read_bytes()


# -- resumable and ordered --------------------------------------------------


def test_the_checklist_is_ordered_resumable_and_refuses_by_name(
    journey: dict[str, Any],
) -> None:
    """Every refusal names what to do instead; `status` names what comes next."""
    before = _handover(journey, "status", "--system", SYSTEM)
    assert before["state"] == "none"

    _handover(
        journey,
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        RECEIVING_OWNER,
        "--actor",
        "alice",
    )
    mid = _handover(journey, "status", "--system", SYSTEM)
    assert mid["state"] == "open"
    assert mid["next_step"] == "handover_elicited"
    assert mid["next_verb"] == "adopt handover elicit"

    # Out of order: `close` names the step it needs.
    refused = _run(
        "handover",
        "close",
        "--accepted-by",
        "bob",
        "--system",
        SYSTEM,
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert refused.returncode == ExitCode.USAGE_ERROR
    assert "HANDOVER_STEP_OUT_OF_ORDER" in refused.stderr
    assert "adopt handover snapshot" in refused.stderr

    # A second `start` points at the open one rather than forking it.
    second = _run(
        "handover",
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        "someone-else",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert second.returncode == ExitCode.USAGE_ERROR
    assert "HANDOVER_ALREADY_OPEN" in second.stderr

    # After closing, the event is history: a step verb refuses rather than
    # appending to an acceptance both parties already hold.
    _handover(
        journey, "elicit", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice"
    )
    _handover(journey, "pack", "--system", SYSTEM, "--out", str(journey["out"]), "--actor", "alice")
    _handover(
        journey,
        "verify",
        "--checklist",
        str(journey["checklist"]),
        "--system",
        SYSTEM,
        expect=ExitCode.DEGRADED_WITH_FINDINGS,
    )
    _handover(
        journey,
        "snapshot",
        "--system",
        SYSTEM,
        "--out",
        str(journey["acceptance"]),
    )
    _handover(journey, "close", "--accepted-by", "bob", "--system", SYSTEM)

    after = _run(
        "handover",
        "elicit",
        "--system",
        SYSTEM,
        "--out",
        str(journey["out"]),
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert after.returncode == ExitCode.USAGE_ERROR
    assert "HANDOVER_NOT_OPEN" in after.stderr


# -- both parties hold it ---------------------------------------------------


def test_the_client_recomputes_the_acceptance_digest_from_their_own_copy(
    journey: dict[str, Any],
) -> None:
    """The promise that makes the snapshot worth taking.

    Import the delivered bundle into a fresh store, export it again, and
    recompute. The re-export's `written_at` necessarily differs, which is the
    whole reason the digest is over the table digests and nothing else.
    """
    from adopt_handover import acceptance_digest

    _through_snapshot(journey)
    delivered = json.loads((journey["acceptance"] / "acceptance.json").read_text(encoding="utf-8"))
    digest = delivered["snapshot"]["bundle_digest"]

    client_store = journey["tmp"] / "client.db"
    reexport = journey["tmp"] / "reexport"
    imported = _run(
        "import",
        str(journey["acceptance"] / "bundle"),
        "--into",
        str(client_store),
        "--json",
        cwd=journey["checkout"],
    )
    assert imported.returncode == ExitCode.SUCCESS, imported.stderr
    exported = _run(
        "export", str(reexport), "--store", str(client_store), "--json", cwd=journey["checkout"]
    )
    assert exported.returncode == ExitCode.SUCCESS, exported.stderr

    original_manifest = json.loads(
        (journey["acceptance"] / "bundle" / "manifest.json").read_text(encoding="utf-8")
    )
    client_manifest = json.loads((reexport / "manifest.json").read_text(encoding="utf-8"))
    assert original_manifest["written_at"] != client_manifest["written_at"], (
        "the two exports share a written_at, so this test proves nothing"
    )

    recomputed = acceptance_digest(
        (entry["name"], entry["sha256"]) for entry in client_manifest["tables"]
    )
    assert recomputed == digest, "the client cannot verify what they were handed"


# -- the two honesty rules --------------------------------------------------


def test_an_event_cannot_be_opened_without_a_receiving_owner(
    journey: dict[str, Any],
) -> None:
    """Honesty rule A at the near end: no owner named, no event.

    The far end -- a close whose ownership post-check fails, rolled back whole --
    is `tests/unit/test_handover_transfer.py`, which can drive the composition
    into that state directly.
    """
    refused = _run(
        "handover",
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        "  ",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )

    assert refused.returncode == ExitCode.POLICY_REFUSAL
    assert "HANDOVER_UNOWNED" in refused.stderr
    assert _sql(journey["store"], "SELECT COUNT(*) FROM audit_event") == [(0,)]


def test_a_writing_verb_refuses_a_replica_and_status_still_answers(
    journey: dict[str, Any],
) -> None:
    """R9's third replica rule: the plane owns an operated system's canon.

    A close against a replica would write the ownership transfer, the gap
    dispositions and the acceptance trail into a file the next `adopt pull`
    replaces wholesale -- the whole engagement closure, gone with no trace.
    `status` is a read and stays available, which is what an operator needs when
    they are trying to work out what this store even is.
    """
    marker = journey["store"].with_name(journey["store"].name + ".replica.json")
    marker.write_text(
        json.dumps(
            {
                "plane_url": "https://plane.example.invalid",
                "system_id": "sys_01J000000000000000000000",
                "pulled_at": "2026-09-02T12:00:00.000Z",
                "bundle_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    refused = _run(
        "handover",
        "start",
        "--system",
        SYSTEM,
        "--receiving-owner",
        RECEIVING_OWNER,
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert refused.returncode == ExitCode.POLICY_REFUSAL
    assert "HANDOVER_TARGET_IS_REPLICA" in refused.stderr
    assert _sql(journey["store"], "SELECT COUNT(*) FROM audit_event") == [(0,)]

    readable = _run(
        "handover",
        "status",
        "--system",
        SYSTEM,
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert readable.returncode == ExitCode.SUCCESS, "a read was refused on a replica"
