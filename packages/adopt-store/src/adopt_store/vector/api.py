"""The `VectorIndex` seam — a protocol, and deliberately nothing else.

PRD F4.7 and implementation spec §4.7: vector access is reached only through
this interface, and **swapping the index must not touch the canonical schema or
bump `export_version`**. That is not a preference. `sqlite-vec` is pre-1.0, and
a pre-1.0 dependency reaching into the persisted shape is how a storage format
inherits someone else's breaking change.

There is no implementation in Build 0 and the feature flag is off. The `vec0`
(local) and `pgvector` (server) backings arrive behind this protocol in a later
item; `no-vector-impl` already names this module as the only place either driver
may be imported, so the contract has a target before the code does.
"""

from collections.abc import Sequence
from typing import Final, Protocol, runtime_checkable

__all__ = ["VECTOR_FEATURE_FLAG", "SearchHit", "VectorIndex"]

#: Default off, like every flag (implementation spec §7.4). Named here so the
#: flag and the seam it guards are read together.
VECTOR_FEATURE_FLAG: Final[str] = "ADOPT_FEATURE_VECTOR_INDEX"


class SearchHit(Protocol):
    """One result. `ref` is an identity URI, never an internal row id."""

    @property
    def ref(self) -> str: ...
    @property
    def score(self) -> float: ...


@runtime_checkable
class VectorIndex(Protocol):
    """The whole of the vector surface the programme is permitted to depend on."""

    def upsert(self, ref: str, embedding: Sequence[float]) -> None:
        """Add or replace the embedding for `ref`."""
        ...

    def search(self, embedding: Sequence[float], limit: int) -> Sequence[SearchHit]:
        """Nearest `limit` refs to `embedding`, best first."""
        ...

    def delete(self, ref: str) -> None:
        """Forget `ref`. Removing an embedding is not removing knowledge: the
        canonical tables are the record, and this index is derived from them."""
        ...
