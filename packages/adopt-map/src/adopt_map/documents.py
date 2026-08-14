"""Which extractor owns a document -- one home, because two would drift.

**The problem this module exists for.** `common.config` reads every YAML, JSON
and TOML file in a client tree, and pack extractors read *particular* documents:
`web.openapi` reads a published contract, `ai.retrieval` reads a retrieval
configuration, `ai.evalsets` reads an eval set. Without an arbitration rule the
same file is claimed twice -- `config_key/yaml/retrieval.top_k` beside
`retrieval_config/pgvector/orders_kb.top_k` -- which is two identities for one
setting, under two kinds, double-counted in every coverage figure.

B1-CR-67 found this with OpenAPI and fixed it inside `common.config`. S1.5 met it
twice more, so the rule moves **here**: a pack cannot import another pack (that
would make two distributions one deployable unit), and a list living in both
would be one edit away from disagreeing -- the exact "second home" `03` §3 bans
for tunables, applied to a rule instead of a number.

**The rule.** A document is owned by a pack extractor when it *declares itself*
through a top-level key. A filename is a habit -- `config/retrieval.yaml` and
`deploy/rag.yml` are the same document -- and a top-level key is a statement.
"""

import re
from typing import Final

__all__ = ["OWNED_DOCUMENT_KEYS", "declares_owned_document"]

#: Top-level keys that hand a document to a pack extractor, and the extractor
#: that claims each. Held as data with its claimant named, so a reader can tell
#: *why* `common.config` skipped a file without reading two other packages.
OWNED_DOCUMENT_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("retrieval", "ai.retrieval"),
    ("rag", "ai.retrieval"),
    ("vectorstore", "ai.retrieval"),
    ("vector_store", "ai.retrieval"),
    ("evalset", "ai.evalsets"),
    ("evals", "ai.evalsets"),
    ("eval_set", "ai.evalsets"),
    ("evaluation", "ai.evalsets"),
)

#: How much of a document is read to answer the question. A declaring key is at
#: the top, and reading the whole of a large document to find out that it is not
#: ours would cost the walk more than the answer is worth.
_HEAD_CHARS: Final[int] = 4096


def declares_owned_document(text: str) -> str | None:
    """The extractor that owns this document, or `None`.

    Args:
        text: The document's text.

    Returns:
        The claiming extractor's manifest id, so a caller that skips can say who
        it deferred to rather than merely that it declined.
    """
    head = text[:_HEAD_CHARS]
    for key, owner in OWNED_DOCUMENT_KEYS:
        if re.search(rf"^{re.escape(key)}\s*:", head, re.MULTILINE):
            return owner
    return None
