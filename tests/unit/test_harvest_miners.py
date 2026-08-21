"""The six signals, candidate construction, and the taxonomy drift alarm.

`mine` is pure, so these drive it with `Commit` values directly. The subprocess
half is `test_gitlog`'s subject and is not re-tested here: one instrument per
question, and the question here is *what qualifies as a decision*.
"""

import re
from pathlib import Path

import pytest
from adopt_knowledge.gitlog import Commit
from adopt_knowledge.harvest import (
    CONFIG_SUFFIXES,
    DEPENDENCY_MANIFESTS,
    NOT_CONFIG,
    SIGNAL_CONFIG,
    SIGNAL_DECISION_RECORD,
    SIGNAL_DEPENDENCY,
    SIGNAL_MERGE,
    SIGNAL_RATIONALE,
    SIGNAL_REVERT,
    batch_key,
    decision_record_titles,
    mine,
    signals_of,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _commit(
    sha: str = "a" * 40,
    subject: str = "Do a thing",
    body: str = "",
    parents: tuple[str, ...] = ("b" * 40,),
    files: tuple[str, ...] = (),
) -> Commit:
    return Commit(
        sha=sha,
        parents=parents,
        authored_at="2026-08-21T09:00:00+00:00",
        subject=subject,
        body=body,
        files=files,
    )


def test_a_commit_with_no_signal_is_not_a_candidate() -> None:
    """The rule that keeps the queue worth opening.

    *Fails when* every commit becomes a candidate. *Matters because* a review
    queue holding one item per commit in a release is a queue a reviewer closes
    without reading, and everything Build 2 builds after this depends on that
    queue being opened. *No other instrument catches it because* the store
    accepts four hundred unverified items perfectly happily -- coverage stays
    honest, no gate goes red, and the only symptom is a person deciding the
    tool is noise.
    """
    subjectless = _commit(subject="fix typo", body="", files=("src/app/util.py",))

    assert signals_of(subjectless) == ()
    assert mine([subjectless]) == ()


def test_each_signal_fires_on_its_own_evidence_and_only_on_it() -> None:
    """All six miners, each shown a commit that carries exactly it.

    *Fails when* a miner stops firing, or fires on a commit that does not carry
    its evidence. *Matters because* the signal is the whole of why a candidate
    exists; a miner that silently stops leaves a class of decision unmined, and
    the run still exits `0` with a plausible number. *No other instrument
    catches it because* candidates from five working miners look exactly like
    candidates from six.
    """
    cases = {
        SIGNAL_RATIONALE: _commit(body="We did it this way because the provider settles late."),
        SIGNAL_MERGE: _commit(parents=("b" * 40, "c" * 40)),
        SIGNAL_REVERT: _commit(subject='Revert "Add the approval step"'),
        SIGNAL_DEPENDENCY: _commit(files=("pyproject.toml",)),
        SIGNAL_CONFIG: _commit(files=("deploy/settings.toml",)),
        SIGNAL_DECISION_RECORD: _commit(files=("docs/adr/0007-use-postgres.md",)),
    }

    for name, commit in cases.items():
        assert name in {signal.name for signal in signals_of(commit)}, name

    bare = _commit()
    assert signals_of(bare) == (), "a one-line commit touching nothing carries no signal"


def test_a_revert_is_found_in_the_body_as_well_as_the_subject() -> None:
    """git writes the reversal in two places, and only one is the subject.

    *Fails when* `This reverts commit ...` in a body stops qualifying.
    *Matters because* a squash or a rebase routinely rewrites the subject while
    leaving git's own body sentence intact -- and a reversal is the single
    strongest evidence in this build that a decision was made and then
    unmade. *No other instrument catches it because* the subject-prefix case
    passes identically, and it is the case every hand-written fixture uses.
    """
    rewritten = _commit(
        subject="Restore the previous refund flow (#412)",
        body="This reverts commit deadbeefdeadbeefdeadbeefdeadbeefdeadbeef.",
    )

    assert SIGNAL_REVERT in {signal.name for signal in signals_of(rewritten)}


def test_a_dependency_manifest_is_not_also_counted_as_configuration() -> None:
    """*Fails when* `pyproject.toml` fires both the dependency and config miners.

    *Matters because* the signals are what a reviewer reads to decide whether a
    commit is worth their attention, and one file reported as two independent
    reasons overstates the evidence. It is also exactly the rule Build 1's
    `ConfigKeyExtractor` encodes -- somebody else's manifest is not this
    system's configuration -- and two answers to one question is drift. *No
    other instrument catches it because* the candidate is created either way,
    with the same bindings and the same body.
    """
    names = {signal.name for signal in signals_of(_commit(files=("pyproject.toml",)))}

    assert SIGNAL_DEPENDENCY in names
    assert SIGNAL_CONFIG not in names
    assert SIGNAL_CONFIG not in {
        signal.name for signal in signals_of(_commit(files=("package-lock.json",)))
    }


def test_a_decision_record_is_a_path_segment_not_a_substring() -> None:
    """*Fails when* the ADR miner matches `adr` anywhere in a path.

    *Matters because* a signal that fires on `src/quadratic.py` or
    `lib/padrino/` is a signal a reviewer stops believing, and the queue's only
    resource is their attention. *No other instrument catches it because* the
    true positives keep passing -- the coincidental ones simply add candidates
    nobody can tell apart from real ones without opening the file.
    """
    fires = {signal.name for signal in signals_of(_commit(files=("adr/0001-choose-db.md",)))}
    coincidence = {signal.name for signal in signals_of(_commit(files=("src/padrino/app.rb",)))}

    assert SIGNAL_DECISION_RECORD in fires
    assert SIGNAL_DECISION_RECORD not in coincidence
    assert SIGNAL_DECISION_RECORD in {
        signal.name for signal in signals_of(_commit(files=("CHANGELOG.md",)))
    }


def test_one_commit_with_several_signals_is_still_one_candidate() -> None:
    """*Fails when* a commit yields one candidate per signal.

    *Matters because* the candidate is keyed on its sha for idempotence -- two
    candidates sharing one sha would make a re-harvest unable to tell which it
    had already written, and it would create the missing one every run forever.
    *No other instrument catches it because* the first harvest looks correct:
    the rows are well-formed, the bindings are right, and only the *second* run
    reveals it.
    """
    busy = _commit(
        subject='Revert "Pin the client library"',
        body="This reverts commit " + "f" * 40 + ".\n\nThe pin broke the build.",
        parents=("b" * 40, "c" * 40),
        files=("pyproject.toml", "docs/adr/0009-pinning.md", "deploy/app.toml"),
    )

    candidates = mine([busy])

    assert len(candidates) == 1
    assert len(candidates[0].signals) >= 5
    assert candidates[0].sha == busy.sha


def test_the_body_carries_the_authors_words_and_nothing_this_code_wrote() -> None:
    """The `artifact_observed` claim, asserted rather than intended.

    *Fails when* `mine` puts a signal summary, a file list or a "harvested by"
    banner into `body_md`. *Matters because* the first revision claims
    `artifact_observed` -- that this text was read out of the client's own
    artifact -- and one sentence we wrote makes that claim false in the field
    Build 3's `adopt ask` will cite to a client. *No other instrument catches
    it because* a helpful summary in the body improves every review-queue
    reading of this feature while quietly breaking its provenance.
    """
    commit = _commit(
        subject="Hold refunds for approval",
        body="The provider settles asynchronously.",
        files=("pyproject.toml", "docs/adr/0009-pinning.md"),
    )

    body = mine([commit])[0].body_md

    assert body == "Hold refunds for approval\n\nThe provider settles asynchronously.\n"
    assert "pyproject.toml" not in body
    assert "signal" not in body.lower()
    assert "harvest" not in body.lower()


def test_an_adr_heading_beats_the_subject_that_added_it(tmp_path: Path) -> None:
    """*Fails when* a decision record's own heading stops becoming the title.

    *Matters because* the title is the only thing a reviewer sees in the queue
    listing, and "Add ADR 0007" tells them nothing while "Use Postgres for the
    primary store" tells them everything. *No other instrument catches it
    because* the fallback title is always present and always plausible, so the
    feature failing looks identical to a repository with no ADRs in the range.
    """
    record = tmp_path / "docs" / "adr" / "0007-postgres.md"
    record.parent.mkdir(parents=True)
    record.write_text(
        "---\nstatus: accepted\n---\n\n# Use Postgres for the primary store\n", encoding="utf-8"
    )
    commit = _commit(subject="Add ADR 0007", files=("docs/adr/0007-postgres.md",))

    titles = decision_record_titles(tmp_path, commit.files)
    candidate = mine([commit], decision_titles=titles)[0]

    assert candidate.title == "Use Postgres for the primary store"
    assert mine([commit])[0].title == "Add ADR 0007", "no titles supplied -- the subject stands"


def test_a_decision_record_that_is_gone_falls_back_rather_than_failing(tmp_path: Path) -> None:
    """*Fails when* a deleted ADR raises instead of yielding no title.

    *Matters because* a file added and later removed inside one range is
    ordinary, and a harvest that dies on it mines nothing at all for that range
    -- turning a cosmetic title improvement into a total refusal. *No other
    instrument catches it because* every ADR in a fixture repository exists.
    """
    assert decision_record_titles(tmp_path, ["docs/adr/never-existed.md"]) == {}


def test_the_batch_key_names_the_range_it_covers() -> None:
    """*Fails when* two harvests of two ranges produce one key.

    *Matters because* `batch_key` is the queue's coalescing key: a key that does
    not name its range makes a re-harvest of the same range indistinguishable
    from a harvest of a new one, and Build 6's change batches and Build 8's
    managed batches share this table. *No other instrument catches it because*
    any string opens a batch successfully.
    """
    assert batch_key("v1.2.0", "a" * 40) == f"harvest:v1.2.0..{'a' * 40}"
    assert batch_key("v1.2.0", "a" * 40) != batch_key("v1.3.0", "a" * 40)


def test_the_dependency_taxonomy_still_matches_the_pack_that_owns_it() -> None:
    """The drift alarm on a list this module restates rather than imports.

    *Fails when* Build 1's `generic.DependencyExtractor` learns a manifest
    harvest does not know, or forgets one it does. *Matters because*
    `adopt_knowledge` deliberately does not depend on `adopt-map` -- that edge
    would pull the extractor machinery into the harvest import graph for three
    filenames -- and a restated list with no alarm on it drifts silently: a
    `go.mod` change would stop being a dependency decision, and nothing would
    say so. *No other instrument catches it because* both lists are internally
    consistent and every test of either passes with them disagreeing.
    """
    source = (REPO_ROOT / "packages/adopt-map/src/adopt_map/packs/generic.py").read_text(
        encoding="utf-8"
    )
    body = source.split("class DependencyExtractor", 1)[1].split("\nclass ", 1)[0]
    named = set(re.findall(r'iter_named\("([^"]+)"\)', body))

    assert named, "the extractor stopped naming its manifests -- this alarm went blind"
    assert named == set(DEPENDENCY_MANIFESTS), (
        f"harvest knows {sorted(DEPENDENCY_MANIFESTS)}; the pack names {sorted(named)}"
    )


def test_the_config_taxonomy_still_matches_the_pack_that_owns_it() -> None:
    """The same alarm for `generic.ConfigKeyExtractor`'s exclusion list.

    *Fails when* the pack's `_NOT_CONFIG` set changes and harvest's copy does
    not. *Matters because* the two answer one question -- is this file the
    system's configuration -- and two answers is exactly the drift the one-canon
    rule exists to prevent. *No other instrument catches it because* this set is
    an exclusion: a stale copy makes harvest fire a config signal on somebody
    else's manifest, which reads as a slightly noisy queue rather than as a bug.
    """
    from adopt_map.packs.generic import ConfigKeyExtractor

    assert set(ConfigKeyExtractor._NOT_CONFIG) == set(NOT_CONFIG)
    assert frozenset({".toml", ".json"}) == CONFIG_SUFFIXES
