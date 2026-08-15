"""The eight `02` §7 obligations, over **every registered extractor** -- C13.

`03` §5.10: *"The eight extractor obligations are verified once by a shared
conformance suite parameterized over every registered extractor -- not rewritten
per extractor. That single suite is worth more than a hundred bespoke extractor
tests."*

The parameterization is over **every shipped pack**, so a new extractor is
covered the day it is registered and an extractor removed from a pack stops being
claimed as covered. A suite with a hand-written list would drift from the registry
the first time somebody added a pack in a hurry, and every later sprint would
inherit the gap.

> **B1-CR-69 -- this suite was parameterized over the `common` pack alone, and its
> own anti-vacuity guard could not see it.** `_EXTRACTORS` was `list(pack())` for
> `adopt_extractors_common`, and `test_the_suite_covers_every_registered_extractor`
> built a registry from *that same pack* and compared the two -- true by
> construction, blind to every other pack, and green on the day S1.4 shipped
> eleven web extractors that no obligation test ever ran against. `05` S1.4's
> Final Output Validation line reads *"the conformance suite auto-covers all
> eleven web extractors' eight obligations"*, and **B1-CR-63 repaired three
> sprints' validation lines on exactly that premise**: *"the conformance suite is
> parameterized over every registered extractor, so a pack is covered the moment
> it registers"*. The command passed; the claim attached to it did not hold, and
> registering the `ai` pack would have inherited the same blindness.
>
> The guard now builds its registry from `PACKS` -- the same tuple the
> parameterization reads -- and additionally asserts a **per-pack floor**, because
> a guard comparing one list against another list derived from it is the shape of
> the defect rather than the fix. A pack whose extractors all vanish fails the
> floor; a pack that is never added to `PACKS` fails `test_every_shipped_pack_is_parameterized`,
> which reads the workspace's `adopt-extractors-*` directories rather than a list
> anybody maintains here.

*Defect sentence.* Fails when any registered extractor breaks one of the eight
obligations; matters because the obligations are what let the framework own
scope, confidence and URIs -- an extractor that violates one can fork an identity,
promote its own confidence or reach a client's environment; no other instrument
catches it because a violating extractor still produces facts that look fine in
isolation.
"""

import ast
import inspect
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final, NamedTuple

import pytest
from adopt_extractors_ai import pack as ai_pack
from adopt_extractors_common import pack as common_pack
from adopt_extractors_data import pack as data_pack
from adopt_extractors_lowcode import pack as lowcode_pack
from adopt_extractors_platform import pack as platform_pack
from adopt_extractors_web import pack as web_pack
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.plugins import audit_source
from adopt_map.schemas import Extractor, SurfaceFact

from adopt_model._enums import Archetype

pytestmark = [pytest.mark.conformance, pytest.mark.integration]

#: Where the workspace keeps its packs. Read from the filesystem rather than
#: restated, so a pack added without a `PACKS` row fails
#: `test_every_shipped_pack_is_parameterized` instead of being silently uncovered
#: -- which is the whole of B1-CR-69.
_PACKAGES_DIR: Final[Path] = Path("packages")


class Pack(NamedTuple):
    """One shipped pack, and the tree its obligations are exercised against.

    **The tree is per pack on purpose.** Every obligation below is satisfied
    perfectly by an extractor that emits nothing, so running the `web` pack
    against a tree with no routes would assert eight obligations over an empty
    sequence eight times. Each pack therefore meets a fixture with something of
    its own shape in it, and `test_each_pack_emits_something_over_its_tree` is the
    control that says so.
    """

    name: str
    extractors: tuple[Extractor, ...]
    tree: Path
    archetype: Archetype


