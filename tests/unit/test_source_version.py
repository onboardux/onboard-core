"""The `source_version` composite -- contracts §4, `02` §10 C5, impl spec §5.4.

`03` §5.4 calls the per-kind table below **the highest-value table in the build**:
it is what lets a downstream classifier answer *"semantics or only labels?"*
without a model call, so every kind needs one case that moves `sem` alone and one
that moves `ren` alone. A kind missing from this table is a kind whose partition
nobody has ever seen work.

| Behavior | Tier | Defect it catches |
|---|---|---|
| Per kind: `sem` moves alone, `ren` moves alone | **T1** | A field on the wrong side of the partition |
| `src` takes no part in equality | **T1** | Every commit rewriting every revision (B1-CR-43) |
| Null `sem` **is** equal to null for idempotence | **T1** | An opaque fact writing a revision on every run |
| Null `sem` **never** matches for a move | **T1** | Two unrelated opaque identities fused by a fabricated move |
| Codec round-trip over every grammar form | T2 | A composite the store can hold and this build cannot read |
| A kind with no presentation projection renders `r-` | T2 | A secret reference acquiring a cosmetic digest |
"""

from typing import Any

import pytest
from adopt_map.schemas.surface import FactRelation, SurfaceFact
from adopt_map.sourceversion import (
    SourceVersion,
    build_source_version,
    matches_semantically,
    parse_source_version,
)

from adopt_obs import AdoptError

pytestmark = pytest.mark.unit


def _version(
    kind: str,
    namespace: str | None,
    attributes: dict[str, Any],
    *,
    opaque: bool = False,
    outside_vcs: bool = False,
    prose: str | None = None,
    relations: list[FactRelation] | None = None,
    vcs_revision: str | None = None,
) -> SourceVersion:
    fact = SurfaceFact(
        identity_kind=kind,  # type: ignore[arg-type]
        namespace=namespace,
        local_key="k",
        title="t",
        attributes=attributes,
        opaque=opaque,
        outside_vcs=outside_vcs,
        prose=prose,
        relations=relations or [],
    )
    return build_source_version(
        fact, fact.validated_attributes().model_dump(mode="json"), vcs_revision=vcs_revision
    )


#: `(kind, namespace, base, semantic_field, semantic_value, presentation_field,
#: presentation_value)` -- every kind in `02` §4.2's table, plus the `secret:*`
#: model, which has no presentation projection and therefore no `ren` case.
_KINDS: list[tuple[str, str | None, dict[str, Any], str, Any, str | None, Any]] = [
    ("endpoint", "http", {"http_method": "GET"}, "path", "/v1/orders", "summary", "Orders"),
    ("endpoint", "http", {"path": "/v1/o"}, "framework", "django", "tags", ["public"]),
    ("db_field", "pg:public.orders", {"column": "status"}, "data_type", "text", "comment", "c"),
    ("symbol", "python", {"signature": "f()"}, "return_type", "int", "docstring", "d"),
    ("job", "celery", {"queue": "default"}, "schedule", "0 * * * *", "display_name", "n"),
    ("config_key", "django", {"key_path": "A.B"}, "value_type", "int", "description", "d"),
    ("config_key", "secret:env", {"source": "env"}, "name", "DB_PASSWORD", None, None),
    ("flag", "local", {"key_path": "f.x"}, "default", "true", "group_label", "g"),
    ("prompt", "file", {"variables": ["a"]}, "output_schema", "{}", "file_comments", "c"),
    (
        "model_pin",
        "anthropic",
        {"provider": "anthropic"},
        "model_id",
        "m",
        "alias_display_name",
        "a",
    ),
    ("tool_schema", "mcp", {"tool_name": "lookup"}, "has_side_effects", True, "description", "d"),
    ("retrieval_config", "qdrant", {"index": "kb"}, "top_k", 8, "display_label", "l"),
    ("metadata_component", "sap", {"api_name": "Z1"}, "data_type", "CHAR", "label", "L"),
    ("state_transition", "orders", {"from_state": "a"}, "to_state", "b", "display_name", "n"),
    ("ui_component", "aria", {"role": "button"}, "accessible_name", "Submit", "visible_text", "Go"),
]


@pytest.mark.parametrize(
    ("kind", "namespace", "base", "sem_field", "sem_value", "ren_field", "ren_value"),
    _KINDS,
    ids=[f"{row[0]}-{row[3]}" for row in _KINDS],
)
def test_projection_moves_exactly_one_digest(
    kind: str,
    namespace: str | None,
    base: dict[str, Any],
    sem_field: str,
    sem_value: Any,
    ren_field: str | None,
    ren_value: Any,
) -> None:
    """A semantic field moves `sem` alone; a presentation field moves `ren` alone.

    Fails when a field sits on the wrong side of the partition; matters because
    the whole render-only mechanism is this separation, and a semantic field
    filed as presentation makes a real change look cosmetic to every consumer
    downstream; no other instrument catches it because both composites are
    well-formed and the run reports success either way.
    """
    baseline = _version(kind, namespace, base)

    flipped_sem = _version(kind, namespace, {**base, sem_field: sem_value})
    assert flipped_sem.sem != baseline.sem, f"{sem_field} did not move sem"
    assert flipped_sem.ren == baseline.ren, f"{sem_field} moved ren as well"

    if ren_field is None:
        assert baseline.ren is None, "a kind with no presentation projection renders r-"
        return

    flipped_ren = _version(kind, namespace, {**base, ren_field: ren_value})
    assert flipped_ren.ren != baseline.ren, f"{ren_field} did not move ren"
    assert flipped_ren.sem == baseline.sem, f"{ren_field} moved sem as well"


