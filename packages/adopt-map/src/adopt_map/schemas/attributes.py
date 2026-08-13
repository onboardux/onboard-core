"""The per-kind attribute registry -- contracts §5.1.

**Strict-closed is an egress allowlist, not a nicety** (`02` §1.1). A field that
does not exist on one of these models cannot reach the store, an artifact or a
log line, and that is the whole mechanism behind PRD N9 (no secret values) and
N16 (log hygiene). Every model here is `extra="forbid"`; nothing widens one to
make an extractor's output fit.

Three rules this module enforces structurally rather than by review:

1. **Every `IdentityKind` has an entry, checked at import.** A kind with no model
   would fall through to "no validation" -- the one state in which an unknown
   field reaches the store -- so the absence is a hard import failure rather than
   a runtime surprise on whichever fixture happens to exercise that kind first.
2. **The `secret:*` model has no value field.** `02` §3.1 rule 2 and §5.1 rule 4:
   a secret *reference* is a `config_key` under `namespace = "secret:<source>"`,
   and the attribute model admits `source` and `name` only. There is nowhere to
   put a secret, so no extractor can emit one by mistake -- which is a stronger
   guarantee than scanning for one afterwards.
3. **Selection is by `(kind, namespace)`, not by kind alone.** The `secret:*`
   namespace is the only case today, and routing it through the same lookup keeps
   the caller from having to know it is special.

**Field sources.** Each model's fields are those `02` §4.2 names in that kind's
semantic and presentation projections, plus the fields `02` §5's worked example
carries. Nothing is invented here. **S1.2 owns the projection table itself**
(`05` S1.2) -- this module declares *which fields exist*, and the partition of
those fields into the two digests is that sprint's work, held as data with its
own import-time assertion.

Every field is optional except the ones that name the referent, because the
degrade ladder is real: a `grammar` pass and a `regex` pass see the same
referent with different amounts of detail, and a required field would force the
weaker pass to invent one.
"""

from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "ATTRIBUTE_MODELS",
    "SECRET_NAMESPACE_PREFIX",
    "AttributeModel",
    "attribute_model_for",
    "validate_attributes",
]

_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

#: `02` §3.1 rule 2. A `config_key` whose namespace starts with this is a secret
#: *reference*, and takes the value-free model below.
#:
#: `S105` is suppressed because this is the opposite of a hard-coded credential:
#: it is the marker that routes a fact to the one model with **no value field**.
SECRET_NAMESPACE_PREFIX: Final[str] = "secret:"  # noqa: S105


class _Attributes(BaseModel):
    """Base for every attribute model: strict-closed and frozen."""

    model_config = _CONFIG


class EndpointAttributes(_Attributes):
    http_method: str | None = None
    path: str | None = None
    parameters: list[str] = []
    parameter_types: dict[str, str] = {}
    request_schema_digest: str | None = None
    response_schema_digest: str | None = None
    auth: str | None = None
    status_codes: list[int] = []
    direction: Literal["inbound", "outbound"] = "inbound"
    framework: str | None = None
    handler_symbol: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] = []
    operation_name: str | None = None
    declaration_order: int | None = None


class DbFieldAttributes(_Attributes):
    column: str | None = None
    data_type: str | None = None
    nullable: bool | None = None
    default: str | None = None
    is_key: bool | None = None
    is_unique: bool | None = None
    indexes: list[str] = []
    comment: str | None = None
    verbose_name: str | None = None
    help_text: str | None = None
    admin_ordering: int | None = None


class SymbolAttributes(_Attributes):
    signature: str | None = None
    parameters: list[str] = []
    parameter_types: dict[str, str] = {}
    return_type: str | None = None
    decorators: list[str] = []
    raises: list[str] = []
    docstring: str | None = None
    comments: str | None = None


class JobAttributes(_Attributes):
    schedule: str | None = None
    target_symbol: str | None = None
    queue: str | None = None
    retry_policy: str | None = None
    timeout_seconds: int | None = None
    display_name: str | None = None
    description: str | None = None


class ConfigKeyAttributes(_Attributes):
    key_path: str | None = None
    value_type: str | None = None
    default: str | None = None
    required: bool | None = None
    description: str | None = None
    group_label: str | None = None


class SecretReferenceAttributes(_Attributes):
    """`config_key` under a `secret:*` namespace -- contracts §5.1 rule 4.

    **Two fields, and neither is a value.** `02` §4.2's projection for this kind
    reads *"reference name and location only"* and gives it no presentation
    projection at all. The absence is the guarantee: a model with an optional
    `value` field that extractors are told not to set is a model that will carry
    a secret the first time someone is in a hurry.
    """

    source: str | None = None
    name: str | None = None


class FlagAttributes(_Attributes):
    key_path: str | None = None
    value_type: str | None = None
    default: str | None = None
    required: bool | None = None
    description: str | None = None
    group_label: str | None = None


class PromptAttributes(_Attributes):
    template_body: str | None = None
    variables: list[str] = []
    output_schema: str | None = None
    headings: list[str] = []
    file_comments: str | None = None


class ModelPinAttributes(_Attributes):
    provider: str | None = None
    model_id: str | None = None
    pin_stability: Literal["pinned", "floating", "unknown"] = "unknown"
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    alias_display_name: str | None = None


