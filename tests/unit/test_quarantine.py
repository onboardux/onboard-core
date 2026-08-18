"""The quarantine pipeline, the review ledger and the approve refusal -- `04` §6.

Three claims carry this file and each is T1 under `03` §7:

1. **A rejected module is never written.** `04` §6 step 1 is *"DISCARD ... No file
   written"*, and the failure mode is a rejected artefact sitting on disk where a
   reviewer can approve it.
2. **Quarantined facts never reach the store.** `01` F12.3. Asserted structurally
   -- the pipeline is handed no store and no writer -- and then again by looking.
3. **`--approve` is refused on a modified file.** `04` §6, and it is the
   measurement rather than the friction: without it ADR-0.1's rewrite-rate
   trigger can never fire.

`05` S1.7 also asks for *"one per audit rule"*, which is
`test_extractor_audit.py`'s existing parameterization over `AUDIT_RULES` -- the
rules and their tests landed together in S1.3 and are not duplicated here.
"""

import json
from pathlib import Path

import pytest
from adopt_map.agent_gate import GateDecision
from adopt_map.quarantine import (
    QuarantinePaths,
    approve,
    generated_digest,
    quarantine,
    reject,
    rewrite,
)
from adopt_map.review_ledger import (
    REVIEW_OUTCOMES,
    ReviewEntry,
    append,
    m5_rewrite_rate,
    now_iso,
    read_all,
)
from adopt_map.schemas.agent import GlueOutput
from adopt_map.schemas.surface import ExtractorManifest

from adopt_const import MAP_GLUE_REWRITE_ALERT, URI_SCHEME
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_CLEAN = '''"""Clean."""


class Extractor:
    def manifest(self):
        return None

    def applies_to(self, root):
        return False

    def extract(self, ctx):
        return iter(())


EXTRACTOR = Extractor
'''

_ID = "agent.test.routes"


def _manifest() -> ExtractorManifest:
    return ExtractorManifest(
        id=_ID,
        version="0.1.0",
        pack="common",
        archetypes=["web"],
        kinds=["endpoint"],
        method="regex",
    )


def _authored(source: str) -> GlueOutput:
    return GlueOutput(
        outcome="authored",
        extractor_id=_ID,
        module_source=source,
        test_source="def test_x() -> None:\n    assert True\n",
        manifest=_manifest(),
    )


def _run(output: GlueOutput, adopt_dir: Path, root: Path) -> object:
    return quarantine(
        output,
        paths=QuarantinePaths(adopt_dir=adopt_dir),
        root=root,
        samples=(),
        decision=GateDecision(allowed=True),
        prompt_ref="map-glue-001/v1",
        adapter="stub",
        cost_usd=0.0,
    )


def test_a_clean_module_reaches_quarantine_with_its_artefacts(tmp_path: Path) -> None:
    """The positive control for every refusal below, plus `04` §6 steps 2, 4 and 5."""
    adopt_dir = tmp_path / ".adopt"
    paths = QuarantinePaths(adopt_dir=adopt_dir)

    result = _run(_authored(_CLEAN), adopt_dir, tmp_path)

    assert result.status == "quarantined"
    assert result.written
    assert paths.module(_ID).is_file()
    assert paths.test_module(_ID).is_file()
    assert (paths.quarantine_out / "facts.json").is_file()
    assert (paths.quarantine_out / "review.json").is_file()


def test_a_module_carrying_a_forged_uri_is_rejected_and_never_written(tmp_path: Path) -> None:
    """B1-CR-26, planted from the constant rather than from a literal.

    The scheme is composed from `adopt_const.URI_SCHEME` at plant time, so this
    proof follows the constant the day `onboard-v2` is ratified. A test spelling
    `onboard-v1://` would go blind on exactly the day the rule did -- which is the
    defect B1-CR-26 exists to record, arriving in the test instead of the audit.
    """
    adopt_dir = tmp_path / ".adopt"
    forged = f'{_CLEAN}\n_U = "{URI_SCHEME}://f/e/s/prod/endpoint/http/x"\n'

    result = _run(_authored(forged), adopt_dir, tmp_path)

    assert result.status == "rejected"
    assert not result.written
    assert "uri_construction" in result.audit_rules
    assert not QuarantinePaths(adopt_dir=adopt_dir).module(_ID).exists()


