"""`adopt map --report` -- rendered from the store, and honest about its reach.

**From the store, never from the walk** (v6.1 §6). The distinction is the whole
value of the feature: a report built from the observation stream tells you what
this run just found, which is a restatement of the run's own output and agrees
with itself by construction. A report built from the rows tells you what the
system *has recorded*, which is the thing every later build queries, and it
disagrees with the run whenever a write was refused, a transaction rolled back,
or an identity was created by an earlier run and no longer exists in the code.

The single exception is the walked-but-unmapped count, which has no home in the
store because a file that produced nothing produces no row. It comes from the
current walk and is labelled as such.

**Ordering is total and declared.** Kind, then URI, both ascending. A report
whose row order depended on a query plan would show a diff on every run and
teach its reader to ignore diffs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["StoredRevision", "build_report", "chain_summary"]


@dataclass(frozen=True, slots=True)
class StoredRevision:
    """The provenance an `identity_revision` carries, flattened for rendering."""

    identity_id: str
    extractor: str | None
    extractor_version: str | None
    source_ref: str | None
    source_version: str | None
    status: str
    #: Ordering key within one identity's chain. ISO text is enough: the chain
    #: is append-only and `id` breaks a same-millisecond tie deterministically.
    created_at: str
    revision_id: str


def build_report(
    *,
    identities: Sequence[Any],
    revisions: Sequence[StoredRevision],
    files_walked: int,
    files_unmapped: int,
    files_oversized: Sequence[str] = (),
) -> dict[str, Any]:
    """The `--report` payload: counts by kind, a provenance listing, and reach.

    Args:
        identities: `identity` rows in scope, as the generated model. Typed
            loosely on purpose -- this module renders whatever the store's own
            model says, and pinning the type here would make `adopt_map` a
            second opinion about the shape of a row.
        revisions: `identity_revision` rows for those identities.
        files_walked: From the current walk.
        files_unmapped: From the current walk -- walked files no extractor drew
            an observation from.
        files_oversized: Files skipped for exceeding the read cap, by path.

    Returns:
        A payload dict, deterministically ordered at every level.
    """
    chains = chain_summary(revisions)

    counts: dict[str, int] = {}
    listing: list[dict[str, Any]] = []
    for identity in sorted(identities, key=lambda row: (str(row.identity_kind), str(row.uri))):
        kind = str(identity.identity_kind)
        counts[kind] = counts.get(kind, 0) + 1
        chain = chains.get(str(identity.id))
        revision = chain[0] if chain else None
        listing.append(
            {
                "uri": str(identity.uri),
                "kind": kind,
                "namespace": identity.namespace,
                "key": str(identity.local_key),
                "extractor": revision.extractor if revision else None,
                "extractor_version": revision.extractor_version if revision else None,
                "source_ref": revision.source_ref if revision else None,
                # Provenance from the creating revision, status from the head:
                # they answer different questions. "Where did we first see this"
                # and "is this still where it lives" are both things a reader
                # needs, and a moved identity is precisely where they differ.
                "status": chain[1].status if chain else None,
            }
        )

    return {
        "identities": len(listing),
        "counts_by_kind": dict(sorted(counts.items())),
        "files_walked": files_walked,
        "files_unmapped": files_unmapped,
        "files_oversized": list(files_oversized),
        "listing": listing,
    }


def chain_summary(
    revisions: Sequence[StoredRevision],
) -> Mapping[str, tuple[StoredRevision, StoredRevision]]:
    """`identity_id -> (creating revision, head revision)`.

    **Both ends, because they answer different questions.** Provenance is the
    *earliest* revision: where the referent was first seen. Status is the
    *head*: whether it is still there. Reading provenance off the head would
    blank it for every moved identity -- a `moved` revision carries an alias and
    no extractor -- which is exactly the set whose history matters most.

    One pass, one ordering rule: `created_at` then `revision_id`. The chain is
    append-only, so the id breaks a same-millisecond tie the same way on every
    machine.
    """
    chains: dict[str, tuple[StoredRevision, StoredRevision]] = {}
    for revision in sorted(revisions, key=lambda row: (row.created_at, row.revision_id)):
        existing = chains.get(revision.identity_id)
        chains[revision.identity_id] = (
            (existing[0] if existing else revision),
            revision,
        )
    return chains