class ToolSchemaAttributes(_Attributes):
    tool_name: str | None = None
    parameter_schema_digest: str | None = None
    has_side_effects: bool | None = None
    description: str | None = None


class RetrievalConfigAttributes(_Attributes):
    store: str | None = None
    index: str | None = None
    top_k: int | None = None
    embedding_model: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    rerank_model: str | None = None
    display_label: str | None = None


class MetadataComponentAttributes(_Attributes):
    component_type: str | None = None
    api_name: str | None = None
    data_type: str | None = None
    relationship_targets: list[str] = []
    label: str | None = None
    help_text: str | None = None
    layout_position: str | None = None


class StateTransitionAttributes(_Attributes):
    from_state: str | None = None
    to_state: str | None = None
    guard: str | None = None
    display_name: str | None = None


class UiComponentAttributes(_Attributes):
    """Rungs 1-2 only -- Bet 1, `02` §3.1 rule 3, B1-CR-11.

    There is no `selector`, no `xpath` and no `coordinates` field, and there
    never will be. A CSS selector reaching a URI is a P0 defect, and the cheapest
    way to make that impossible is to give it nowhere to live.
    """

    stable_id: str | None = None
    role: str | None = None
    accessible_name: str | None = None
    task: str | None = None
    visible_text: str | None = None
    ordering: int | None = None


AttributeModel = type[_Attributes]

#: Keyed by Build 0's closed `IdentityKind`. Imported, never restated.
ATTRIBUTE_MODELS: Final[dict[str, AttributeModel]] = {
    "endpoint": EndpointAttributes,
    "db_field": DbFieldAttributes,
    "symbol": SymbolAttributes,
    "job": JobAttributes,
    "config_key": ConfigKeyAttributes,
    "flag": FlagAttributes,
    "prompt": PromptAttributes,
    "model_pin": ModelPinAttributes,
    "tool_schema": ToolSchemaAttributes,
    "retrieval_config": RetrievalConfigAttributes,
    "metadata_component": MetadataComponentAttributes,
    "state_transition": StateTransitionAttributes,
    "ui_component": UiComponentAttributes,
}


def _assert_registry_is_total() -> None:
    """Every `IdentityKind` member has a model, or this module does not import.

    Deliberately at import rather than in a test: a missing entry means facts of
    that kind reach the store unvalidated, and the first fixture to exercise the
    kind is a bad place to discover it. The test that asserts this exists too --
    it asserts that the *check* is here, which is a different claim.
    """
    declared = frozenset(get_args(IdentityKind))
    missing = sorted(declared - set(ATTRIBUTE_MODELS))
    extra = sorted(set(ATTRIBUTE_MODELS) - declared)
    if missing or extra:
        raise RuntimeError(
            f"the attribute registry does not match `IdentityKind`: "
            f"missing {missing}, unknown {extra}. Every kind needs a closed model -- "
            "a kind with none is a kind whose facts reach the store unvalidated."
        )


_assert_registry_is_total()


def attribute_model_for(kind: str, namespace: str | None) -> AttributeModel:
    """The closed model that validates this fact's attributes.

    Args:
        kind: An `identity_kind` value.
        namespace: The fact's namespace, which selects the `secret:*` model.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when `kind` is not a declared
            `identity_kind`.
    """
    if (
        kind == "config_key"
        and namespace is not None
        and namespace.startswith(SECRET_NAMESPACE_PREFIX)
    ):
        return SecretReferenceAttributes
    model = ATTRIBUTE_MODELS.get(kind)
    if model is None:
        raise AdoptError(
            ErrorCode.MAP_EXTRACTOR_FAILED,
            message=f"identity kind {kind!r} is not a declared identity_kind value",
            hint="The kind enum is Build 0's and closed (`02` §3.1 rule 1). A referent "
            "that does not fit maps to the nearest kind with a namespace that "
            "disambiguates; a genuine gap seen twice is a Build 0 amendment.",
        )
    return model


def validate_attributes(kind: str, namespace: str | None, attributes: object) -> _Attributes:
    """Validate a fact's attributes against its kind's closed model.

    Args:
        kind: An `identity_kind` value.
        namespace: The fact's namespace.
        attributes: The raw attribute mapping an extractor emitted.

    Returns:
        The validated, frozen model.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` on an unknown kind, an unknown key
            or a type mismatch. One code, because from the operator's side these
            are one thing: the extractor emitted something the schema does not
            admit, and the run continues with that extractor's facts recorded as
            a failure (`02` §7 obligation 8).
    """
    model = attribute_model_for(kind, namespace)
    try:
        return model.model_validate(attributes)
    except ValidationError as exc:
        raise AdoptError(
            ErrorCode.MAP_EXTRACTOR_FAILED,
            message=f"attributes for kind {kind!r} do not validate: {exc.error_count()} error(s)",
            hint=f"The attribute schema is closed (`02` §5.1 rule 1): {exc.errors(include_url=False)}. "
            "Widening the model is a contracts change, not a fix -- the closed schema is "
            "what keeps a secret value from reaching the store.",
        ) from exc
