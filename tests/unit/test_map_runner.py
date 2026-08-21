"""The runner: pack selection refusals, and a failing extractor being loud.

The failure half is B-08 promoted from a backlog finding into a design rule. On
a 930k-line real repository one extractor failed on one run of two, the run
exited `0`, and the only trace was one fewer identity -- because a silent
extractor failure and a genuinely smaller system are indistinguishable from
outside. Everything here exists so that they are distinguishable.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from adopt_map import Observation, Pack, SourceTree, run_map, select_packs
from adopt_map.observation import Span

from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope, ScopeNode


class _Nothing:
    """A records stand-in whose transaction does nothing."""

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield


class _Collecting:
    """An `IdentityWriter` that records what it was asked to observe."""

    def __init__(self) -> None:
        self.observed: list[tuple[str, tuple[str, ...]]] = []

    def observe(self, **kwargs: object) -> object:
        self.observed.append((str(kwargs["kind"]), tuple(kwargs["key"])))  # type: ignore[arg-type]
        return None


class _Exploding:
    name = "test.exploding"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        raise PermissionError("the file was locked by something else")
        yield  # pragma: no cover -- unreachable, present to make this a generator


class _Working:
    name = "test.working"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        yield Observation(
            kind="config_key",
            key=("FOUND",),
            namespace="env",
            attributes={"name": "FOUND"},
            span=Span(path="a.py", start_line=1, end_line=1),
        )


@pytest.fixture
def scope() -> Scope:
    node = ScopeNode(id="x", slug="s")
    return Scope(firm=node, engagement=node, system=node, environment=node)


@pytest.fixture
def tree(tmp_path: Path) -> SourceTree:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    return SourceTree.scan(tmp_path)


@pytest.mark.unit
def test_a_failing_extractor_is_recorded_with_its_exception_type(
    tree: SourceTree, scope: Scope
) -> None:
    """*Fails when* an extractor exception is swallowed or its type dropped.
    *Matters because* B-08 was undiagnosable for days precisely because the
    detail was dropped at emission -- `PermissionError` and `FileNotFoundError`
    demand completely different fixes, and without the type neither can be told
    from the other without a debugger and a reproduction nobody has. *No other
    instrument catches it because* the run still succeeds and still writes the
    other extractors' identities."""
    report = run_map(
        tree=tree,
        scope=scope,
        packs=[Pack(name="t", extractors=(_Exploding(), _Working()))],
        writer=_Collecting(),
        records=_Nothing(),
    )

    assert [outcome.extractor for outcome in report.failed] == ["test.exploding"]
    assert report.failed[0].detail == "PermissionError"


@pytest.mark.unit
def test_one_broken_extractor_does_not_cost_the_others_their_observations(
    tree: SourceTree, scope: Scope
) -> None:
    """*Fails when* an exception aborts the pack. *Matters because* one broken
    extractor should cost its own observations, not the other twenty-eight's --
    an FDE with a partially working map can still do the engagement, and a map
    that refuses entirely over one bad file cannot be used on real client code.
    *No other instrument catches it because* the failing extractor's own outcome
    is recorded either way; what differs is whether anything after it ran."""
    writer = _Collecting()

    report = run_map(
        tree=tree,
        scope=scope,
        packs=[Pack(name="t", extractors=(_Exploding(), _Working()))],
        writer=writer,
        records=_Nothing(),
    )

    assert writer.observed == [("config_key", ("FOUND",))]
    assert report.identities_seen == 1


@pytest.mark.unit
def test_selecting_packs_for_an_unrecorded_archetype_is_refused_not_defaulted() -> None:
    """*Fails when* a missing archetype quietly falls back to `generic`.
    *Matters because* a thin generic-only map looks exactly like a complete one
    -- it exits 0 and lists identities -- so the FDE would never learn the web
    and AI extractors never ran. *No other instrument catches it because* a
    successful run over the wrong pack set is indistinguishable from a successful
    run, by construction."""
    with pytest.raises(AdoptError) as caught:
        select_packs(None, available={"generic": Pack(name="generic", extractors=())})

    assert caught.value.code is ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE


@pytest.mark.unit
def test_an_unknown_pack_name_is_refused_by_name() -> None:
    """*Fails when* `--packs typo` is silently ignored. *Matters because* the
    operator would believe they had mapped with a pack that never ran. *No other
    instrument catches it because* ignoring an unknown name produces a valid run
    over the remaining packs."""
    with pytest.raises(AdoptError) as caught:
        select_packs(
            "web",
            override=["generic", "webb"],
            available={"generic": Pack(name="generic", extractors=())},
        )

    assert caught.value.code is ErrorCode.MAP_NO_PACK_FOR_ARCHETYPE
    assert "webb" in caught.value.message


@pytest.mark.unit
def test_the_tree_refuses_to_be_larger_than_the_walk_bound(tmp_path: Path) -> None:
    """*Fails when* an oversized tree truncates instead of refusing. *Matters
    because* a truncated walk reports a coverage number for a repository it only
    partly saw -- `files_unmapped`, the recall floor and every count downstream
    would be quietly computed against the wrong denominator. *No other instrument
    catches it because* a truncated run looks completely normal and exits 0."""
    for index in range(5):
        (tmp_path / f"f{index}.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(AdoptError) as caught:
        SourceTree.scan(tmp_path, max_files=3)

    assert caught.value.code is ErrorCode.MAP_TREE_TOO_LARGE


@pytest.mark.unit
def test_the_tree_never_walks_adopts_own_working_directory(tmp_path: Path) -> None:
    """*Fails when* `.adopt/` re-enters the walk. *Matters because* the tool
    would map its own store: `adopt map` would mint identities for our database
    and the report's file counts would include our files, making the tool part of
    the system it is describing. *No other instrument catches it because* those
    identities are perfectly well-formed and the run exits 0 -- this was found by
    reading a real run's output, not by a failing test."""
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".adopt").mkdir()
    (tmp_path / ".adopt" / "store.db").write_text("not really a database", encoding="utf-8")

    walked = {entry.path for entry in SourceTree.scan(tmp_path).files}

    assert walked == {"real.py"}
