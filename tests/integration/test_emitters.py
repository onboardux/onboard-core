"""The four artifacts, the first screen, and what may never appear in one.

`02` §9.1's first-screen order is **normative**, and `02` §9.1 says plainly:
*"A degradation or truncation that does not appear in the first screen is a
defect."* That is the sentence this file exists for.

*Defect sentence.* Fails when the first screen reorders, when a degradation or a
budget truncation sinks below the inventory, when `surface.json` stops being
byte-sorted, or when an absolute path or client source reaches an artifact;
matters because this build exists to stop a client discovering six weeks in that
a family was never covered, and burying the honest headline is exactly how that
happens; no other instrument catches it because a buried degradation is still
*present* -- every count is right and the file is complete.

**The ordering assertion is against the module's own table, not a snapshot.** A
golden file alone would let somebody reorder the sections and re-bless the
snapshot in the same commit.
"""

import json
from pathlib import Path

import pytest
from adopt_extractors_common import pack
from adopt_extractors_common.stub import MANIFEST, StubExtractor
from adopt_map.confidence import Degradation
from adopt_map.emit.d2 import render_d2
from adopt_map.emit.json_report import SURFACE_JSON_NAME, render_surface_json, surface_payload
from adopt_map.emit.markdown import FIRST_SCREEN, render_markdown, render_stage1
from adopt_map.emit.mermaid import collapsed, render_mermaid
from adopt_map.orchestrator import run as run_map
from adopt_map.plugins import ExtractorRegistry
from adopt_map.report import RunResult, absolute_paths_in
from adopt_map.scheduler import ExtractorOutcome
from adopt_map.writer import FactBatch

from adopt_const import MAP_DIAGRAM_MAX_NODES, SURFACE_REPORT_VERSION
from tests.build1_conftest import build_scoped_store, context_for, surface_writer_for

pytestmark = pytest.mark.integration

_TREE = Path("fixtures/repos/poisoned-import")


@pytest.fixture
def result(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> RunResult:
    """One real run, through the orchestrator, into a real store."""
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("emit"))
    registry = ExtractorRegistry()
    registry.register_all(pack())
    return run_map(
        resolved=scopes["prod"],
        root=_TREE,
        registry=registry,
        adopt_version="0.3.0",
        writer=surface_writer_for(handle),
        coverage_records=handle.coverage_records(),
        cache=handle.backend,
        out_dir=tmp_path / "out",
        formats=("md", "json", "mermaid", "d2"),
        sequential=True,
    )


