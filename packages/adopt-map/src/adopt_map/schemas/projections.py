"""The per-kind semantic / presentation partition -- contracts §4.2.

**Held as data, not as branches** (`05` S1.2). Thirteen kinds plus the `secret:*`
model is fourteen partitions, and a function per kind is fourteen places for the
rule to drift; the shape of the answer is a table, so the table is what exists
and `sourceversion` reads it.

**The partition is asserted disjoint *and* total at import, and the totality half
is the one that matters** (B1-CR-44). `02` §4.2 says only *"a field belongs to
exactly one projection; a field in both fails at import"* -- it names the
double-assignment failure and not the one that actually hides. A field assigned
to **neither** projection is a field no digest covers: change it and the composite
is unchanged, so the writer writes no revision, the run reports zero and the
change is gone. That failure is silent, and the double assignment is not. Both
are refused here.

Enforcing totality found three fields `02` §4.2's table does not mention:
`endpoint.framework`, `endpoint.handler_symbol` and `ui_component.accessible_name`.
All three are semantic -- the framework that declares an endpoint and the symbol
that handles it are what the endpoint *is*, and an accessible name is half of a
rung-2 locator's identity (`02` §3.1 rule 3), so a change to it is a change of
referent. §4.2 is repaired to name them rather than this module quietly assigning
them.

**What is not here.** `SurfaceFact.relations` and `.prose` are not attribute
fields, so they are not in this table; `sourceversion` adds them to the two
projections directly, with the reason stated there.
"""

from dataclasses import dataclass
from typing import Final

from adopt_map.schemas.attributes import (
    ATTRIBUTE_MODELS,
    AttributeModel,
    ConfigKeyAttributes,
    DbFieldAttributes,
    EndpointAttributes,
    FlagAttributes,
    JobAttributes,
    MetadataComponentAttributes,
    ModelPinAttributes,
    PromptAttributes,
    RetrievalConfigAttributes,
    SecretReferenceAttributes,
    StateTransitionAttributes,
    SymbolAttributes,
    ToolSchemaAttributes,
    UiComponentAttributes,
    attribute_model_for,
)

__all__ = ["PROJECTIONS", "Projection", "projection_for"]


@dataclass(frozen=True, slots=True)
class Projection:
    """One kind's partition of its attribute fields into the two digests."""

    semantic: frozenset[str]
    presentation: frozenset[str]

    @property
    def has_presentation(self) -> bool:
        """Whether this kind has a presentation digest at all.

        `False` renders `02` §4.1's null form ``r-``. It is derived from the
        table rather than listed separately, so a kind cannot be given
        presentation fields and a null `ren` at the same time.
        """
        return bool(self.presentation)


