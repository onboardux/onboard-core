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

**All five demo lines run here** as of S4.2. The drafting leg drives the real
`Runner` against the recorded fake adapter (kind `test`), so the seam, the
prompt, the grounding check and the whole write path are exercised and only the
model's reply is scripted -- no network, no credential, no provider. The DOCX
leg skips when pandoc is absent, which is the honest treatment of a tool the CI
job installs and a laptop does not.

**The no-model assertions stay.** R3 makes a pack with no adapter a complete
product, so the tests that prove it run in the same file as the ones that prove
drafting works -- the two are not alternatives, and a change that made drafting
mandatory would go red here rather than in the field.

**Every step goes through the CLI as a subprocess**, the same entry-point module
the release binary compiles (CR-56), and assertions read the store file directly
rather than through `adopt_store`, so one bug cannot both write the wrong row and
vouch for it.
"""

import json
import os
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


def test_a_scoped_re_render_matches_the_full_pack_and_writes_beside_it(
    journey: dict[str, Any],
) -> None:
    """Build 8's demo line, through the CLI: *sections regenerate scoped*.

    *Fails when* `--sections` renders bytes a full pack does not contain, or
    when it overwrites the pack it was meant to patch. *Matters because* an FDE
    resolving a review item re-renders one section and hands the result to a
    client beside the pack it came from -- two documents that disagree about the
    same knowledge is the failure the byte contract exists to prevent, and
    losing the full pack to a fragment is the failure the separate filename
    exists to prevent. *No other instrument catches it because* the unit test
    proves the library contract over a constructed pack, and only this one
    proves the command wires the flag to it and puts the file somewhere safe.
    """
    from adopt_handover import parse_sidecar, sections_affected

    _pack(journey)
    full = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")
    sidecar = parse_sidecar(
        (journey["out"] / f"{AUDIENCE}.lineage.json").read_text(encoding="utf-8")
    )

    selected = sections_affected(sidecar, [], [DOCKERFILE_URI])
    assert selected == ("runbook",), f"the sidecar selected {selected!r}"

    payload = _pack(journey, "--sections", ",".join(selected))
    assert payload["rendered_sections"] == ["runbook"]
    assert payload["sidecar"] is None, "a fragment must not claim to be a pack's lineage"

    fragment_path = journey["out"] / f"{AUDIENCE}.sections.md"
    fragment = fragment_path.read_text(encoding="utf-8")
    assert fragment.rstrip("\n") in full, "the scoped bytes are not the full pack bytes"
    assert b"\r\n" not in fragment_path.read_bytes()

    # The pack itself is untouched, and the fragment is genuinely smaller.
    assert (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8") == full
    assert "Coverage gaps" not in fragment
    assert len(fragment) < len(full)


def test_sections_with_no_names_is_refused_rather_than_rendering_everything(
    journey: dict[str, Any],
) -> None:
    """An empty selection must not silently mean the whole pack.

    *Fails when* `--sections ,,` falls back to a full render. *Matters because*
    that is exactly how scoped regeneration becomes whole-pack regeneration
    while every output stays correct and nobody notices the feature is gone.
    """
    completed = _run(
        "pack",
        "--audience",
        AUDIENCE,
        "--out",
        str(journey["out"]),
        "--store",
        str(journey["store"]),
        "--sections",
        " , ",
        cwd=journey["checkout"],
    )
    assert completed.returncode != ExitCode.SUCCESS
    assert not (journey["out"] / f"{AUDIENCE}.sections.md").exists()


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


def test_a_captured_answer_reaches_the_pack_and_closes_its_gap(
    journey: dict[str, Any],
) -> None:
    """The capture ratchet's output is a deliverable, not just a store row.

    *Fails when* `adopt answer` writes an item with no `audience_tag` row.
    *Matters because* two readers filter on that tag and both go quiet rather
    than loud: `recompute_coverage` treats an untagged item as inapplicable, so
    the gap the answer just closed stays open, and `sections.select` filters on
    the audience, so the pack's "Answers to common questions" section renders
    its empty note -- an answer captured, bound, confirmed, and invisible in
    both places a human would look for it. *No other instrument catches it
    because* no test carried a capture through to a pack: the ask journey stops
    at the re-ask serving KNOWN, this journey never captured anything, and both
    are right about what they assert.

    **Both halves are asserted here** because the default is what the defect
    was: a capture with no `--audience` must still be counted, and a capture
    that names this pack's audience must still render into it.
    """
    # The fixture's one endpoint, and deliberately **not** the Dockerfile:
    # `REFUND_DOC` already binds that one by canonical URI, so covering it again
    # would leave the coverage count unchanged and this test would pass whether
    # or not the capture was counted.
    uri = _payload(
        _run(
            "identity",
            "build",
            "--scope",
            SCOPE,
            "--kind",
            "endpoint",
            "--key",
            "POST /v1/orders",
            "--json",
            cwd=journey["checkout"],
        )
    )["uri"]

    before = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )

    asked = _run(
        "ask",
        "who signs off a refund above the threshold?",
        "--escalate",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert asked.returncode == ExitCode.SUCCESS, asked.stderr
    escalation_id = _payload(asked)["escalation_id"]

    captured = _run(
        "answer",
        escalation_id,
        "--text",
        "The finance approver on duty signs off any refund above the threshold.",
        "--uri",
        uri,
        "--audience",
        AUDIENCE,
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert captured.returncode == ExitCode.SUCCESS, captured.stderr
    assert _payload(captured)["binding_ids"], "the capture bound nothing, so it covers nothing"

    after = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    assert after["covered"] == before["covered"] + 1, (
        "the captured answer was bound to a mapped identity and still did not cover it: "
        f"{before['covered']} -> {after['covered']}"
    )

    payload = _pack(journey)
    answers = next(section for section in payload["sections"] if section["section"] == "answers")
    assert answers["revisions"] == 1, "the captured answer rendered into no section"
    rendered = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")
    assert "The finance approver on duty" in rendered


# -- demo lines 2-4: drafting, review, derived format ------------------------


def _fake_adapter(journey: dict[str, Any], *turns: dict[str, Any]) -> dict[str, str]:
    """Environment for a run whose model is the recorded fake.

    The whole configuration is three variables and a file: no credential, no
    network, no provider. `ADOPT_OFFLINE` stays at its default -- the fake is
    adapter kind `test`, which the seam permits offline precisely so a journey
    like this one can exercise the door without opening it.
    """
    endpoint = journey["checkout"].parent / "recorded.json"
    endpoint.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "text": json.dumps(turn),
                        "tool_calls": [],
                        "input_tokens": 10,
                        "output_tokens": 5,
                    }
                    for turn in turns
                ]
            }
        ),
        encoding="utf-8",
    )
    return {
        "ADOPT_ADAPTER": "fake_recorded",
        "ADOPT_ADAPTER_ENDPOINT": str(endpoint),
        "ADOPT_PROMPTS_DIR": str(Path(__file__).resolve().parents[2] / "prompts"),
    }


def _run_with(env: dict[str, str], *argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={**os.environ, **env},
    )


def _first_gap_uri(journey: dict[str, Any]) -> str:
    listed = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    return str(listed["gaps"][0]["uri"])


def _draft_missing(
    journey: dict[str, Any], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return _run_with(
        env,
        "pack",
        "--audience",
        AUDIENCE,
        "--out",
        str(journey["out"]),
        "--store",
        str(journey["store"]),
        "--draft-missing",
        "--json",
        cwd=journey["checkout"],
    )


def test_draft_missing_lands_unverified_knowledge_the_pack_banners(
    journey: dict[str, Any],
) -> None:
    """`adopt pack --audience client_ops --draft-missing`, the demo's second line.

    *Fails when* drafting stops writing, stops binding, or -- worst -- writes
    something the pack then renders without its banner. *Matters because* this
    is H3's whole claim: the human never authors from blank, **and** a client
    can never mistake a draft for verified truth. *No other instrument catches
    it because* the unit tests hand `assemble` values they built; only this one
    proves the CLI drafts through the real seam and renders what it wrote.
    """
    uri = _first_gap_uri(journey)
    env = _fake_adapter(
        journey,
        {"body_md": "This is drafted from what the map observed.", "cited_facts": [uri]},
    )

    drafted = _draft_missing(journey, env)
    assert drafted.returncode == ExitCode.SUCCESS, drafted.stderr
    payload = _payload(drafted)

    assert payload["drafting"]["available"] is True
    assert payload["drafting"]["drafted"] == 1, payload["drafting"]["outcomes"][:4]

    # Landed unverified and authored, read straight out of the store file.
    rows = _sql(
        journey["store"],
        "SELECT verification, authority_class FROM knowledge_revision "
        "WHERE created_by_actor_id = 'adopt-draft'",
    )
    assert rows == [("unverified", "human_confirmed")]

    # Bound to its identity, so a change to that identity stales the draft.
    bound = _sql(
        journey["store"],
        "SELECT COUNT(*) FROM binding b JOIN knowledge_revision kr ON kr.item_id = b.item_id "
        "WHERE kr.created_by_actor_id = 'adopt-draft'",
    )
    assert bound[0][0] == 1

    # And rendered with the banner. This is the assertion the build exists for.
    document = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")
    assert "UNVERIFIED" in document
    assert "This is drafted from what the map observed." in document
    assert "*Status:* **unverified**" in document


def test_an_ungrounded_draft_is_discarded_and_the_store_is_untouched(
    journey: dict[str, Any],
) -> None:
    """Invariant #7, through the command an FDE actually types.

    *Fails when* a draft citing nothing still reaches the store. *Matters
    because* every other assertion about drafting assumes the discard holds; if
    it does not, the product writes a model's unsourced prose into a client's
    handover pack and stamps it. *No other instrument catches it because* the
    command exits zero and reports a pack either way.
    """
    env = _fake_adapter(journey, {"body_md": "Orders are processed nightly.", "cited_facts": []})

    completed = _draft_missing(journey, env)
    assert completed.returncode == ExitCode.SUCCESS, completed.stderr

    assert _payload(completed)["drafting"]["drafted"] == 0
    assert (
        _sql(
            journey["store"],
            "SELECT COUNT(*) FROM knowledge_revision WHERE created_by_actor_id = 'adopt-draft'",
        )[0][0]
        == 0
    )


def test_draft_missing_without_an_adapter_still_writes_a_complete_pack(
    journey: dict[str, Any],
) -> None:
    """R3, at its sharpest: the flag is passed and no model exists.

    *Fails when* `--draft-missing` becomes an error without an adapter. *Matters
    because* R3 makes the no-model mode complete rather than degraded -- a
    capability that turned into a failure would make the model a dependency of
    the handover pack, which is the thing v6.1 forbids outright. *No other
    instrument catches it because* every drafting test configures an adapter.
    """
    payload = _pack(journey, "--draft-missing")

    assert payload["drafting"]["available"] is False
    assert payload["drafting"]["drafted"] == 0
    document = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")
    assert document.startswith("# Handover pack")


def test_a_confirmed_draft_upgrades_to_confirmed_and_fresh(journey: dict[str, Any]) -> None:
    """`adopt review` -> confirm, the demo's third line.

    *Fails when* confirming a draft leaves it unverified, or when the next pack
    still banners it. *Matters because* the confirm is the only thing in this
    product that turns generated text into canon (D11), and the pack is where
    that promotion becomes visible to a client. *No other instrument catches it
    because* the review command reports `confirmed` whatever the revision says.
    """
    uri = _first_gap_uri(journey)
    env = _fake_adapter(journey, {"body_md": "Drafted from the map.", "cited_facts": [uri]})
    assert _draft_missing(journey, env).returncode == ExitCode.SUCCESS

    queue = _payload(
        _run("review", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    drafts = [row for row in queue["queue"] if row["source"] == "draft"]
    assert len(drafts) == 1, "the draft did not appear in the one review queue"

    confirmed = _run(
        "review",
        "--confirm",
        drafts[0]["review_item"],
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert confirmed.returncode == ExitCode.SUCCESS, confirmed.stderr

    after = _pack(journey)
    document = (journey["out"] / f"{AUDIENCE}.md").read_text(encoding="utf-8")
    assert "Drafted from the map." in document
    runbook = next(row for row in after["sections"] if row["section"] == "runbook")
    assert runbook["unverified"] == 0, "a confirmed draft still rendered as unverified"
    assert "fresh" in runbook["stamps"]


def test_adopt_draft_drafts_one_named_identity(journey: dict[str, Any]) -> None:
    """`adopt draft <uri>` -- the single-target door onto the same pass.

    *Fails when* the verb drafts the wrong identity, or drafts nothing. *Matters
    because* the bulk pass is ranked and capped, and an FDE who knows which
    endpoint needs writing up should not have to wait for it to come up the
    queue. *No other instrument catches it because* `--draft-missing` would keep
    passing with this verb entirely broken.
    """
    uri = _first_gap_uri(journey)
    env = _fake_adapter(journey, {"body_md": "One section, on request.", "cited_facts": [uri]})

    completed = _run_with(
        env,
        "draft",
        uri,
        "--audience",
        AUDIENCE,
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )
    assert completed.returncode == ExitCode.SUCCESS, completed.stderr

    payload = _payload(completed)
    assert payload["drafted"] == 1
    assert payload["outcomes"][0]["uri"] == uri


def test_adopt_draft_refuses_an_unmapped_uri(journey: dict[str, Any]) -> None:
    """A URI no identity carries is a usage error, not an empty success."""
    completed = _run(
        "draft",
        f"onboard-v1://{SCOPE}/endpoint/-/GET %2Fnope",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )

    assert completed.returncode == ExitCode.USAGE_ERROR
    assert "BIND_TARGET_NOT_FOUND" in completed.stdout + completed.stderr


def test_drafting_changes_no_coverage_number(journey: dict[str, Any]) -> None:
    """Build 2's honesty invariant against the build most likely to break it.

    *Fails when* an unverified draft starts counting as coverage. *Matters
    because* drafting writes a bound knowledge item for every gap it touches --
    the exact shape coverage counts -- so the only thing keeping `adopt gaps`
    honest is the verification filter. If it slipped, one `--draft-missing`
    would close every gap in the report without a human reading a word. *No
    other instrument catches it because* both numbers would agree with each
    other and with the store; they would just be false.
    """
    before = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    uri = str(before["gaps"][0]["uri"])
    env = _fake_adapter(journey, {"body_md": "Drafted.", "cited_facts": [uri]})
    assert _draft_missing(journey, env).returncode == ExitCode.SUCCESS

    after = _payload(
        _run("gaps", "--store", str(journey["store"]), "--json", cwd=journey["checkout"])
    )
    assert after["uncovered"] == before["uncovered"]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
def test_the_docx_is_content_equivalent(journey: dict[str, Any]) -> None:
    """`adopt pack --audience client_ops --format docx`, the demo's fourth line.

    Structural, not byte-wise: v6.1 says derived formats are content-equivalent,
    and two pandoc releases produce different bytes from one input.
    """
    payload = _pack(journey, "--format", "docx")
    target = Path(payload["derived"])

    assert target.exists()
    assert payload["derived_with"], "the converter version was not recorded"
    assert (journey["out"] / f"{AUDIENCE}.md").exists(), "the canonical pack was not written"


def test_an_unknown_format_is_refused_before_anything_is_written(
    journey: dict[str, Any],
) -> None:
    """*Fails when* a bad `--format` is discovered after the pack is on disk.
    *Matters because* the refusal is only useful before the side effect: an
    operator who mistypes should get a message, not a directory that looks
    half-finished."""
    completed = _run(
        "pack",
        "--audience",
        AUDIENCE,
        "--out",
        str(journey["out"] / "epub"),
        "--format",
        "epub",
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["checkout"],
    )

    assert completed.returncode == ExitCode.USAGE_ERROR
    assert "PACK_RENDERER_MISSING" in completed.stdout + completed.stderr
    assert not (journey["out"] / "epub").exists()