def _headings(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def test_the_first_screen_is_in_the_normative_order(result: RunResult) -> None:
    """`02` §9.1's eight items, in order, before the inventory."""
    headings = _headings(render_markdown(result))
    assert len(headings) == len(FIRST_SCREEN) + 1
    for position, heading in enumerate(FIRST_SCREEN, start=1):
        assert headings[position - 1] == f"## {position}. {heading}"
    assert headings[-1].endswith("Inventory")


def test_a_degradation_appears_in_the_first_screen(result: RunResult) -> None:
    """`05` S1.3's Final Output Validation line 7, and `02` §9.1's defect rule.

    The degradation is forced rather than hoped for: a ladder transition is put
    on the result directly, so the assertion is about **where it renders**, not
    about whether this particular tree happens to degrade.
    """
    degraded = _with(
        result,
        degradations=(
            Degradation(
                kind="symbol",
                language="kotlin",
                from_method="grammar",
                to_method="regex",
                reason="grammar_unavailable",
                affected=142,
            ),
        ),
    )
    markdown = render_markdown(degraded)
    body = markdown.split("## 8. Inventory")[0]
    assert "kotlin" in body, "the degradation is below the inventory"
    assert "grammar_unavailable" in body
    assert "142" in body


def test_a_budget_truncation_appears_in_the_first_screen(result: RunResult) -> None:
    """Truncation is the other half of `02` §9.1's rule, and the more damaging
    one: a truncated map is short of families the run intended to cover."""
    truncated = _with(result, truncated_families=("db_field", "job"))
    body = render_markdown(truncated).split("## 8. Inventory")[0]
    assert "Truncated by budget" in body
    assert "db_field" in body and "job" in body
    assert "does not claim otherwise" in body


def test_a_sampling_disclosure_appears_in_the_first_screen(result: RunResult) -> None:
    """A sampled run that reads as a complete one is the most expensive claim
    this build can make, so the disclosure is on the honest headline."""
    from dataclasses import replace

    sampled = _with(result, index=replace(result.index, sampled=True, discovered=100_000))
    body = render_markdown(sampled).split("## 8. Inventory")[0]
    assert "SAMPLED" in body
    assert "100000" in body


def test_the_stage1_map_says_it_is_partial(result: RunResult) -> None:
    """`01` F11.2. A stage-1 artifact that did not say it was partial would be
    indistinguishable from a complete one that happened to find less."""
    stage1 = render_stage1(result)
    assert "stage-1 map" in stage1
    assert "Still running" in stage1
    assert "claims to be a complete inventory" in stage1
    # The same normative first screen, so the reader who stops at minute fifteen
    # gets the honest headline rather than a preview of it.
    assert (
        _headings(stage1)[: len(FIRST_SCREEN)]
        == _headings(render_markdown(result))[: len(FIRST_SCREEN)]
    )


def test_surface_json_facts_are_byte_sorted_by_uri(result: RunResult) -> None:
    """`02` §10 C15. Sorted on encoded **bytes**, because Python's string
    comparison and byte order differ for non-ASCII keys and `01` N4 makes the
    artifact's bytes part of what determinism means."""
    payload = surface_payload(result)
    uris = [fact["identity_uri"] for fact in payload["facts"]]
    assert uris == sorted(uris, key=lambda value: str(value).encode("utf-8"))
    assert len(uris) > 1, "one fact cannot demonstrate an order"


def test_surface_json_carries_every_02_field(result: RunResult) -> None:
    """The `02` §9.2 shape, field by field. An absent key is a broken contract
    for an integrator's `jq`, even when the value would have been empty."""
    payload = surface_payload(result)
    for key in (
        "report_version",
        "run_id",
        "generated_at",
        "scope",
        "system",
        "toolchain",
        "counts_by_kind",
        "coverage",
        "revisions_written",
        "degradations",
        "outside_vcs",
        "moves",
        "conflicts",
        "truncated_families",
        "gaps",
        "facts",
    ):
        assert key in payload, f"surface.json is missing {key}"
    assert payload["report_version"] == SURFACE_REPORT_VERSION
    assert payload["coverage"]["source"] == "recompute"


def test_two_renders_of_one_result_are_byte_identical(result: RunResult) -> None:
    """`01` N4, at the artifact. The volatile fields are the run id and the
    timestamp, and both are properties of the result rather than of the render."""
    assert render_surface_json(result) == render_surface_json(result)
    assert render_markdown(result) == render_markdown(result)
    assert render_mermaid(result) == render_mermaid(result)
    assert render_d2(result) == render_d2(result)


def test_no_absolute_path_reaches_any_artifact(result: RunResult, tmp_path: Path) -> None:
    """`02` §9.3 and `01` N16, asserted over the **rendered bytes**.

    The run root is an absolute path and the result holds it; no artifact may.
    Checked structurally over the report payload and by substring over the text
    artifacts, because "no absolute path" is a property of what was written.
    """
    assert absolute_paths_in(result.as_report()) == ()
    root = str(_TREE.resolve())
    for rendered in (
        render_markdown(result),
        render_surface_json(result),
        render_mermaid(result),
        render_d2(result),
        json.dumps(result.as_report()),
    ):
        assert root not in rendered
        assert str(tmp_path) not in rendered


def test_the_run_report_carries_no_client_source_content(result: RunResult) -> None:
    """`02` §9.3: *"paths, symbol names, counts and timings only"*.

    The fixture's modules contain a distinctive line of body text. Symbol names
    are permitted and expected; the **bodies** they came from are not.
    """
    report = json.dumps(result.as_report())
    assert "metrics registered" not in report
    assert "a client module was imported" not in report


def test_a_diagram_collapses_above_the_node_threshold_with_a_stated_notice(
    result: RunResult,
) -> None:
    """`01` F11.5. Collapse, never truncation: a diagram that silently drew the
    first 300 of 3,000 nodes would be a picture of an arbitrary tenth of a system
    presented as a picture of the system."""
    facts = tuple(StubExtractor().extract(context_for(".")))
    many = _with(
        result,
        batches=(
            FactBatch(manifest=MANIFEST, facts=facts * (MAP_DIAGRAM_MAX_NODES // len(facts) + 2)),
        ),
    )
    assert collapsed(many) is True
    mermaid = render_mermaid(many)
    assert "Collapsed" in mermaid
    assert str(MAP_DIAGRAM_MAX_NODES) in mermaid
    assert "Collapsed" in render_d2(many)
    # And below the threshold it does not collapse, so the notice means something.
    assert collapsed(result) is False
    assert "Collapsed" not in render_mermaid(result)


def test_the_artifacts_the_orchestrator_wrote_are_on_disk(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`--format` selects artifacts; the run report is written regardless.

    Making the report optional would make every NFR claim optional with it.
    """
    handle, scopes = build_scoped_store(tmp_path_factory.mktemp("emit-disk"))
    registry = ExtractorRegistry()
    registry.register_all(pack())
    out = tmp_path / "out"
    run_map(
        resolved=scopes["prod"],
        root=_TREE,
        registry=registry,
        adopt_version="0.3.0",
        writer=surface_writer_for(handle),
        coverage_records=handle.coverage_records(),
        cache=handle.backend,
        out_dir=out,
        formats=("json",),
        sequential=True,
    )
    assert (out / SURFACE_JSON_NAME).is_file()
    assert (out / "run_report.json").is_file()
    assert (out / "telemetry.jsonl").is_file()
    assert not (out / "surface.d2").exists(), "--format did not select"
    # `surface.md` exists even though `md` was not selected: `01` F11.2's stage-1
    # artifact is a promise about the run, not about the format flag.
    assert (out / "surface.md").is_file()


def test_the_coverage_block_names_why_nothing_is_covered(result: RunResult) -> None:
    """B1-CR-62 on the first screen.

    A bare `0.0%` is the one coverage figure a reader cannot act on. The reason
    is what turns it into a finding somebody can take to the Build 5 owner.
    """
    body = render_markdown(result).split("## 8. Inventory")[0]
    assert "Uncovered because" in body
    assert "audience_or_environment_inapplicable" in body


def _with(result: RunResult, **changes: object) -> RunResult:
    from dataclasses import replace

    return replace(result, **changes)  # type: ignore[arg-type]


def test_a_failed_extractor_appears_in_the_first_screen_with_its_cause(
    result: RunResult,
) -> None:
    """B1-CR-97 -- a failed extractor was invisible everywhere a human looks.

    *Fails when* an extractor raises and the map says nothing about it. *Matters
    because* the run still exits 0 -- `02` section 8's *"Complete"* -- while
    holding fewer identities than the run before it, so an operator reads an
    absence as evidence that a system has no surface of that kind. *No other
    instrument catches it because* the degradations block one section above is the
    **ladder's**: it reports a family that dropped a rung, not a plugin that threw,
    and the two are different events with different remedies.

    Found by the S1.8 soak on `saleor`: `common.secrets` succeeded on the first run
    and failed on the second over an unchanged tree, and the second map lost an
    identity with nothing anywhere saying so.
    """
    failed = _with(
        result,
        outcomes=(
            ExtractorOutcome(
                extractor_id="common.secrets",
                status="failed",
                facts=(),
                elapsed_s=0.7,
                detail="UnicodeDecodeError",
            ),
        ),
    )

    markdown = render_markdown(failed)
    body = markdown.split("## 8. Inventory")[0]
    assert "common.secrets" in body, "a failed extractor is below the inventory"
    assert "UnicodeDecodeError" in body, "the failure is reported without its cause"

    # The positive control: a run with nothing failed emits no callout at all. A
    # standing "every extractor succeeded" line is one readers learn to skip, and
    # without this half the assertion above would pass on an emitter that printed
    # the block unconditionally.
    assert "extractor(s) failed" not in render_markdown(result)


def test_the_run_report_keeps_the_cause_of_a_failed_extractor(result: RunResult) -> None:
    """`02` section 9.3's `detail`, which was computed and dropped until S1.8.

    *Fails when* `run_report.json` records that an extractor failed without
    recording why. *Matters because* the report is the only artefact that survives
    the run, and an intermittent failure diagnosed from it is the difference
    between a bug report and a shrug. *No other instrument catches it because*
    `run_extractor` classifies the cause correctly -- the loss happened one layer
    later, in the projection, where every test asserting on `status` still passed.
    """
    failed = _with(
        result,
        outcomes=(
            ExtractorOutcome(
                extractor_id="common.secrets",
                status="failed",
                facts=(),
                elapsed_s=0.7,
                detail="UnicodeDecodeError",
            ),
        ),
    )

    rows = failed.as_report()["extractors"]
    assert isinstance(rows, list)
    assert rows[0]["detail"] == "UnicodeDecodeError"

    # `02` section 9.3 is "no client source content": the cause is a type name or a
    # registered error code, never a message, because an exception's `str()` is the
    # one field that routinely carries the line it choked on.
    assert " " not in str(rows[0]["detail"])
