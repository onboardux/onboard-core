"""Build 1's shapes -- contracts §5.1, §7; implementation spec §5.1.

Four instruments, and the manifest that earned each one:

| Behavior | Tier | Instrument |
|---|---|---|
| Every `IdentityKind` has an attribute model, or import fails | T1 | registry completeness |
| A field outside a kind's closed model is rejected | T3 | extra-key table |
| A `secret:*` fact has nowhere to put a value | **T1** | absent-field assertion |
| `SurfaceFact` carries no framework-owned field | **T1** | absent-field assertion |

The two T1 rows are *absence* tests, which is unusual and deliberate. Both
guarantees -- "a staging run cannot emit a production URI" and "no secret value
reaches the store" -- rest on a field not existing. A test that asserts a field's
behaviour cannot notice the day someone adds the field; a test that asserts the
field is absent is the only instrument that can.
"""

from typing import get_args

import pytest
from adopt_map.schemas import (
    ATTRIBUTE_MODELS,
    RELATION_PREDICATES,
    ExtractorManifest,
    FactRelation,
    SecretReferenceAttributes,
    SourceRef,
    SurfaceFact,
    attribute_model_for,
    validate_attributes,
)
from pydantic import ValidationError

from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, ErrorCode

#: Framework-owned, and therefore absent from `SurfaceFact` (contracts §7).
_FRAMEWORK_OWNED_FIELDS = (
    "uri",
    "confidence",
    "source_version",
    "firm_id",
    "engagement_id",
    "system_id",
    "environment_id",
    "environment",
    "scope",
)


@pytest.mark.unit
def test_every_identity_kind_has_an_attribute_model() -> None:
    """*Fails when* a kind is added to the manifest and no model follows.

    *Matters because* a kind with no model validates against nothing, which is
    the one state in which an unknown attribute reaches the store. *No other
    instrument catches it because* the writer would accept the fact happily --
    the defect is silent by construction.
    """
    assert set(ATTRIBUTE_MODELS) == set(get_args(IdentityKind))


@pytest.mark.unit
def test_the_registry_check_runs_at_import_not_only_here() -> None:
    """*Fails when* the import-time assertion is deleted and only this test remains.

    *Matters because* the test above passes in CI and says nothing about a
    developer's process, where the missing model surfaces on whichever fixture
    happens to exercise that kind first. *No other instrument catches it because*
    a deleted assertion leaves no trace.
    """
    from adopt_map.schemas import attributes

    original = dict(attributes.ATTRIBUTE_MODELS)
    try:
        attributes.ATTRIBUTE_MODELS.pop("endpoint")
        with pytest.raises(RuntimeError, match="does not match `IdentityKind`"):
            attributes._assert_registry_is_total()
    finally:
        attributes.ATTRIBUTE_MODELS.clear()
        attributes.ATTRIBUTE_MODELS.update(original)


@pytest.mark.unit
@pytest.mark.parametrize("kind", sorted(get_args(IdentityKind)))
def test_an_unknown_attribute_key_is_rejected_for_every_kind(kind: str) -> None:
    """*Fails when* a model is declared without `extra="forbid"`.

    *Matters because* `02` §1.1 makes the closed schema an **egress allowlist**:
    a key the model does not declare is a key that cannot reach the store, an
    artifact or a log line. *No other instrument catches it because* one
    permissive model among thirteen is invisible until the field it admits turns
    out to have been a secret.
    """
    with pytest.raises(AdoptError) as caught:
        validate_attributes(kind, None, {"not_a_declared_field": "x"})
    assert caught.value.code is ErrorCode.MAP_EXTRACTOR_FAILED


