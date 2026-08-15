"""`data.dbt` -- models, sources and the lineage between them.

`01` F8.5: *"Data: models, sources, metrics (`metadata_component` with a data
namespace) **plus lineage relations**."* A dbt project is the one archetype in
this build where the upstream/downstream direction is written down by the client
rather than inferred, so it is the one place lineage can be recorded without
guessing.

**A model is a `.sql` file, not a `schema.yml` entry.** dbt's own rule: the file
is what creates the model, and `schema.yml` documents models that already exist.
Keying off the schema file would miss every undocumented model -- which in a real
warehouse is most of them -- and would report a *documented* surface as if it
were the surface. So the `.sql` files are enumerated and the schema file supplies
descriptions where it has them.

**Lineage direction, stated once so it cannot drift.** `derives_from` points
**from the derived thing to what it was derived from**: a model that selects from
`ref('stg_orders')` gets `model.orders_daily --derives_from--> model.stg_orders`.
`02` §5.2 is explicit that *"the framework does not auto-create inverses -- an
extractor emits the direction it observed"*, and the direction observed in a
`ref()` call is "I depend on that".

**`ref()` and `source()` are read with a regex, and that is honest rather than
lazy.** They are Jinja calls inside SQL: the SQL grammar cannot see them (they
are not SQL), and the Jinja is not a language this build carries a grammar for.
The regex reads two call shapes dbt itself defines, and the extractor's declared
`method` is `declared` because what it reports is the project's own declaration
of its dependencies.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, FactRelation, SourceRef, SurfaceFact

from adopt_extractors_data._documents import DBT_OWNER, declaring_keys_for, load_yaml

__all__ = ["MANIFEST", "NAMESPACE", "DbtExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="data.dbt",
    version="1.0.0",
    pack="data",
    archetypes=["data"],
    kinds=["metadata_component"],
    method="declared",
)

#: `02` §3.1's `metadata_component` namespace for this platform.
NAMESPACE: Final[str] = "dbt"

#: `{{ ref('stg_orders') }}` and `{{ ref("stg_orders") }}`, with or without
#: whitespace. A package-qualified `ref('pkg', 'model')` takes the **last**
#: argument, which is the model name in dbt's own two-argument form.
_REF: Final[re.Pattern[str]] = re.compile(
    r"\bref\s*\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"](?P<name>[^'\"]+)['\"]\s*\)"
)

#: `{{ source('raw', 'orders') }}` -- always two arguments in dbt.
_SOURCE: Final[re.Pattern[str]] = re.compile(
    r"\bsource\s*\(\s*['\"](?P<source>[^'\"]+)['\"]\s*,\s*['\"](?P<table>[^'\"]+)['\"]\s*\)"
)

_MODEL_TYPE: Final[str] = "model"
_SOURCE_TYPE: Final[str] = "source"


class DbtExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        described: dict[str, str] = {}
        sources: list[tuple[str, str, str, str, str | None]] = []

        # Pass one: the project's declarations. Two passes over an index already
        # in memory, not two walks -- `03` §5.8's guarantee is about the walk.
        for entry in ctx.files():
            ctx.budget.check()
            document = load_yaml(entry.path, ctx.text(entry))
            if document is None or not _is_dbt_document(document):
                continue
            described.update(_descriptions(document))
            sources.extend(_sources(document, entry.path, entry.blob_sha))

        for source_name, table, path, blob_sha, description in sources:
            yield _component(
                component_type=_SOURCE_TYPE,
                api_name=f"{source_name}.{table}",
                title=f"dbt source {source_name}.{table}",
                path=path,
                blob_sha=blob_sha,
                label=description,
            )

        # Pass two: the models themselves, with the lineage each one declares.
        for entry in ctx.files():
            ctx.budget.check()
            if not entry.path.endswith(".sql"):
                continue
            name = PurePosixPath(entry.path).stem
            text = ctx.text(entry)
            yield _component(
                component_type=_MODEL_TYPE,
                api_name=name,
                title=f"dbt model {name}",
                path=entry.path,
                blob_sha=entry.blob_sha,
                label=described.get(name),
                relations=_lineage(text),
            )


def _is_dbt_document(document: Mapping[str, Any]) -> bool:
    """Whether this YAML document is one `adopt_map.documents` gives this reader.

    Asked against the *loaded* document rather than against its head text,
    because by this point we have parsed it anyway -- and asked against that
    table rather than a list here, so the set of documents `common.config` skips
    and the set this reader claims cannot diverge.
    """
    return any(key in document for key in declaring_keys_for(DBT_OWNER))


def _descriptions(document: Mapping[str, Any]) -> dict[str, str]:
    """`{model name: description}` for every documented model."""
    found: dict[str, str] = {}
    models = document.get("models")
    if not isinstance(models, Sequence) or isinstance(models, str):
        return found
    for model in models:
        if not isinstance(model, Mapping):
            continue
        name = model.get("name")
        description = model.get("description")
        if isinstance(name, str) and isinstance(description, str) and description.strip():
            found[name] = description.strip()
    return found


def _sources(
    document: Mapping[str, Any], path: str, blob_sha: str
) -> list[tuple[str, str, str, str, str | None]]:
    """Every `sources: -> tables:` entry, as `(source, table, path, sha, text)`."""
    found: list[tuple[str, str, str, str, str | None]] = []
    sources = document.get("sources")
    if not isinstance(sources, Sequence) or isinstance(sources, str):
        return found
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        source_name = source.get("name")
        tables = source.get("tables")
        if not isinstance(source_name, str) or not isinstance(tables, Sequence):
            continue
        for table in tables:
            if not isinstance(table, Mapping):
                continue
            table_name = table.get("name")
            if not isinstance(table_name, str):
                continue
            description = table.get("description")
            found.append(
                (
                    source_name,
                    table_name,
                    path,
                    blob_sha,
                    description.strip()
                    if isinstance(description, str) and description.strip()
                    else None,
                )
            )
    return found


def _lineage(sql: str) -> list[FactRelation]:
    """`derives_from` edges for every `ref()` and `source()` in one model.

    De-duplicated and emitted in first-appearance order: a model selecting from
    one upstream twice declares one dependency, and document order is what makes
    two runs agree without a sort (`02` §7 obligation 3).
    """
    relations: list[FactRelation] = []
    seen: list[str] = []
    for match in _REF.finditer(sql):
        key = f"{_MODEL_TYPE}.{match.group('name')}"
        if key not in seen:
            seen.append(key)
            relations.append(_edge(key))
    for match in _SOURCE.finditer(sql):
        key = f"{_SOURCE_TYPE}.{match.group('source')}.{match.group('table')}"
        if key not in seen:
            seen.append(key)
            relations.append(_edge(key))
    return relations


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
                # A dbt description is written by a person for a person, which
                # is what a label is. Its absence leaves the model unlabelled
                # rather than labelled with its own file name (`01` §8).
                ("label", label),
            )
            if value is not None
        },
        relations=relations or [],
        source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
    )
