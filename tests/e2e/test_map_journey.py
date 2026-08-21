"""The Build 1 demo journey, run verbatim on two real repositories.

*Fails when* any line of v6.1 §6's Build 1 demo stops working end to end: the
map exits non-zero, an extractor raises, the recall floor drops an entry, a
second run writes, the report loses its provenance, or the bundle stops
carrying identities. *Matters because* this is the **Build Definition of Done**
-- v6.1 §4 R1 makes every build end in a verb an FDE runs on a real engagement,
and this is that verb, on real code, with the numbers recorded. *No other
instrument catches it because* every unit fixture in this suite is a tree we
wrote to be extractable, and S1.1's four worst defects -- the map walking its
own store, router prefixes silently dropped, zero environment variables from a
modern FastAPI app, invisible bare-annotated columns -- each produced a
well-formed map, exit `0`, and a plausible identity count on exactly such a
fixture.

**The repositories are cloned, never vendored**, at the commits `repo.json`
records. The pin is asserted rather than assumed: a curated URI list is true of
one tree, and the same list run against a later commit either fails for reasons
that are not defects or passes while measuring a repository nobody curated.

**A skip here is not evidence of anything.** These tests skip when the clones
are absent, which is the ordinary state of a developer machine; the
`map-journey` CI job is what makes them run, and `test_ci_gates` asserts that
job still exists.
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from _pytest.outcomes import Failed, Skipped

from adopt_obs import ExitCode

#: Where the `map-journey` job puts the clones. One variable, because a test
#: that silently found half the repositories would report half a journey as a
#: whole one.
REFERENCE_ROOT_ENV = "ADOPT_REFERENCE_REPO_ROOT"

#: Set by the `map-journey` job. **Turns every skip below into a failure**, and
#: it exists because a skipped Definition of Done is indistinguishable from a
#: met one in a green run -- CR-67's `0/0 covered (100%)` in a different
#: costume. The rule lives here rather than in the workflow on purpose: the
#: first version of it *was* shell, counting `pytest --collect-only -q` output
#: with `grep -c "::test_"`, and `-q` prints a per-file summary rather than
#: test ids, so it counted zero and failed a run whose journey had just passed
#: on both repositories. A gate written as shell is a gate nobody writes a
#: negative test for -- `test_the_required_flag_turns_a_skip_into_a_failure` is
#: that test.
REFERENCE_REQUIRED_ENV = "ADOPT_REFERENCE_REPOS_REQUIRED"

REFERENCE_DIR = Path(__file__).resolve().parents[1] / "reference"
ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)

#: The three qualification answers (`04`/detect §7a). Written beside the store
#: rather than inside the mapped tree: it is our scaffolding, not the client's
#: repository, and a file we created appearing in the client's inventory is the
#: same class of mistake as the map walking its own `.adopt/` store.
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}


@dataclass(frozen=True)
class Reference:
    """One pinned reference repository and its curated expectations."""

    slug: str
    url: str
    commit: str
    archetype: str
    scope: str
    checkout: Path

    @property
    def expected_list(self) -> Path:
        return REFERENCE_DIR / self.slug / "expected-identities.txt"


def _references() -> list[Reference]:
    root = os.environ.get(REFERENCE_ROOT_ENV)
    if not root:
        return []
    found: list[Reference] = []
    for manifest in sorted(REFERENCE_DIR.glob("*/repo.json")):
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        found.append(
            Reference(
                slug=declared["slug"],
                url=declared["url"],
                commit=declared["commit"],
                archetype=declared["archetype"],
                scope=declared["scope"],
                checkout=Path(root) / declared["slug"],
            )
        )
    return found


REFERENCES = _references()
_IDS = [reference.slug for reference in REFERENCES] or ["none"]


def _required() -> bool:
    """Whether a missing checkout is a failure rather than a skip."""
    return os.environ.get(REFERENCE_REQUIRED_ENV, "").strip().lower() in {"1", "true", "yes"}


def _absent(reason: str) -> None:
    """Skip on a developer machine; **fail** wherever the journey is required."""
    if _required():
        pytest.fail(
            f"{reason} -- and {REFERENCE_REQUIRED_ENV} is set, so this is a failure "
            "rather than a skip. The Build 1 demo is this build's Definition of Done; "
            "a run that skipped it is indistinguishable from a run that met it."
        )
    pytest.skip(f"{reason}; the map-journey CI job supplies it")


@pytest.fixture(params=REFERENCES or [None], ids=_IDS)
def reference(request: pytest.FixtureRequest) -> Reference:
    """A pinned checkout, or a skip naming exactly what is missing."""
    subject = request.param
    if subject is None:
        _absent(f"{REFERENCE_ROOT_ENV} is unset")
    if not subject.checkout.is_dir():
        _absent(f"{subject.slug} is not checked out at {subject.checkout}")
    return subject


@pytest.mark.e2e
def test_the_required_flag_turns_a_skip_into_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The anti-skip rule, watched failing.

    *Fails when* `ADOPT_REFERENCE_REPOS_REQUIRED` stops converting a missing
    checkout into a failure. *Matters because* this is the whole of what makes
    the `map-journey` job evidence: without it a job whose clones failed reports
    seven green skips and a passing run, which is exactly CR-67's
    `0/0 covered (100%)`. *No other instrument catches it because* on every
    healthy run the flag changes nothing -- the clones are present, nothing
    skips, and the rule is never exercised.
    """
    monkeypatch.setenv(REFERENCE_REQUIRED_ENV, "1")

    with pytest.raises(Failed, match=REFERENCE_REQUIRED_ENV):
        _absent("a checkout that is not there")

    monkeypatch.setenv(REFERENCE_REQUIRED_ENV, "0")
    with pytest.raises(Skipped):
        _absent("a checkout that is not there")