@pytest.mark.unit
def test_a_secret_reference_has_nowhere_to_put_a_value() -> None:
    """*Fails when* a value-bearing field is added to the `secret:*` model.

    *Matters because* PRD N9 is "zero secret values, anywhere" and this is the
    structural half of it -- the planted-secret property suite is the empirical
    half. *No other instrument catches it because* a newly added `value` field
    would be populated by a well-meaning extractor and the fuzz corpus would
    have to happen to contain that exact secret to notice.
    """
    model = attribute_model_for("config_key", "secret:vault")
    assert model is SecretReferenceAttributes
    assert set(SecretReferenceAttributes.model_fields) == {"source", "name"}

    for forbidden in ("value", "secret", "token", "password", "content"):
        with pytest.raises(AdoptError):
            validate_attributes("config_key", "secret:env", {"name": "DB_PASSWORD", forbidden: "s"})


@pytest.mark.unit
def test_a_plain_config_key_still_takes_the_ordinary_model() -> None:
    """The `secret:` routing is a prefix match on the namespace, not on the kind."""
    assert attribute_model_for("config_key", "django") is ATTRIBUTE_MODELS["config_key"]
    assert attribute_model_for("config_key", None) is ATTRIBUTE_MODELS["config_key"]


@pytest.mark.unit
def test_surface_fact_carries_no_framework_owned_field() -> None:
    """*Fails when* a URI, a confidence or a scope field is added to `SurfaceFact`.

    *Matters because* environment isolation (PRD F6) is structural precisely
    because an extractor has no field through which to name an environment. Add
    one and the guarantee silently becomes procedural -- "extractors are asked
    not to set it" -- which is what the fuzz suite exists to disprove. *No other
    instrument catches it because* the fuzz suite fuzzes the fields that exist.
    """
    declared = set(SurfaceFact.model_fields)
    assert declared.isdisjoint(_FRAMEWORK_OWNED_FIELDS), sorted(
        declared.intersection(_FRAMEWORK_OWNED_FIELDS)
    )


@pytest.mark.unit
def test_a_relation_names_a_referent_rather_than_a_uri() -> None:
    """The same argument, one level down: a relation target is minted, not supplied."""
    declared = set(FactRelation.model_fields)
    assert "target" not in declared
    assert "target_uri" not in declared
    assert {"predicate", "target_kind", "target_namespace", "target_local_key"} == declared


@pytest.mark.unit
def test_an_unknown_relation_predicate_is_rejected() -> None:
    """The predicate vocabulary is closed (`02` §5.2); nineteen and no twentieth."""
    assert len(RELATION_PREDICATES) == len(
        get_args(FactRelation.model_fields["predicate"].annotation)
    )
    with pytest.raises(ValidationError):
        FactRelation(predicate="relates_to", target_kind="symbol", target_local_key="x")  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_surface_fact_rejects_an_undeclared_field_of_its_own() -> None:
    """`extra="forbid"` on the fact itself, not only on its attributes."""
    with pytest.raises(ValidationError):
        SurfaceFact(
            identity_kind="endpoint",
            local_key="GET /v1/orders",
            title="orders",
            confidence=0.99,  # type: ignore[call-arg]
        )


@pytest.mark.unit
def test_a_manifest_must_declare_at_least_one_kind() -> None:
    """*Fails when* `kinds` becomes optional.

    *Matters because* `02` §7 obligation 4 is enforced *against* this list: an
    extractor declaring no kinds is one that may emit none, and the runtime check
    would then pass vacuously for every fact it produced.
    """
    with pytest.raises(ValidationError):
        ExtractorManifest(
            id="common.stub", version="1.0.0", pack="common", kinds=[], method="grammar"
        )


@pytest.mark.unit
def test_a_source_ref_holds_a_relative_path() -> None:
    """A `SourceRef` is constructible with a repo-relative path and no more."""
    ref = SourceRef(path="orders/views.py", start_line=10, end_line=20, blob_sha="abc")
    assert ref.path == "orders/views.py"
    with pytest.raises(ValidationError):
        SourceRef(path="orders/views.py", absolute_path="/home/x/orders/views.py")  # type: ignore[call-arg]
