"""`data.semantic_model` -- semantic models and the metrics built on them.

`01` F8.5 files *"models, sources, **metrics**"* under `metadata_component`, and
a semantic layer is where a warehouse states what its numbers **mean**: a metric
definition is the difference between "revenue" as a column and "revenue" as the
number a board deck quotes. Changing a metric's expression changes what every
downstream answer says while touching no model and no table.

**Lineage in the same direction as `data.dbt`.** A semantic model
`derives_from` the dbt model it is built on; a metric `derives_from` the
semantic model that supplies its measure. `02` §5.2: the extractor emits the
direction it observed, and what a `model: ref('orders')` line observes is "I am
built on that".

**Split from `data.dbt` deliberately.** They read the same file family, and they
are still two extractors because a client can have either without the other -- a
warehouse with no semantic layer is the common case, and a semantic layer over
somebody else's models is not rare. `03` §9's rollback lever is per extractor, so
two subjects that fail independently are two extractors.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, FactRelation, SourceRef, SurfaceFact

from adopt_extractors_data._documents import SEMANTIC_OWNER, declaring_keys_for, load_yaml

__all__ = ["MANIFEST", "NAMESPACE", "SemanticModelExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="data.semantic_model",
    version="1.0.0",
    pack="data",
    archetypes=["data"],
    kinds=["metadata_component"],
    method="declared",
)

#: `02` §3.1's `metadata_component` namespace for this platform.
NAMESPACE: Final[str] = "dbt"

#: `ref('orders_daily')` inside a semantic model's `model:` line.
_REF: Final[re.Pattern[str]] = re.compile(r"\bref\s*\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\)")

_SEMANTIC_TYPE: Final[str] = "semantic_model"
_METRIC_TYPE: Final[str] = "metric"
_MODEL_TYPE: Final[str] = "model"


class SemanticModelExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files():
            ctx.budget.check()
            document = load_yaml(entry.path, ctx.text(entry))
            if document is None:
                continue
            if not any(key in document for key in declaring_keys_for(SEMANTIC_OWNER)):
                continue
            yield from _semantic_facts(document, entry.path, entry.blob_sha)
            yield from _metric_facts(document, entry.path, entry.blob_sha)


def _semantic_facts(document: Mapping[str, Any], path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    for entry in _entries(document, "semantic_models"):
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        upstream = _model_reference(entry.get("model"))
        yield _component(
            component_type=_SEMANTIC_TYPE,
            api_name=name,
            title=f"dbt semantic model {name}",
            path=path,
            blob_sha=blob_sha,
            label=_text(entry.get("description")),
            relations=[_edge(f"{_MODEL_TYPE}.{upstream}")] if upstream else [],
        )


def _metric_facts(document: Mapping[str, Any], path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    for entry in _entries(document, "metrics"):
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        yield _component(
            component_type=_METRIC_TYPE,
            api_name=name,
            title=f"dbt metric {name}",
            path=path,
            blob_sha=blob_sha,
            label=_text(entry.get("label")) or _text(entry.get("description")),
            # A metric's `type` -- simple, ratio, cumulative -- changes what the
            # number *is*, so it is recorded as the component's data type.
            data_type=_text(entry.get("type")),
            relations=[
                _edge(f"{_SEMANTIC_TYPE}.{upstream}") for upstream in _measure_owners(entry)
            ],
        )


def _entries(document: Mapping[str, Any], key: str) -> Iterator[Mapping[str, Any]]:
    section = document.get(key)
    if not isinstance(section, Sequence) or isinstance(section, str):
        return
    for entry in section:
        if isinstance(entry, Mapping):
            yield entry


def _model_reference(value: object) -> str | None:
    """`ref('orders_daily')` -> `orders_daily`; a bare name stays as it is."""
    if not isinstance(value, str):
        return None
    match = _REF.search(value)
    return match.group("name") if match else (value.strip() or None)


def _measure_owners(metric: Mapping[str, Any]) -> list[str]:
    """The semantic models a metric's measures name, in declaration order.

    dbt writes a measure reference as `type_params.measure.name` (or a list of
    them under `measures`), qualified `semantic_model.measure` when it needs to
    disambiguate. Only the qualified form names a semantic model, so only the
    qualified form produces an edge -- an unqualified measure is a name we cannot
    resolve without the project's own compilation, and guessing which semantic
    model owns it would be a lineage edge nobody declared.
    """
    owners: list[str] = []
    params = metric.get("type_params")
    if not isinstance(params, Mapping):
        return owners
    candidates: list[object] = []
    measure = params.get("measure")
    if measure is not None:
        candidates.append(measure)
    measures = params.get("measures")
    if isinstance(measures, Sequence) and not isinstance(measures, str):
        candidates.extend(measures)
    for candidate in candidates:
        name = candidate.get("name") if isinstance(candidate, Mapping) else candidate
        if isinstance(name, str) and "." in name:
            owner = name.rsplit(".", 1)[0]
            if owner and owner not in owners:
                owners.append(owner)
    return owners


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _edge(target_local_key: str) -> FactRelation:
    return FactRelation(
        predicate="derives_from",
        target_kind="metadata_component",
        target_namespace=NAMESPACE,
        target_local_key=target_local_key,
    )


def _component(
    *,
    component_type: str,
    api_name: str,
    title: str,
    path: str,
    blob_sha: str,
    label: str | None = None,
    data_type: str | None = None,
    relations: list[FactRelation] | None = None,
) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="metadata_component",
        namespace=NAMESPACE,
        local_key=f"{component_type}.{api_name}",
        title=title,
        attributes={
            key: value
            for key, value in (
                ("component_type", component_type),
                ("api_name", api_name),
                ("label", label),
                ("data_type", data_type),
            )
            if value is not None
        },
        relations=relations or [],
        source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
    )
