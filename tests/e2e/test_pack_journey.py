"""The Build 4 demo journey -- the deterministic legs, run verbatim.

*Fails when* any line of v6.1 §6's Build 4 demo stops working end to end:
assembly stops selecting confirmed knowledge, a section loses its stamp, the
Markdown stops being byte-stable, the sidecar stops naming its sources, or a gap
disposition stops surviving a map rerun. *Matters because* this is the **Build
Definition of Done** -- v6.1 §4 R1 requires every build to end in a verb an FDE
runs on a real engagement and gets value from that day. *No other instrument
catches it because* every unit test in this suite hands `assemble` values it
constructed; only this one proves the CLI reads a real store, resolves real
freshness and writes real files.

**Two demo lines are S4.2's and are deliberately absent**: `--draft-missing` and
`--format docx`. They are asserted here only in the negative -- the no-model
pack must be complete without them (R3) -- and the sprint that builds them adds
their legs to this file.

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

ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "web" / "fastapi_orders"
SCOPE = "northwind/acme-erp/orders-api/prod"
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}
AUDIENCE = "client_ops"

DOCKERFILE_URI = f"onboard-v1://{SCOPE}/metadata_component/file/Dockerfile"

#: The corpus's one document, tagged for the pack's audience and naming its
#: identity by **canonical URI** -- the one tier that auto-binds (Build 2's H2).
#: A name match would be a review suggestion, so a document that said
#: ``the `Dockerfile` image`` would bind nothing and this journey would assemble
#: an empty runbook while passing every assertion about headings.
REFUND_DOC = f"""---
audience: client_ops
kind: procedure
---
# Refund approvals

The approval step exists on refunds because chargebacks were disputed twice in
the first quarter and the acquirer required a documented human decision before
any money moves back.

The container image `{DOCKERFILE_URI}` carries the approval worker, so a change
to that image is a change to how approvals run.
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
    try:
        parsed: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover -- only on a broken envelope
        pytest.fail(f"stdout was not the JSON envelope:\n{completed.stdout[:2000]}")
    return parsed


def _sql(store: Path, query: str, *args: object) -> list[tuple[Any, ...]]:
    with sqlite3.connect(store) as connection:
        return list(connection.execute(query, args).fetchall())