#: Keyed by the attribute **model**, not by the kind, because `config_key`
#: has two: the ordinary one and the value-free `secret:*` model, and they
#: partition differently -- the secret model has no presentation projection at
#: all (`02` §4.2, *"— (`r-`)"*).
PROJECTIONS: Final[dict[AttributeModel, Projection]] = {
    EndpointAttributes: Projection(
        semantic=frozenset(
            {
                "http_method",
                "path",
                "parameters",
                "parameter_types",
                "request_schema_digest",
                "response_schema_digest",
                "auth",
                "status_codes",
                "direction",
                # B1-CR-44: named by neither projection in `02` §4.2, and both
                # semantic. Two frameworks declaring one path are one referent
                # only if the framework is part of what is compared.
                "framework",
                "handler_symbol",
            }
        ),
        presentation=frozenset(
            {"summary", "description", "tags", "operation_name", "declaration_order"}
        ),
    ),
    DbFieldAttributes: Projection(
        semantic=frozenset(
            {"column", "data_type", "nullable", "default", "is_key", "is_unique", "indexes"}
        ),
        presentation=frozenset({"comment", "verbose_name", "help_text", "admin_ordering"}),
    ),
    SymbolAttributes: Projection(
        semantic=frozenset(
            {"signature", "parameters", "parameter_types", "return_type", "decorators", "raises"}
        ),
        presentation=frozenset({"docstring", "comments"}),
    ),
    JobAttributes: Projection(
        semantic=frozenset(
            {"schedule", "target_symbol", "queue", "retry_policy", "timeout_seconds"}
        ),
        presentation=frozenset({"display_name", "description"}),
    ),
    ConfigKeyAttributes: Projection(
        semantic=frozenset({"key_path", "value_type", "default", "required"}),
        presentation=frozenset({"description", "group_label"}),
    ),
    FlagAttributes: Projection(
        semantic=frozenset({"key_path", "value_type", "default", "required"}),
        presentation=frozenset({"description", "group_label"}),
    ),
    SecretReferenceAttributes: Projection(
        # *"reference name and location only"* (`02` §4.2), and no presentation
        # projection. There is nothing here that could be a secret and nothing
        # that could be cosmetic.
        semantic=frozenset({"source", "name"}),
        presentation=frozenset(),
    ),
    PromptAttributes: Projection(
        semantic=frozenset({"template_body", "variables", "output_schema"}),
        presentation=frozenset({"headings", "file_comments"}),
    ),
    ModelPinAttributes: Projection(
        semantic=frozenset(
            {"provider", "model_id", "pin_stability", "temperature", "max_tokens", "top_p"}
        ),
        presentation=frozenset({"alias_display_name"}),
    ),
    ToolSchemaAttributes: Projection(
        semantic=frozenset({"tool_name", "parameter_schema_digest", "has_side_effects"}),
        presentation=frozenset({"description"}),
    ),
    RetrievalConfigAttributes: Projection(
        semantic=frozenset(
            {
                "store",
                "index",
                "top_k",
                "embedding_model",
                "chunk_size",
                "chunk_overlap",
                "rerank_model",
            }
        ),
        presentation=frozenset({"display_label"}),
    ),
    MetadataComponentAttributes: Projection(
        semantic=frozenset({"component_type", "api_name", "data_type", "relationship_targets"}),
        presentation=frozenset({"label", "help_text", "layout_position"}),
    ),
    StateTransitionAttributes: Projection(
        semantic=frozenset({"from_state", "to_state", "guard"}),
        presentation=frozenset({"display_name"}),
    ),
    UiComponentAttributes: Projection(
        # B1-CR-44: `accessible_name` is named by neither projection in §4.2 and
        # is semantic -- `role` plus accessible name **is** the rung-2 locator,
        # so a change to it is a change of referent, not of presentation.
        semantic=frozenset({"stable_id", "role", "accessible_name", "task"}),
        presentation=frozenset({"visible_text", "ordering"}),
    ),
}


def _assert_partition() -> None:
    """Every attribute model is partitioned exactly once, or this module does not import.

    At import rather than in a test, for the reason `attributes._assert_registry_is_total`
    is: a model with no partition, or one with a field in neither half, means facts
    of that kind are compared on an incomplete digest, and the first fixture to
    exercise the kind is a bad place to discover it. The test that asserts this
    exists too -- it asserts that the *check* is here, which is a different claim.
    """
    models: set[AttributeModel] = {*ATTRIBUTE_MODELS.values(), SecretReferenceAttributes}

    missing = sorted(model.__name__ for model in models - set(PROJECTIONS))
    unknown = sorted(model.__name__ for model in set(PROJECTIONS) - models)
    if missing or unknown:
        raise RuntimeError(
            f"the projection table does not match the attribute registry: missing "
            f"{missing}, unknown {unknown}. A model with no partition is a kind whose "
            "facts are compared on no digest at all."
        )

    for model, projection in PROJECTIONS.items():
        declared = frozenset(model.model_fields)
        both = sorted(projection.semantic & projection.presentation)
        neither = sorted(declared - projection.semantic - projection.presentation)
        invented = sorted((projection.semantic | projection.presentation) - declared)
        if both or neither or invented:
            raise RuntimeError(
                f"{model.__name__}'s projection is not a partition of its fields: "
                f"in both {both}, in neither {neither}, not a field {invented}. "
                "A field in neither projection is a field whose change writes no "
                "revision and is reported as nothing having happened."
            )


_assert_partition()


def projection_for(kind: str, namespace: str | None) -> Projection:
    """The partition for this fact's attribute model.

    Routed through `attribute_model_for` rather than through a second lookup, so
    the `secret:*` namespace is special in exactly one place.

    Raises:
        AdoptError: ``MAP_EXTRACTOR_FAILED`` when `kind` is not a declared
            `identity_kind` value.
    """
    return PROJECTIONS[attribute_model_for(kind, namespace)]
