"""Archetype detection over the fixture corpus, and the four ways it refuses.

*Fails when* a system stops being classified as what it is, when the walk starts
reading what it must not read, or when an ambiguous tree starts being guessed at.
*Matters because* the archetype selects the extractor set for the whole
engagement: a wrong one is not a slightly wrong answer but a different set of
tools pointed at a client system, and nothing downstream can detect that. *No
other instrument catches it because* the determinism property proves two runs
agree without proving either is right, and the CUJs never call detection.

**The corpus is the instrument.** Fifteen systems, three per archetype, plus the
five aggregate directories -- because contracts §14's validation command runs
`adopt detect tests/fixtures/repos/web`, which is a parent of three web systems
and must classify as `web` too.
"""

from pathlib import Path

import pytest

from adopt_const import DETECT_CONFIDENCE_MIN, DETECT_MAX_DEPTH
from adopt_detect import ARCHETYPES, detect, load_rule_sets
from adopt_detect.gitignore import GitignoreFilter
from adopt_detect.rules import ArchetypeRules, Rule
from adopt_obs import AdoptError, ErrorCode

REPOS = Path(__file__).resolve().parent.parent / "fixtures" / "repos"

#: Every system in the corpus and the archetype it must be classified as.
CORPUS: list[tuple[str, str]] = [
    ("web/django_shop", "web"),
    ("web/fastapi_orders", "web"),
    ("web/express_billing", "web"),
    ("platform/salesforce_service", "platform"),
    ("platform/sap_finance", "platform"),
    ("platform/dynamics_crm", "platform"),
    ("lowcode/powerplatform_approvals", "lowcode"),
    ("lowcode/powerapps_inspection", "lowcode"),
    ("lowcode/mendix_claims", "lowcode"),
    ("data/dbt_warehouse", "data"),
    ("data/airflow_pipelines", "data"),
    ("data/looker_semantic", "data"),
    ("ai/langgraph_support", "ai"),
    ("ai/rag_knowledgebase", "ai"),
    ("ai/langchain_assistant", "ai"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("relative", "expected"), CORPUS)
def test_corpus_system_is_classified_as_its_archetype(relative: str, expected: str) -> None:
    result = detect(REPOS / relative)
    assert result.archetype == expected, f"{relative}: ranked {result.ranked()}"
    assert not result.ambiguous
    assert result.confidence >= DETECT_CONFIDENCE_MIN


@pytest.mark.unit
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_aggregate_directory_is_classified_as_its_archetype(archetype: str) -> None:
    """The shape contracts §14's validation command actually points at.

    `tests/fixtures/repos/web` holds three web systems. A rule set that only
    worked on a single-system checkout would pass every row above and fail the
    documented command, which is the one an operator runs.
    """
    result = detect(REPOS / archetype)
    assert result.archetype == archetype, f"{archetype}: ranked {result.ranked()}"


@pytest.mark.unit
def test_a_tree_that_is_two_things_is_ambiguous_rather_than_guessed() -> None:
    """A Django service carrying its own dbt project -- a real monorepo shape.

    PRD F10.3: ambiguity escalates, never guesses. The result must carry the
    ranked scores, because "ambiguous" with no evidence gives an operator nothing
    to act on and they will pick one themselves.
    """
    result = detect(REPOS / "_mixed" / "django_with_dbt")
    assert result.ambiguous
    assert result.archetype is None
    ranked = dict(result.ranked())
    assert ranked["web"] > 0 and ranked["data"] > 0
    assert {hit.rule_id for hit in result.rules_fired} >= {"web.python.manage", "data.dbt.project"}


@pytest.mark.unit
def test_an_empty_tree_is_ambiguous_and_does_not_divide_by_zero(tmp_path: Path) -> None:
    """Zero evidence scores zero everywhere rather than raising.

    The denominator is the weight *matched*, so a tree with no signals has a
    total of zero -- the one input that would make a ratio undefined.
    """
    result = detect(tmp_path)
    assert result.ambiguous
    assert set(result.scores.values()) == {0.0}


@pytest.mark.unit
def test_scores_are_a_distribution_over_the_evidence_found() -> None:
    result = detect(REPOS / "_mixed" / "django_with_dbt")
    assert sum(result.scores.values()) == pytest.approx(1.0)


@pytest.mark.unit
def test_gitignored_paths_do_not_contribute_evidence(tmp_path: Path) -> None:
    """*Fails when* build output starts voting on the archetype.

    A vendored `site-packages` carries every framework marker there is. If
    detection reads it, every client tree looks like every other one -- which is
    the failure `.gitignore` support exists to prevent, not a tidiness concern.
    """
    (tmp_path / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    (tmp_path / "dbt_project.yml").write_text("name: w\n", encoding="utf-8")
    vendored = tmp_path / "vendor" / "django" / "conf"
    vendored.mkdir(parents=True)
    (vendored / "settings.py").write_text("INSTALLED_APPS = []\n", encoding="utf-8")

    result = detect(tmp_path)
    assert {hit.rule_id for hit in result.rules_fired} == {"data.dbt.project"}


@pytest.mark.unit
def test_a_symlink_out_of_the_tree_is_not_followed(tmp_path: Path) -> None:
    """*Fails when* detection reads files the operator never pointed it at.

    Under a `contains` rule this is not only a privacy problem: content outside
    the tree would change the archetype of the tree.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.py").write_text("INSTALLED_APPS = []\n", encoding="utf-8")
    target = tmp_path / "tree"
    target.mkdir()
    (target / "dbt_project.yml").write_text("name: w\n", encoding="utf-8")
    try:
        (target / "escape").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover -- unprivileged Windows
        pytest.skip("this platform does not permit creating a symlink")

    result = detect(target)
    assert {hit.rule_id for hit in result.rules_fired} == {"data.dbt.project"}


@pytest.mark.unit
def test_the_walk_stops_at_the_declared_depth(tmp_path: Path) -> None:
    """*Fails when* the depth bound stops binding on a deep client monorepo."""
    deep = tmp_path
    for level in range(DETECT_MAX_DEPTH + 2):
        deep = deep / f"level{level}"
    deep.mkdir(parents=True)
    (deep / "dbt_project.yml").write_text("name: too-deep\n", encoding="utf-8")

    result = detect(tmp_path)
    assert result.rules_fired == ()


@pytest.mark.unit
def test_an_unreadable_file_never_fires_a_contains_rule(tmp_path: Path) -> None:
    """ "We could not look" is not evidence.

    Asserted through the rule itself rather than by making a file unreadable,
    because file permissions are not portable and the claim is about `matches`.
    """
    rule = Rule(id="r", weight=1, path="**/*.py", why="w", contains="INSTALLED_APPS")
    assert rule.matches("app/settings.py", b"INSTALLED_APPS = []") is True
    assert rule.matches("app/settings.py", None) is False


@pytest.mark.unit
def test_a_path_glob_does_not_cross_a_directory_separator() -> None:
    """`*/settings.py` is a rule about one level, not about anything anywhere."""
    rule = Rule(id="r", weight=1, path="*/settings.py", why="w")
    assert rule.matches("app/settings.py", None) is True
    assert rule.matches("a/b/c/settings.py", None) is False


@pytest.mark.unit
def test_detect_refuses_a_path_that_is_not_a_directory(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(AdoptError) as caught:
        detect(target)
    assert caught.value.code is ErrorCode.ADOPT_CONFIG_UNRESOLVED


@pytest.mark.unit
def test_adding_a_signal_is_a_data_change_and_needs_no_code(tmp_path: Path) -> None:
    """Implementation spec §4.11's actual requirement, asserted rather than assumed.

    *Fails when* the scoring engine grows a branch that only the packaged rules
    satisfy. A rule set built entirely in the test drives the same engine, which
    is what "adding a signal is a data change" has to mean to be worth stating.
    """
    invented = tuple(
        ArchetypeRules(
            archetype=archetype,  # type: ignore[arg-type]
            rules=(Rule(id=f"{archetype}.marker", weight=1, path=f"{archetype}.marker", why="w"),),
        )
        for archetype in ARCHETYPES
    )
    (tmp_path / "lowcode.marker").write_text("", encoding="utf-8")

    result = detect(tmp_path, rule_sets=invented)
    assert result.archetype == "lowcode"
    assert result.confidence == 1.0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ("archetype: web\nrules: []\n", "an archetype with no rules can never be detected"),
        ("archetype: data\nrules:\n  - {id: a, weight: 1, path: p, why: w}\n", "name disagrees"),
        (
            "archetype: web\nrules:\n  - {id: a, weight: 0, path: p, why: w}\n",
            "a zero-weight rule appears as evidence that did not count",
        ),
        (
            "archetype: web\nrules:\n  - {id: a, weight: 1, path: p}\n",
            "a rule nobody can explain cannot be reviewed when it misfires",
        ),
        (
            "archetype: web\nrules:\n  - {id: a, weight: 1, path: p, why: w, nope: 1}\n",
            "an unknown key is a signal silently never firing",
        ),
        (
            "archetype: web\nrules:\n"
            "  - {id: a, weight: 1, path: p, why: w}\n"
            "  - {id: a, weight: 1, path: q, why: w}\n",
            "two rules with one id make rules_fired unreadable",
        ),
        (
            "archetype: web\nrules:\n  - {id: a, weight: 1, path: p, why: w, contains: ''}\n",
            "an empty substring matches every file that exists",
        ),
    ],
)
def test_a_malformed_rule_file_is_refused(tmp_path: Path, document: str, reason: str) -> None:
    """*Fails when* a broken rule set loads and silently scores nothing."""
    for archetype in ARCHETYPES:
        (tmp_path / f"{archetype}.yaml").write_text(
            f"archetype: {archetype}\nrules:\n  - {{id: {archetype}.ok, weight: 1, "
            f"path: marker, why: w}}\n",
            encoding="utf-8",
        )
    (tmp_path / "web.yaml").write_text(document, encoding="utf-8")

    with pytest.raises(AdoptError) as caught:
        load_rule_sets(tmp_path)
    assert caught.value.code is ErrorCode.MANIFEST_INVALID, reason


@pytest.mark.unit
def test_a_missing_rule_file_is_refused(tmp_path: Path) -> None:
    """An archetype with no rule set can never win, which reads in the field as
    "we do not support that" rather than as a missing file."""
    for archetype in ARCHETYPES[:-1]:
        (tmp_path / f"{archetype}.yaml").write_text(
            f"archetype: {archetype}\nrules:\n  - {{id: x, weight: 1, path: p, why: w}}\n",
            encoding="utf-8",
        )
    with pytest.raises(AdoptError) as caught:
        load_rule_sets(tmp_path)
    assert caught.value.code is ErrorCode.MANIFEST_INVALID


@pytest.mark.unit
def test_the_packaged_rules_cover_exactly_the_declared_archetypes() -> None:
    """*Fails when* the manifest gains an archetype and the rules do not."""
    assert tuple(rule_set.archetype for rule_set in load_rule_sets()) == ARCHETYPES


@pytest.mark.unit
@pytest.mark.parametrize(
    ("patterns", "path", "is_dir", "ignored"),
    [
        ("build/\n", "build", True, True),
        ("build/\n", "build/out.js", False, True),
        ("build/\n", "build", False, False),
        ("*.log\n!keep.log\n", "keep.log", False, False),
        ("*.log\n!keep.log\n", "other.log", False, True),
        ("/dist\n", "dist", True, True),
        ("/dist\n", "packages/dist", True, False),
        ("doc/frotz\n", "a/doc/frotz", True, False),
        ("# comment\n\n", "anything", False, False),
    ],
)
def test_gitignore_semantics(patterns: str, path: str, is_dir: bool, ignored: bool) -> None:
    """The subset of git's rules this reader implements, stated as a table.

    *Matters because* the boundary of what is implemented is documented in
    `gitignore.py` prose, and prose that nothing asserts drifts.
    """
    assert GitignoreFilter.from_text(patterns).is_ignored(path, is_dir=is_dir) is ignored