def _git(*argv: str, cwd: Path) -> None:
    done = subprocess.run(["git", *argv], cwd=str(cwd), check=False, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.fixture
def journey(tmp_path: Path) -> dict[str, Any]:
    """`init` -> `map` -> `ingest`: the state a pack is assembled from.

    The pack is written **outside** the checkout. That is not tidiness: `adopt
    map` walks the repository, so a pack written into it becomes source on the
    next run, and the sidecar's JSON keys are extracted as config identities --
    which then appear as gaps. The demo line writes `--out ./handover`, and a
    real engagement should gitignore it; the journey keeps the two apart so the
    identity count it asserts is the repository's rather than our own output's.
    """
    if not shutil.which("git"):  # pragma: no cover -- every CI runner ships git
        pytest.skip("git is not on PATH, and ingest reads a real checkout")

    checkout = tmp_path / "orders-api"
    shutil.copytree(FIXTURE, checkout)
    (checkout / "docs").mkdir()
    # Written with CRLF **deliberately**: a document from a Windows checkout is
    # the ordinary case (`git` with `core.autocrlf` guarantees it), and writing
    # LF here would make the line-ending assertion below pass for free on Linux
    # -- a measurement with nothing to measure, which this repository has now
    # found eight times.
    (checkout / "docs" / "refunds.md").write_text(REFUND_DOC, encoding="utf-8", newline="\r\n")
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

    ingested = _run("ingest", "docs", "--store", str(store), "--json", cwd=checkout)
    assert ingested.returncode == ExitCode.SUCCESS, ingested.stderr

    return {"checkout": checkout, "store": store, "out": tmp_path / "handover"}


def _pack(journey: dict[str, Any], *extra: str) -> dict[str, Any]:
    completed = _run(
        "pack",
        "--audience",
        AUDIENCE,
        "--out",
        str(journey["out"]),
        "--store",
        str(journey["store"]),
        "--json",
        *extra,
        cwd=journey["checkout"],
    )
    assert completed.returncode == ExitCode.SUCCESS, completed.stderr
    return _payload(completed)


# -- demo line 1: the pack assembles ----------------------------------------


def test_the_pack_renders_every_section_stamped_and_dated(journey: dict[str, Any]) -> None:
    """`adopt pack --audience client_ops --out ./handover`, the demo's first line."""
    payload = _pack(journey)
    document = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")

    # Every section v6.1 names, present whether or not it selected anything.
    for heading in (
        "System overview",
        "Runbook and how-to",
        "Decisions and rationale",
        "Coverage gaps",
        "Observability boundary",
    ):
        assert f"## {heading}" in document, f"the pack has no {heading!r} section"

    # The map's inventory, the boundary statement, and the ingested document.
    assert "Identities mapped:" in document
    assert payload["boundary"] == "declared"
    assert "**Tier:**" in document
    assert "Refund approvals" in document

    # Stamped and dated: every rendered revision carries both.
    runbook = next(row for row in payload["sections"] if row["section"] == "runbook")
    assert runbook["revisions"] == 1
    assert runbook["stamps"], "a rendered section carried no stamp at all"
    assert "*Status:*" in document
    assert "*Dated:*" in document


def test_the_pack_is_byte_stable_across_runs(journey: dict[str, Any]) -> None:
    """v6.1: byte-stable given the same revisions.

    *Fails when* a clock, a set iteration or a dict ordering reaches the output.
    *Matters because* a pack that differs on every run cannot be diffed,
    reviewed or committed to a client's repository -- and the first thing an FDE
    does with a regenerated pack is diff it against the one they sent last week.
    """
    _pack(journey)
    markdown = (journey["out"] / f"{AUDIENCE}.md").read_bytes()
    sidecar = (journey["out"] / f"{AUDIENCE}.lineage.json").read_bytes()

    _pack(journey)

    assert (journey["out"] / f"{AUDIENCE}.md").read_bytes() == markdown
    assert (journey["out"] / f"{AUDIENCE}.lineage.json").read_bytes() == sidecar


def test_the_markdown_uses_lf_endings_on_every_platform(journey: dict[str, Any]) -> None:
    """CRLF is a recorded failure class here; a pack diffed across machines
    must not differ in every line."""
    _pack(journey)
    assert b"\r\n" not in (journey["out"] / f"{AUDIENCE}.md").read_bytes()


def test_the_sidecar_names_the_revisions_each_section_came_from(
    journey: dict[str, Any],
) -> None:
    """Build 8 regenerates a section from this; it must resolve to real rows."""
    _pack(journey)
    sidecar = json.loads((journey["out"] / f"{AUDIENCE}.lineage.json").read_text(encoding="utf-8"))

    assert sidecar["audience"] == AUDIENCE
    runbook = next(row for row in sidecar["sections"] if row["section"] == "runbook")
    assert runbook["revision_ids"], "the runbook cited no revision"

    for revision_id in runbook["revision_ids"]:
        held = _sql(journey["store"], "SELECT id FROM knowledge_revision WHERE id = ?", revision_id)
        assert held, f"the sidecar cites {revision_id}, which is not in the store"
    assert DOCKERFILE_URI in runbook["identity_uris"]


def test_the_pack_needs_no_model_and_calls_none(journey: dict[str, Any]) -> None:
    """R3: the no-model mode is the default and complete.

    Asserted rather than assumed -- no adapter is configured anywhere in this
    journey, so a pack that required one would fail here rather than in the
    field.
    """
    payload = _pack(journey)
    assert payload["sections"], "the pack rendered no sections without a model"
    document = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")
    # An uncovered section says so rather than vanishing: that note *is* the
    # no-model mode, and S4.2's `--draft-missing` is what replaces it.
    assert "No confirmed decision records yet" in document


# -- demo line 5: gap dispositions -------------------------------------------


def test_a_gap_disposition_is_recorded_and_survives_a_map_rerun(
    journey: dict[str, Any],
) -> None:
    """`adopt gaps --ack <gap> --owner alice --note "SME session booked"`.

    *Fails when* a disposition is keyed to something a rerun changes. *Matters
    because* v6.1 requires human dispositions to survive regeneration: an FDE who
    acknowledges forty gaps and loses them on the next `adopt map` will never
    acknowledge one again.
    """
    listed = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    assert listed["gaps"], "the corpus produced no gaps to disposition"
    key = listed["gaps"][0]["gap_key"]

    disposed = _payload(
        _run(
            "gaps",
            "--ack",
            key,
            "--owner",
            "alice",
            "--note",
            "SME session booked",
            "--store",
            str(journey["store"]),
            "--json",
            cwd=journey["checkout"],
        )
    )
    assert disposed["disposed"]["status"] == "acknowledged"
    assert disposed["disposed"]["owner"] == "alice"

    rerun = _run("map", ".", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    assert rerun.returncode == ExitCode.SUCCESS, rerun.stderr

    after = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    survivor = next(row for row in after["gaps"] if row["gap_key"] == key)
    assert survivor["status"] == "acknowledged"
    assert survivor["owner"] == "alice"


def test_a_disposition_reaches_the_pack(journey: dict[str, Any]) -> None:
    """The gap appendix is the join, so what a human decided shows up in it."""
    listed = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    key = listed["gaps"][0]["gap_key"]
    _run(
        "gaps",
        "--ack",
        key,
        "--owner",
        "alice",
        "--note",
        "SME session booked",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )

    _pack(journey)
    document = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")

    assert "acknowledged" in document
    assert "alice" in document
    assert "SME session booked" in document


def test_a_waiver_without_an_expiry_is_refused_at_the_command(
    journey: dict[str, Any],
) -> None:
    """The mandatory-expiry rule, at the surface an operator actually types."""
    listed = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    key = listed["gaps"][0]["gap_key"]

    refused = _run(
        "gaps",
        "--waive",
        key,
        "--store",
        str(journey["store"]),
        cwd=journey["checkout"],
    )

    assert refused.returncode == ExitCode.USAGE_ERROR
    assert "GAP_WAIVER_NEEDS_UNTIL" in refused.stdout + refused.stderr


def test_unverified_knowledge_never_counts_as_coverage(journey: dict[str, Any]) -> None:
    """Build 2's honesty invariant, unchanged by this build.

    *Fails when* a pack's assembly path starts counting what it renders as
    coverage. *Matters because* Build 4 is the first build that reads the
    coverage result for presentation, and a presentation layer that fed itself
    back into the count would make every pack claim more coverage than the store
    has.
    """
    payload = _pack(journey)
    listed = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    assert payload["gaps"] == len(listed["gaps"])
    assert listed["uncovered"] == len(listed["gaps"])