#: Every pack, its extractors and its fixture tree.
#:
#: `common` meets `poisoned-import`: Python declarations, a dotenv with an
#: ordinary key and a credential, and a config file -- plus a module that writes
#: a file on import, which the audit reads without ever executing.
PACKS: Final[tuple[Pack, ...]] = (
    Pack("common", tuple(common_pack()), Path("fixtures/repos/poisoned-import"), "web"),
    Pack("web", tuple(web_pack()), Path("fixtures/repos/django-orders"), "web"),
    Pack("ai", tuple(ai_pack()), Path("fixtures/repos/langgraph-support"), "ai"),
    # S1.6. `platform` and `lowcode` meet **export bundles** rather than source
    # trees, which is what those two archetypes have instead of one -- the tree
    # here is exactly what `--export-bundle` is handed on a real run.
    Pack("platform", tuple(platform_pack()), Path("fixtures/repos/sf-metadata-bundle"), "platform"),
    Pack("lowcode", tuple(lowcode_pack()), Path("fixtures/repos/powerapps-export"), "lowcode"),
    Pack("data", tuple(data_pack()), Path("fixtures/repos/dbt-warehouse"), "data"),
)

_EXTRACTORS = [extractor for entry in PACKS for extractor in entry.extractors]
_IDS = [extractor.manifest().id for extractor in _EXTRACTORS]
_TREE_OF = {extractor.manifest().id: entry for entry in PACKS for extractor in entry.extractors}


def _context_for(extractor: Extractor, *, exhausted: bool = False) -> ExtractorContext:
    entry = _TREE_OF[extractor.manifest().id]
    start = time.time() - 10_000 if exhausted else time.time()
    span = 1.0 if exhausted else 3_600.0
    return ExtractorContext(
        root=str(entry.tree),
        index=build_index(entry.tree),
        budget=Budget.starting_at(start, stage1_s=span, total_s=span),
        archetype=entry.archetype,
        tier="T2",
    )