def _run(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """One CLI invocation, through the same module the release binary compiles."""
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """The JSON envelope, or a failure naming what came out instead."""
    try:
        parsed: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover -- only on a broken envelope
        pytest.fail(f"stdout was not the JSON envelope:\n{completed.stdout[:2000]}")
    return parsed


@pytest.fixture
def journey(reference: Reference, tmp_path: Path) -> dict[str, Any]:
    """`adopt init` then the whole demo, run once per repository.

    One fixture for the whole sequence rather than one test per line: the demo
    **is** a sequence, and its most valuable assertion -- that a second run
    writes nothing -- only exists relative to the first.
    """
    assert_pinned(reference)
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

    common = (str(reference.checkout), "--store", str(store), "--json")
    first = _run("map", *common, cwd=tmp_path)
    checked = _run("map", *common, "--check-expected", str(reference.expected_list), cwd=tmp_path)
    second = _run("map", *common, cwd=tmp_path)
    reported = _run("map", *common, "--report", cwd=tmp_path)
    return {
        "reference": reference,
        "store": store,
        "tmp": tmp_path,
        "first": first,
        "checked": checked,
        "second": second,
        "reported": reported,
    }


def assert_pinned(reference: Reference) -> None:
    """Refuse a checkout that is not at the commit the curated list describes."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(reference.checkout),
        check=False,
        capture_output=True,
        text=True,
    )
    assert head.returncode == 0, head.stderr
    assert head.stdout.strip() == reference.commit, (
        f"{reference.slug} is at {head.stdout.strip()}, not the pinned "
        f"{reference.commit}. The expected-identities list is curated against one "
        "tree; run against another it either fails for reasons that are not "
        "defects, or passes while measuring a repository nobody curated."
    )


@pytest.mark.e2e
def test_the_map_exits_zero_and_no_extractor_failed(journey: dict[str, Any]) -> None:
    """Line 2 of the demo, plus B-08's rule.

    *Fails when* `adopt map` exits non-zero on a real repository, or any
    extractor raised. *Matters because* B-08 cost days: on a 930k-line tree one
    extractor failed on one run of two, the run exited `0`, and the only trace
    was one fewer identity. *No other instrument catches it because* a silently
    smaller map and a genuinely smaller system are indistinguishable from
    outside, so only an explicit outcome-per-extractor assertion can tell them
    apart.
    """
    first = journey["first"]
    assert first.returncode == ExitCode.SUCCESS, first.stderr
    payload = _payload(first)
    assert payload["failed"] == [], payload["extractors"]
    assert payload["identities_seen"] > 0
    assert all(outcome["status"] == "ok" for outcome in payload["extractors"])


@pytest.mark.e2e
def test_every_curated_identity_is_present(journey: dict[str, Any]) -> None:
    """Invariant #1 -- the map recall floor (v6.1 §6 H1, R6).

    *Fails when* an identity an FDE would name from memory is absent from the
    map. *Matters because* this is the **only** critical semantic invariant v6.1
    assigns Build 1, and it is the one measure of extraction quality that cannot
    be improved by shrinking a denominator. *No other instrument catches it
    because* every count this build reports goes **up** when an extractor gets
    noisier and stays flat when one goes blind on a whole category -- as the AI
    pack's first run did, yielding zero environment variables from an app that
    reads a dozen.
    """
    checked = journey["checked"]
    payload = _payload(checked)["expected"]
    assert payload["missing"] == [], payload["findings"]
    assert payload["checked"] == payload["present"]
    assert payload["checked"] > 0, "an empty list passes every check"
    assert checked.returncode == ExitCode.SUCCESS, checked.stderr


@pytest.mark.e2e
def test_a_planted_miss_fails_naming_it_and_exits_four(journey: dict[str, Any]) -> None:
    """The recall floor is watched failing, not merely watched passing.

    *Fails when* `--check-expected` reports success for a URI that is not in the
    store, or exits with anything but `4`. *Matters because* a gate nobody has
    seen fail is a gate nobody should trust -- and this one has a specific way
    of being wrong that looks exactly like success: an expected list that fails
    to load reports a perfect recall floor over nothing, which is CR-67's
    `0/0 covered (100%)` in a different costume. *No other instrument catches it
    because* the green case above passes identically whether the check works or
    does nothing at all.
    """
    reference: Reference = journey["reference"]
    planted = journey["tmp"] / "planted-expected.txt"
    absent = f"onboard-v1://{reference.scope}/endpoint/-/GET%20%2Fthis-endpoint-does-not-exist"
    planted.write_text(
        reference.expected_list.read_text(encoding="utf-8") + absent + "\n", encoding="utf-8"
    )

    result = _run(
        "map",
        str(reference.checkout),
        "--store",
        str(journey["store"]),
        "--json",
        "--check-expected",
        str(planted),
        cwd=journey["tmp"],
    )

    assert result.returncode == ExitCode.DEGRADED_WITH_FINDINGS, result.stderr
    payload = _payload(result)["expected"]
    assert payload["missing"] == [absent]
    assert payload["findings"] == [{"code": "MAP_EXPECTED_IDENTITY_MISSING", "uri": absent}]


@pytest.mark.e2e
def test_a_second_run_writes_no_identity_and_no_revision(journey: dict[str, Any]) -> None:
    """Line 4 of the demo: idempotence, measured against the store.

    *Fails when* a re-run over an unchanged tree creates an identity or appends
    a revision. *Matters because* every later build's change detection reads
    this chain, and a chain that grows once per scan is a log of how often
    somebody ran the tool rather than a record of what changed. *No other
    instrument catches it because* the observation stream is identical either
    way -- the same 204 observations produce 197 identities or 394, and only the
    rows can say which.
    """
    second = journey["second"]
    assert second.returncode == ExitCode.SUCCESS, second.stderr

    identities, revisions = _row_counts(journey["store"])
    first = _payload(journey["first"])
    assert identities <= first["identities_seen"]
    assert revisions == identities, (
        "one revision per identity after four runs; anything more means a re-observation appended"
    )
    assert _payload(second)["moves"] == []
    assert _payload(second)["absent"] == []


@pytest.mark.e2e
def test_the_report_renders_from_the_store_with_provenance(journey: dict[str, Any]) -> None:
    """Line 5 of the demo: counts by kind, a provenance listing, and reach.

    *Fails when* `--report` loses its per-kind counts, its walked-but-unmapped
    number, or the provenance on a listed row. *Matters because* a report built
    from the run's own observation stream agrees with itself by construction and
    says nothing; the value is that these numbers come from the rows every later
    build queries. *No other instrument catches it because* the run payload
    already carries the walk's counts, so a report that quietly restated them
    would look complete.
    """
    reported = journey["reported"]
    assert reported.returncode == ExitCode.SUCCESS, reported.stderr
    payload = _payload(reported)

    identities, _ = _row_counts(journey["store"])
    assert payload["identities"] == identities
    assert sum(payload["counts_by_kind"].values()) == identities
    assert payload["files_walked"] > payload["files_unmapped"] >= 0
    for row in payload["listing"]:
        assert row["extractor"], row
        assert row["extractor_version"], row
        assert ":" in str(row["source_ref"]), row
    assert payload["listing"] == sorted(
        payload["listing"], key=lambda row: (row["kind"], row["uri"])
    )


@pytest.mark.e2e
def test_identities_travel_in_the_export_bundle(journey: dict[str, Any]) -> None:
    """Line 6 of the demo: what was mapped leaves in the standard bundle.

    *Fails when* `adopt export` refuses a store `adopt map` filled, or emits a
    bundle whose identity table does not carry the mapped rows. *Matters because*
    a map that cannot leave the machine that made it is not a handover artefact.
    *No other instrument catches it because* `golden-g0` proves the bundle is
    byte-stable over a **fixture** store, and no fixture store has ever been
    filled by a command.
    """
    bundle = journey["tmp"] / "bundle"
    exported = _run(
        "export",
        str(bundle),
        "--store",
        str(journey["store"]),
        "--json",
        cwd=journey["tmp"],
    )
    assert exported.returncode == ExitCode.SUCCESS, exported.stderr

    identities, _ = _row_counts(journey["store"])
    table = bundle / "tables" / "identity.ndjson"
    assert table.is_file(), sorted(p.name for p in bundle.rglob("*"))
    lines = [line for line in table.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == identities
    assert all(json.loads(line)["uri"].startswith("onboard-v1://") for line in lines)


@pytest.mark.e2e
def test_a_moved_file_is_recorded_as_a_move_and_the_old_uri_survives(
    journey: dict[str, Any],
) -> None:
    """Line 7 of the sprint's scope: a move, on a real tree (plan decision D6).

    *Fails when* moving a mapped file mints an unrelated new identity and leaves
    the old one looking deleted. *Matters because* every binding, probe and
    piece of bound knowledge in every later build hangs off the URI, and a move
    that is not recorded as one silently detaches all of them -- permanently,
    since a URI is never rewritten. *No other instrument catches it because* the
    unit test for `detect_moves` is a pure function over two lists, and it
    cannot show that the attributes a real extractor emits are stable enough
    across a move for the pairing to be found at all -- which they were not
    until `generic.files_of_interest` stopped putting the path in its own
    digest.
    """
    reference: Reference = journey["reference"]
    source = reference.checkout / "README.md"
    if not source.is_file():
        pytest.skip("this reference repository has no README.md to move")

    moved_dir = reference.checkout / "docs-moved"
    moved_dir.mkdir(exist_ok=True)
    destination = moved_dir / "README.md"
    shutil.move(str(source), str(destination))
    try:
        result = _run(
            "map",
            str(reference.checkout),
            "--store",
            str(journey["store"]),
            "--json",
            cwd=journey["tmp"],
        )
        assert result.returncode == ExitCode.SUCCESS, result.stderr
        payload = _payload(result)
        moves = payload["moves"]
        assert len(moves) == 1, payload
        assert moves[0]["from"].endswith("/README.md")
        assert moves[0]["to"].endswith("/docs-moved/README.md")
    finally:
        shutil.move(str(destination), str(source))
        moved_dir.rmdir()

    old, new = moves[0]["from"], moves[0]["to"]
    aliases = _alias_chain(journey["store"], old)
    assert aliases, f"{old} has no moved revision"
    assert _uri_of(journey["store"], aliases[0]) == new
    assert _uri_of(journey["store"], _identity_id(journey["store"], old)) == old, (
        "the old URI was rewritten; it must stay resolvable forever"
    )


def _row_counts(store: Path) -> tuple[int, int]:
    """`(identities, revisions)` straight from the file, no facade involved.

    Deliberately not through `adopt_store`: the point of an end-to-end test is to
    check what the command *left behind*, and asking the same library that wrote
    it would make one bug capable of hiding itself.
    """
    import sqlite3

    with sqlite3.connect(store) as connection:
        identities = connection.execute("SELECT COUNT(*) FROM identity").fetchone()[0]
        revisions = connection.execute("SELECT COUNT(*) FROM identity_revision").fetchone()[0]
    return int(identities), int(revisions)


def _identity_id(store: Path, uri: str) -> str:
    import sqlite3

    with sqlite3.connect(store) as connection:
        row = connection.execute("SELECT id FROM identity WHERE uri = ?", (uri,)).fetchone()
    assert row is not None, f"no identity at {uri}"
    return str(row[0])


def _alias_chain(store: Path, uri: str) -> list[str]:
    import sqlite3

    with sqlite3.connect(store) as connection:
        rows = connection.execute(
            "SELECT r.alias_of_identity_id FROM identity_revision r "
            "JOIN identity i ON i.id = r.identity_id "
            "WHERE i.uri = ? AND r.status = 'moved'",
            (uri,),
        ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def _uri_of(store: Path, identity_id: str) -> str:
    import sqlite3

    with sqlite3.connect(store) as connection:
        row = connection.execute("SELECT uri FROM identity WHERE id = ?", (identity_id,)).fetchone()
    assert row is not None, f"no identity {identity_id}"
    return str(row[0])
