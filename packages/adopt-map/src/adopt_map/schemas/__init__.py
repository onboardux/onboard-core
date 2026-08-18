"""Build 1's shapes -- contracts §5, §7; implementation spec §5.1.

`SurfaceFact`, `FactRelation`, `SourceRef`, the per-kind attribute registry, the
relation-predicate enum and the extractor protocol. The `source_version` codec
joins them in S1.2, which owns the projection table, and S1.7's four glue-pass
output models join them in `agent.py`.

These are **Build 1's shapes and they live in Build 1's package** (B1-CR-28). No
Build 0 code reads a `SurfaceFact`, so putting them here keeps the ownership rule
in `03` §4 stricter than the pack originally asked and removes one reason to
touch a protected package at all.
"""

from adopt_map.schemas.agent import (
    AGENT_OUTPUT_MODELS,
    GlueOutput,
    LabelCandidate,
    LabelOutput,
    ProseOutput,
    TriageItem,
    TriageOutput,
)
from adopt_map.schemas.attributes import (
    ATTRIBUTE_MODELS,
    SECRET_NAMESPACE_PREFIX,
    SecretReferenceAttributes,
    attribute_model_for,
    validate_attributes,
)
from adopt_map.schemas.relations import RELATION_PREDICATES, RelationPredicate
from adopt_map.schemas.surface import (
    EvidenceMethod,
    Extractor,
    ExtractorManifest,
    FactRelation,
    SourceRef,
    SurfaceFact,
)

__all__ = [
    "AGENT_OUTPUT_MODELS",
    "ATTRIBUTE_MODELS",
    "RELATION_PREDICATES",
    "SECRET_NAMESPACE_PREFIX",
    "EvidenceMethod",
    "Extractor",
    "ExtractorManifest",
    "FactRelation",
    "GlueOutput",
    "LabelCandidate",
    "LabelOutput",
    "ProseOutput",
    "RelationPredicate",
    "SecretReferenceAttributes",
    "SourceRef",
    "SurfaceFact",
    "TriageItem",
    "TriageOutput",
    "attribute_model_for",
    "validate_attributes",
]