def test_a_declined_reply_writes_nothing_and_is_not_a_failure(tmp_path: Path) -> None:
    """`04` §4.2: *"declining is a correct and valued outcome"*."""
    result = _run(
        GlueOutput(outcome="declined", decline_reason="not statically recoverable"),
        tmp_path / ".adopt",
        tmp_path,
    )
    assert result.status == "declined"
    assert not result.written
    assert not (tmp_path / ".adopt" / "extractors").exists()


def test_quarantined_facts_go_to_a_file_and_the_pipeline_has_no_store(tmp_path: Path) -> None:
    """`01` F12.3, asserted twice: by looking, and structurally.

    The structural half is the stronger claim -- `adopt_map.quarantine` imports no
    writer, no port and no store handle, so *"facts never reach the store"* is a
    capability it does not have rather than a rule it follows. A grep is the only
    instrument that can see an absence.
    """
    adopt_dir = tmp_path / ".adopt"
    _run(_authored(_CLEAN), adopt_dir, tmp_path)

    payload = json.loads(
        (QuarantinePaths(adopt_dir=adopt_dir).quarantine_out / "facts.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["extractor_id"] == _ID

    source = Path("packages/adopt-map/src/adopt_map/quarantine.py").read_text(encoding="utf-8")
    for forbidden in ("SurfaceWriter", "RevisionAppender", "import adopt_store", "insert_rows"):
        assert forbidden not in source, (
            f"{forbidden!r} appears in the quarantine pipeline. `01` F12.3 holds "
            "because this module cannot reach a store, not because it chooses not to."
        )


def test_the_review_row_carries_what_04_6_names(tmp_path: Path) -> None:
    """`04` §6 step 5's field list, so a reviewer is not handed a bare fact count."""
    adopt_dir = tmp_path / ".adopt"
    _run(_authored(_CLEAN), adopt_dir, tmp_path)
    review = json.loads(
        (QuarantinePaths(adopt_dir=adopt_dir).quarantine_out / "review.json").read_text(
            encoding="utf-8"
        )
    )
    for field in (
        "extractor_id",
        "sampled_files",
        "fact_count",
        "sample_facts",
        "audit_result",
        "sandbox_result",
        "cost_usd",
        "adapter",
        "prompt_id",
    ):
        assert field in review, f"`04` §6 step 5 names {field!r}"


def test_approve_is_refused_after_an_edit_and_names_rewrite(tmp_path: Path) -> None:
    """`04` §6's refusal, and the sentence a reviewer actually reads.

    The hint is asserted, not just the raising: a refusal that does not say what
    to do instead is one people route around, and routing around it is precisely
    how the rewrite rate stops measuring anything.
    """
    adopt_dir = tmp_path / ".adopt"
    paths = QuarantinePaths(adopt_dir=adopt_dir)
    _run(_authored(_CLEAN), adopt_dir, tmp_path)

    paths.module(_ID).write_text(_CLEAN + "\n# a reviewer's fix\n", encoding="utf-8")

    with pytest.raises(AdoptError) as raised:
        approve(paths, _ID)
    assert raised.value.code is ErrorCode.MAP_USAGE
    assert "--rewrite" in (raised.value.hint or "")


def test_approve_succeeds_on_the_unmodified_module(tmp_path: Path) -> None:
    """The positive control for the refusal above.

    Without it, an `approve` that raised unconditionally -- a digest read that
    always mismatched, a sidecar path typo -- would satisfy the refusal test
    perfectly while making the review queue unusable.
    """
    adopt_dir = tmp_path / ".adopt"
    paths = QuarantinePaths(adopt_dir=adopt_dir)
    _run(_authored(_CLEAN), adopt_dir, tmp_path)

    approve(paths, _ID)

    outcomes = [entry.outcome for entry in read_all(paths.ledger)]
    assert outcomes == ["quarantined", "approved"]


def test_reject_deletes_the_module_and_the_ledger_entry_outlives_it(tmp_path: Path) -> None:
    """`04` §6: *"`--reject` deletes it with a reason"* -- and the reason survives."""
    adopt_dir = tmp_path / ".adopt"
    paths = QuarantinePaths(adopt_dir=adopt_dir)
    _run(_authored(_CLEAN), adopt_dir, tmp_path)

    reject(paths, _ID, reason="wrong family")

    assert not paths.module(_ID).exists()
    last = read_all(paths.ledger)[-1]
    assert (last.outcome, last.reason) == ("rejected", "wrong family")


def test_the_ledger_is_append_only_across_runs(tmp_path: Path) -> None:
    """`04` §6: the ledger *"survives runs and is the source of truth"*.

    Two passes and two decisions produce four lines in file order. A ledger that
    rewrote itself would leave an approval rate nobody can audit -- the same
    pressure the approve refusal exists to resist, one layer down.
    """
    adopt_dir = tmp_path / ".adopt"
    paths = QuarantinePaths(adopt_dir=adopt_dir)
    _run(_authored(_CLEAN), adopt_dir, tmp_path)
    rewrite(paths, _ID, reason="tightened the regex")
    _run(_authored(_CLEAN), adopt_dir, tmp_path)
    approve(paths, _ID)

    assert [entry.outcome for entry in read_all(paths.ledger)] == [
        "quarantined",
        "rewritten",
        "quarantined",
        "approved",
    ]


def test_m5_reads_the_latest_outcome_per_extractor(tmp_path: Path) -> None:
    """`01` §6 M5: *"latest outcome per extractor"*, which arithmetic gets wrong.

    An extractor rewritten once and approved after the rewrite counts **once, as
    approved**. Counting every row would report a rewrite rate that rises every
    time somebody fixes something, which is the opposite of what ADR-0.1 watches.
    """
    ledger = tmp_path / "review_ledger.jsonl"
    for extractor_id, outcome in (
        ("a.one", "rewritten"),
        ("a.one", "approved"),
        ("a.two", "rewritten"),
        ("a.three", "approved"),
    ):
        append(
            ledger,
            ReviewEntry(
                ts=now_iso(), extractor_id=extractor_id, outcome=outcome, module_sha256="x"
            ),
        )

    rate = m5_rewrite_rate(read_all(ledger))
    assert rate == pytest.approx(1 / 3)
    assert rate <= MAP_GLUE_REWRITE_ALERT


def test_m5_is_none_when_nothing_has_been_decided(tmp_path: Path) -> None:
    """Build 0's CR-51 at the metric ADR-0.1 reads.

    An empty ledger and a ledger where everybody approved are different facts, and
    reporting the first as `0.0` would read as *"the glue approach is working"* on
    a run where nobody reviewed anything.
    """
    ledger = tmp_path / "review_ledger.jsonl"
    assert m5_rewrite_rate(read_all(ledger)) is None
    append(
        ledger,
        ReviewEntry(ts=now_iso(), extractor_id="a.one", outcome="quarantined", module_sha256="x"),
    )
    assert m5_rewrite_rate(read_all(ledger)) is None


def test_the_ledger_refuses_an_outcome_outside_its_vocabulary() -> None:
    """`pending` is not a stored outcome, and neither is anything invented."""
    with pytest.raises(ValueError, match="pending"):
        ReviewEntry(ts=now_iso(), extractor_id="a.one", outcome="pending", module_sha256="x")
    assert "pending" not in REVIEW_OUTCOMES


def test_the_generated_digest_is_over_bytes_not_over_a_path(tmp_path: Path) -> None:
    """Two identical sources digest alike; one changed byte does not."""
    assert generated_digest(_CLEAN) == generated_digest(_CLEAN)
    assert generated_digest(_CLEAN) != generated_digest(_CLEAN + " ")
