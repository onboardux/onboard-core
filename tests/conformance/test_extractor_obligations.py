"""The eight `02` §7 obligations, over **every registered extractor** -- C13.

`03` §5.10: *"The eight extractor obligations are verified once by a shared
conformance suite parameterized over every registered extractor -- not rewritten
per extractor. That single suite is worth more than a hundred bespoke extractor
tests."*

The parameterization is over `adopt_extractors_common.pack()`, so **a new
extractor is covered the day it is registered** and an extractor removed from the
pack stops being claimed as covered. A suite with a hand-written list would drift
from the registry the first time somebody added a pack in a hurry, and every
later sprint would inherit the gap.

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
from pathlib import Path

import pytest
from adopt_extractors_common import pack
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import build_index
from adopt_map.plugins import audit_source
from adopt_map.schemas import Extractor, SurfaceFact

pytestmark = [pytest.mark.conformance, pytest.mark.integration]

#: A tree with something of every shape the `common` pack reads: Python
#: declarations, a dotenv with an ordinary key and a credential, and a config
#: file. Small enough to run per extractor, real enough that "emitted nothing"
#: is a finding rather than the expected outcome.
_TREE = Path("fixtures/repos/poisoned-import")

_EXTRACTORS = list(pack())
_IDS = [extractor.manifest().id for extractor in _EXTRACTORS]


def _context(*, exhausted: bool = False) -> ExtractorContext:
    start = time.time() - 10_000 if exhausted else time.time()
    span = 1.0 if exhausted else 3_600.0
    return ExtractorContext(
        root=str(_TREE),
        index=build_index(_TREE),
        budget=Budget.starting_at(start, stage1_s=span, total_s=span),
        archetype="web",
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
    first = [_identity(fact) for fact in extractor.extract(_context())]
    second = [_identity(fact) for fact in extractor.extract(_context())]
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
    emitted = {fact.identity_kind for fact in extractor.extract(_context())}
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
    for fact in extractor.extract(_context()):
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

    ctx = _context(exhausted=True)
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

    result = run_all([extractor, CrashingExtractor()], _context(), sequential=True)
    statuses = {outcome.extractor_id: outcome.status for outcome in result.outcomes}
    assert statuses["common.crashing"] == "failed"
    assert statuses[extractor.manifest().id] in {"ok", "truncated"}


def test_the_suite_covers_every_registered_extractor() -> None:
    """The parameterization is the registry, not a list.

    Without this, `_EXTRACTORS` could be filtered to one extractor and every test
    above would still pass -- the shape of vacuous gate this repository has found
    five times.
    """
    from adopt_map.plugins import ExtractorRegistry

    registry = ExtractorRegistry()
    registry.register_all(pack())
    assert {extractor.manifest().id for extractor in _EXTRACTORS} == {
        extractor.manifest().id for extractor in registry.all()
    }
    assert len(_EXTRACTORS) >= 6


def test_the_pack_emits_something_over_the_fixture_tree() -> None:
    """A control for the whole file.

    Every obligation above is satisfied perfectly by an extractor that emits
    nothing. This asserts the pack as a whole reads the tree, so the suite is
    measuring extractors that do something.
    """
    total = sum(len(list(extractor.extract(_context()))) for extractor in _EXTRACTORS)
    assert total > 0


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