def test_relations_are_semantic_and_prose_is_presentational() -> None:
    """Both are written into `body_md`, so both must land in a digest.

    Fails when either is left out of the partition; matters because the stored
    revision body would then carry an edge or a sentence the composite claims is
    unchanged, and the next run would compare a new body against an equal digest
    and write nothing; no other instrument catches it because neither is an
    attribute field and the partition assertion only covers attribute fields.
    """
    base = _version("endpoint", "http", {"path": "/v1/o"})
    with_relation = _version(
        "endpoint",
        "http",
        {"path": "/v1/o"},
        relations=[
            FactRelation(predicate="handled_by", target_kind="symbol", target_local_key="a.b")
        ],
    )
    with_prose = _version("endpoint", "http", {"path": "/v1/o"}, prose="Returns an order.")

    assert with_relation.sem != base.sem
    assert with_relation.ren == base.ren
    assert with_prose.ren != base.ren
    assert with_prose.sem == base.sem


def test_relation_order_does_not_change_the_digest() -> None:
    """Relations are a set of observed edges, not a sequence."""
    edges = [
        FactRelation(predicate="handled_by", target_kind="symbol", target_local_key="a.b"),
        FactRelation(predicate="reads", target_kind="db_field", target_local_key="orders"),
    ]
    forward = _version("endpoint", "http", {"path": "/x"}, relations=edges)
    reversed_ = _version("endpoint", "http", {"path": "/x"}, relations=list(reversed(edges)))
    assert forward.sem == reversed_.sem


def test_prompt_template_whitespace_is_normalized() -> None:
    """`02` §4.2: reindenting a prompt is not a semantic change."""
    one = _version("prompt", "file", {"template_body": "Answer  the\n question."})
    two = _version("prompt", "file", {"template_body": "Answer the question."})
    assert one.sem == two.sem


def test_src_takes_no_part_in_equality() -> None:
    """B1-CR-43 -- the defect that would have made idempotence impossible.

    Fails when `src` re-enters the comparison; matters because `src` is the
    tree's commit sha, so including it means committing anything anywhere
    rewrites every identity's revision on the next run and F4's acceptance
    criterion can never hold; no other instrument catches it on a fixture that
    never commits.
    """
    first = _version("symbol", "python", {"signature": "f()"}, vcs_revision="a" * 40)
    second = _version("symbol", "python", {"signature": "f()"}, vcs_revision="b" * 40)

    assert first.encode() != second.encode(), "src is still recorded"
    assert first.compares_equal(second), "src must not decide whether anything changed"


def test_opaque_is_equal_to_itself_but_never_matches_a_move() -> None:
    """The two comparisons are different, and this is the case that proves it.

    Fails when one equality serves both; matters because collapsing them either
    makes every opaque fact write a revision on every run, or licenses a move
    between two unrelated identities nobody can read; no other instrument
    catches it because each half looks correct in isolation.
    """
    one = _version("metadata_component", "sap", {"api_name": "ZFIELD_003"}, opaque=True)
    two = _version("metadata_component", "sap", {"api_name": "ZFIELD_003"}, opaque=True)

    assert one.sem is None and two.sem is None
    assert one.compares_equal(two), "an unchanged opaque fact must not write a revision"
    assert not matches_semantically(one, two), "null never matches null for a move"


def test_flags_are_recorded_and_ordered() -> None:
    both = _version("prompt", "console", {}, opaque=True, outside_vcs=True)
    assert both.encode().endswith(".foq")
    assert _version("prompt", "console", {}).encode().count(".f") == 0


@pytest.mark.parametrize(
    "composite",
    [
        pytest.param("sv1:s" + "a" * 32 + ".r" + "b" * 32, id="sem-and-ren"),
        pytest.param("sv1:s-.r-", id="both-null"),
        pytest.param("sv1:s" + "a" * 32 + ".r-.v" + "c" * 40, id="with-src"),
        pytest.param("sv1:s-.r" + "b" * 32 + ".fo", id="with-flags"),
        pytest.param("sv1:s" + "a" * 32 + ".r" + "b" * 32 + ".v1234.foq", id="every-segment"),
    ],
)
def test_codec_round_trips(composite: str) -> None:
    assert parse_source_version(composite).encode() == composite


@pytest.mark.parametrize(
    "composite",
    [
        pytest.param("sv2:s-.r-", id="wrong-scheme"),
        pytest.param("s-.r-", id="no-scheme"),
        pytest.param("sv1:s-", id="one-segment"),
        pytest.param("sv1:s" + "a" * 31 + ".r-", id="short-digest"),
        pytest.param("sv1:s-.r-.fx", id="unknown-flag-letter"),
        pytest.param("sv1:s-.r-.v" + "g" * 8, id="non-hex-src"),
        pytest.param("sv1:s-.r-.fo.fo", id="repeated-flags"),
    ],
)
def test_malformed_composites_are_refused(composite: str) -> None:
    with pytest.raises(AdoptError):
        parse_source_version(composite)


def test_a_null_composite_never_equals_a_missing_one() -> None:
    """There is no prior revision, which is not the same as an equal one."""
    assert not _version("symbol", "python", {}).compares_equal(None)