def _source_of(extractor: Extractor) -> str:
    module = inspect.getmodule(extractor)
    assert module is not None and module.__file__ is not None
    return Path(module.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_1_static_only(extractor: Extractor) -> None:
    """No import of client code, no `exec`/`eval`, no subprocess, no network.

    Asserted by running the **production audit** over the extractor's own source
    rather than by a second rule list here. Two lists are two definitions of
    static-only, and the one the shipped audit uses is the one that matters.
    """
    findings = audit_source(_source_of(extractor), declared_kinds=extractor.manifest().kinds)
    assert findings == (), f"{extractor.manifest().id} fails its own static audit: {findings}"


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_2_emits_facts_and_never_writes(extractor: Extractor) -> None:
    """An extractor yields `SurfaceFact`s; the writer writes.

    Checked structurally: the module names no revision helper, no facade and no
    store. It is an absence rather than a mock, because an extractor handed a
    real store would pass a test that only watched a fake one.
    """
    source = _source_of(extractor)
    for forbidden in ("append_revision", "create_item", "RevisionWriter", "adopt_store", "sqlite3"):
        assert forbidden not in source, f"{extractor.manifest().id} reaches {forbidden}"


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_3_deterministic(extractor: Extractor) -> None:
    """Two runs over one tree emit one sequence.

    Run twice against separate contexts, so a cached index cannot make the second
    run agree for the wrong reason. No wall clock and no randomness is asserted
    separately, by source scan, because an extractor could use either once and
    still agree on two consecutive runs.
    """
    first = [_identity(fact) for fact in extractor.extract(_context_for(extractor))]
    second = [_identity(fact) for fact in extractor.extract(_context_for(extractor))]
    assert first == second

    source = _source_of(extractor)
    for forbidden in ("random.", "datetime.now", "time.time", "uuid4"):
        assert forbidden not in source, f"{extractor.manifest().id} uses {forbidden}"

    # `02` §7 obligation 3 forbids *"reliance on set/dict iteration"*, which is a
    # narrower thing than using a set. A `seen: set[str]` consulted with `in` is
    # a membership test and is perfectly deterministic; **iterating** one is what
    # makes emission order depend on hash seeding. So the check is on the
    # iteration, found in the AST, rather than on the substring `set()` -- which
    # would ban the safe use and teach the next author to work around the rule.
    iterated = _sets_iterated(ast.parse(source))
    assert iterated == [], (
        f"{extractor.manifest().id} iterates a set at line(s) {iterated}: emission "
        "order would depend on hash seeding"
    )


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_4_kind_bounded(extractor: Extractor) -> None:
    """Every emitted kind is in the manifest. A hard error otherwise.

    Enforced at the writer too; asserted here because an extractor that widened
    its vocabulary would otherwise only be caught on a tree that happened to
    trigger the extra kind.
    """
    declared = set(extractor.manifest().kinds)
    emitted = {fact.identity_kind for fact in extractor.extract(_context_for(extractor))}
    assert emitted <= declared, f"{extractor.manifest().id} emitted {emitted - declared}"


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_5_confidence_free(extractor: Extractor) -> None:
    """The framework assigns confidence. `SurfaceFact` has no field for it.

    Both halves: the model carries no `confidence`, and the source never names
    one. The first makes it unrepresentable; the second catches an extractor
    trying to smuggle it through `attributes`.
    """
    assert "confidence" not in SurfaceFact.model_fields
    tree = ast.parse(_source_of(extractor))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            names = {keyword.arg for keyword in node.keywords}
            assert "confidence" not in names, f"{extractor.manifest().id} sets its own confidence"


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_6_scope_blind(extractor: Extractor) -> None:
    """No scope is read or set, and no URI is minted.

    **This is the mechanism behind environment isolation** (`01` F6.2): a fuzzed
    extractor cannot emit a production URI from a staging run because it has no
    field, no builder and no scope through which to name one.
    """
    source = _source_of(extractor)
    for forbidden in ("build_uri", "ResolvedScope", "environment_id", "firm_id"):
        assert forbidden not in source, f"{extractor.manifest().id} reaches {forbidden}"
    for fact in extractor.extract(_context_for(extractor)):
        assert not hasattr(fact, "uri")


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_7_budget_aware(extractor: Extractor) -> None:
    """`ctx.budget.check()` at least once, proven by an exhausted budget.

    A source scan would pass an extractor that called `check()` after its loop.
    Running it against an already-elapsed budget proves the call is reached --
    and the extractor either raises `MAP_BUDGET_EXHAUSTED` or emits nothing at
    all, which is the honest alternative for one that has no files to read.
    """
    from adopt_obs import AdoptError, ErrorCode

    ctx = _context_for(extractor, exhausted=True)
    try:
        emitted = list(extractor.extract(ctx))
    except AdoptError as error:
        assert error.code is ErrorCode.MAP_BUDGET_EXHAUSTED
        return
    assert emitted == [], (
        f"{extractor.manifest().id} finished a full extraction against an elapsed "
        "budget: it never checked"
    )


@pytest.mark.parametrize("extractor", _EXTRACTORS, ids=_IDS)
def test_obligation_8_failure_local(extractor: Extractor) -> None:
    """An exception is caught, recorded, and does not abort the run.

    The obligation is the **scheduler's** to keep, so it is asserted through the
    scheduler: the extractor runs beside one that always raises, and both
    outcomes come back.
    """
    from adopt_map.scheduler import run_all

    from tests.fixtures.extractors import CrashingExtractor

    result = run_all([extractor, CrashingExtractor()], _context_for(extractor), sequential=True)
    statuses = {outcome.extractor_id: outcome.status for outcome in result.outcomes}
    assert statuses["common.crashing"] == "failed"
    assert statuses[extractor.manifest().id] in {"ok", "truncated"}


def test_the_suite_covers_every_registered_extractor() -> None:
    """The parameterization is the registry, not a list.

    Without this, `_EXTRACTORS` could be filtered to one extractor and every test
    above would still pass -- the shape of vacuous gate this repository has found
    five times, and **B1-CR-69 is the sixth**: the comparison used to build its
    registry from the one pack the parameterization already read, so it agreed
    with itself while eleven web extractors went untested.

    Two assertions now, because the set comparison alone cannot see a pack that
    is missing from both sides: the registry is built from `PACKS`, and each pack
    additionally has to bring extractors of its own.
    """
    from adopt_map.plugins import ExtractorRegistry

    registry = ExtractorRegistry()
    for entry in PACKS:
        registry.register_all(entry.extractors)
    assert {extractor.manifest().id for extractor in _EXTRACTORS} == {
        extractor.manifest().id for extractor in registry.all()
    }
    for entry in PACKS:
        assert entry.extractors, f"pack {entry.name} contributes no extractor"


def test_every_shipped_pack_is_parameterized() -> None:
    """A pack in the workspace is a pack in `PACKS`.

    *Defect sentence.* Fails when a shipped extractor pack is absent from this
    suite's parameterization; matters because an unparameterized pack's eight
    obligations are asserted by nothing at all while every validation line that
    names this suite still reads green -- which is exactly what happened to the
    `web` pack between S1.4 and S1.5 (B1-CR-69); no other instrument catches it,
    because the suite's own coverage guard compared the parameterization against a
    registry derived from that same parameterization.

    The workspace directory is the source of truth rather than a list here: a new
    pack lands as `packages/adopt-extractors-<name>/` before it has a row anywhere
    else.
    """
    shipped = {
        path.name.removeprefix("adopt-extractors-")
        for path in _PACKAGES_DIR.glob("adopt-extractors-*")
        if path.is_dir()
    }
    assert shipped, f"no extractor packs found under {_PACKAGES_DIR}"
    assert shipped == {entry.name for entry in PACKS}, (
        f"packs shipped but not conformance-tested: {sorted(shipped - {e.name for e in PACKS})}; "
        f"named here but not shipped: {sorted({e.name for e in PACKS} - shipped)}"
    )


@pytest.mark.parametrize("entry", PACKS, ids=[entry.name for entry in PACKS])
def test_each_pack_emits_something_over_its_tree(entry: Pack) -> None:
    """A control, per pack rather than in total.

    Every obligation above is satisfied perfectly by an extractor that emits
    nothing, so a summed total lets one productive pack vouch for a silent one.
    Asserted per pack, against the tree that pack was given.
    """
    extractors: Sequence[Extractor] = entry.extractors
    total = sum(len(list(extractor.extract(_context_for(extractor)))) for extractor in extractors)
    assert total > 0, f"pack {entry.name} emitted nothing over {entry.tree}"


def _identity(fact: SurfaceFact) -> tuple[str, str | None, str]:
    return (fact.identity_kind, fact.namespace, fact.local_key)


def _is_set_expression(node: ast.expr, set_names: set[str]) -> bool:
    """Whether an expression is a set: a literal, a comprehension, or a name
    bound to one earlier in the module."""
    if isinstance(node, ast.Set | ast.SetComp):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"set", "frozenset"}
    return isinstance(node, ast.Name) and node.id in set_names


def _sets_iterated(tree: ast.AST) -> list[int]:
    """Line numbers where a set is **iterated** rather than merely consulted.

    `sorted(a_set)` is fine and common -- sorting is exactly the fix -- so an
    iterable wrapped in `sorted` is not reported.
    """
    set_names: set[str] = set()
    for node in ast.walk(tree):
        target: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        if target is not None and value is not None and _is_set_expression(value, set()):
            set_names.add(target)

    offenders: list[int] = []
    for node in ast.walk(tree):
        iterables: list[ast.expr] = []
        if isinstance(node, ast.For):
            iterables.append(node.iter)
        elif isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            iterables.extend(generator.iter for generator in node.generators)
        for iterable in iterables:
            if (
                isinstance(iterable, ast.Call)
                and isinstance(iterable.func, ast.Name)
                and iterable.func.id == "sorted"
            ):
                continue
            if _is_set_expression(iterable, set_names):
                offenders.append(iterable.lineno)
    return sorted(offenders)
