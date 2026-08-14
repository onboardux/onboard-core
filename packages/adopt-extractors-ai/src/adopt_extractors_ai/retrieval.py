"""`ai.retrieval` -- the retrieval configuration, one identity per parameter.

`02` §3.1 fixes the key as `<index>.<parameter-path>` and the namespace as the
store: `/retrieval_config/pgvector/orders_kb.top_k`. **One identity per
parameter, not per index**, and the convention is doing real work: a chunk size
and a top-k are separately addressable settings that change independently, and a
single `orders_kb` identity would make "the retrieval config changed" the only
sentence this build could ever say about them.

Every fact carries the store and the index as well as its own parameter, because
`02` §4.2's semantic projection names *"store, index, top-k, embedding model,
chunk size and overlap, rerank model"* for the kind: the digest of a `top_k`
identity that moved to a different index has to change, and it only can if the
index is inside the projection.

**Method is `declared`, not `grammar`.** The evidence is a declaration in a
configuration document. `common.config` made the same call for the same reason:
*"Calling it `grammar` would claim a parse we did not do."*
"""

from collections.abc import Iterator, Mapping
from typing import Any, Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

from adopt_extractors_ai._documents import RETRIEVAL_KEYS, declared_section, load_document

__all__ = ["MANIFEST", "RetrievalExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="ai.retrieval",
    version="1.0.0",
    pack="ai",
    archetypes=["ai"],
    kinds=["retrieval_config"],
    method="declared",
)

#: The parameters `02` §4.2 names, and the spellings a client is likely to use.
#: An unlisted key is **not** minted: this pack enumerates a known vocabulary
#: rather than every leaf of a document, which is the bound B1-CR-67 had to add
#: to `common.config` after the fact.
_PARAMETERS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("top_k", ("top_k", "topk", "k")),
    ("embedding_model", ("embedding_model", "embeddings_model", "embedding")),
    ("chunk_size", ("chunk_size", "chunk")),
    ("chunk_overlap", ("chunk_overlap", "overlap")),
    ("rerank_model", ("rerank_model", "reranker", "rerank")),
    ("display_label", ("display_label", "label", "title")),
)

_STORE_KEYS: Final[tuple[str, ...]] = ("store", "provider", "backend", "vector_store")
_INDEX_KEYS: Final[tuple[str, ...]] = ("index", "collection", "namespace", "table")

#: What `02` §3.1's namespace column admits for this kind, plus the spellings a
#: document uses for them. A store outside this list is still minted -- the
#: namespace names the store and inventing one would be worse -- but it is
#: normalized so `PGVector` and `pgvector` are one namespace.
_STORE_ALIASES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("pgvector", ("pgvector", "postgres", "pg")),
    ("qdrant", ("qdrant",)),
    ("sqlite-vec", ("sqlite-vec", "sqlite_vec", "sqlitevec")),
)


class RetrievalExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files():
            ctx.budget.check()
            document = load_document(entry.path, ctx.text(entry))
            if document is None:
                continue
            section = declared_section(document, RETRIEVAL_KEYS)
            if section is None:
                continue
            store = _first(section, _STORE_KEYS) or "unknown"
            index = _first(section, _INDEX_KEYS)
            if index is None:
                # Without an index there is no key to mint: `<index>.<parameter>`
                # with an empty half would collide across every document in the
                # tree. Recorded as nothing rather than as a guess.
                continue
            namespace = _namespace(str(store))
            for canonical, spellings in _PARAMETERS:
                value = _first(section, spellings)
                if value is None:
                    continue
                yield _fact(
                    namespace=namespace,
                    store=str(store),
                    index=str(index),
                    parameter=canonical,
                    value=value,
                    path=entry.path,
                    blob_sha=entry.blob_sha,
                )


def _fact(
    *,
    namespace: str,
    store: str,
    index: str,
    parameter: str,
    value: Any,
    path: str,
    blob_sha: str,
) -> SurfaceFact:
    attributes: dict[str, object] = {"store": store, "index": index}
    attributes[parameter] = _typed(parameter, value)
    return SurfaceFact(
        identity_kind="retrieval_config",
        namespace=namespace,
        local_key=f"{index}.{parameter}",
        title=f"{index} {parameter.replace('_', ' ')}",
        attributes=attributes,
        source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
    )


def _typed(parameter: str, value: Any) -> object:
    """The value in the type its attribute field declares.

    `top_k`, `chunk_size` and `chunk_overlap` are integers on
    `RetrievalConfigAttributes`; everything else is a string. A YAML document
    that spells `top_k: "8"` still validates, and a document that spells it
    `top_k: eight` produces no fact for that parameter rather than a
    `MAP_EXTRACTOR_FAILED` that would take the whole extractor down (`02` §7
    obligation 8).
    """
    if parameter in {"top_k", "chunk_size", "chunk_overlap"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return str(value)


def _first(section: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in section and section[key] is not None:
            return section[key]
    return None


def _namespace(store: str) -> str:
    lowered = store.lower()
    for canonical, aliases in _STORE_ALIASES:
        if any(alias in lowered for alias in aliases):
            return canonical
    return lowered
