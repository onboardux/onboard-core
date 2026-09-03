"""SQLite FTS5 realization of `adopt_ask.SearchRecords`. v6.1 §6 Build 3 (F1).

**The index is derived, and this module is the only thing that may say so.** It
reads the canonical store and writes the annex; nothing here writes canon, and
there is no method that could. The direction is one-way by construction rather
than by discipline, which is what keeps the one-canon rule (R9) true while a
second searchable copy of the text exists on disk.

**What gets indexed, and the invariant hiding in that sentence.** Only the
**verified head** revision of each knowledge item. `head` because a superseded
revision is not what the store would serve; `verified` because F6 says
unverified knowledge never serves as KNOWN, and the cheapest way to honour a
never is to make the alternative unrepresentable. `adopt_ask.branch` filters on
verification a second time. That is not redundancy for its own sake: this module
enforces it at build time and the branch enforces it at serve time, and the two
fail differently -- a stale index defeats the first and nothing defeats the
second.

**What does *not* get filtered here.** Retired items, stale items and passages
bound to dead identities are all indexed. Freshness is not a retrieval concern:
the demo requires a STALE answer to be *served* with its cause named, so an
index that hid stale passages would turn "here is what we knew, and why it may
be wrong" into "we do not know" -- a refusal that destroys information the store
actually holds. `resolve_freshness` decides; the index only finds.
"""

import datetime as _dt
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

from adopt_ask.records import Passage, RefreshOutcome
from adopt_ask.retrieve import content_terms

from adopt_obs import Clock, SystemClock
from adopt_store.annex.sqlite_annex import annex_path, connect_annex
from adopt_store.sqlite.store import SqliteStore

__all__ = ["SqliteSearchRecords", "open_search"]

#: The verification value that may serve as KNOWN. Plan decision D1: v6.1's
#: prose says "confirmed", the machine-gated `verification` enum says `verified`,
#: and the artifact wins.
_VERIFIED: Final[str] = "verified"

#: Head revisions carrying their item's title. The head pointer is
#: `knowledge_item.current_revision_id` -- a pointer with no FK (CR-07), which is
#: why this is a join rather than a subquery on `supersedes_revision_id`.
_HEAD_PASSAGES: Final[str] = (
    "SELECT ki.id AS item_id, ki.title AS title, "
    "kr.id AS revision_id, kr.body_md AS body_md "
    "FROM knowledge_item ki "
    "JOIN knowledge_revision kr ON kr.id = ki.current_revision_id "
    "WHERE kr.verification = ? "
    "ORDER BY ki.id"
)

#: Every URI bound to an item. Retired bindings are included deliberately: a
#: question that names a URI should still reach the document that described it,
#: and freshness is what says the answer has gone stale.
_ITEM_URIS: Final[str] = (
    "SELECT b.item_id AS item_id, i.uri AS uri "
    "FROM binding b JOIN identity i ON i.id = b.identity_id "
    "ORDER BY b.item_id, i.uri"
)

#: The stamp's inputs. Every revision, not only the verified heads: revisions are
#: append-only, so any change to what the index should hold -- a new document, an
#: edit, a retirement, a review confirmation flipping verification -- arrives as
#: a new row here. Counting only verified heads would miss the confirmation that
#: makes a passage servable, which is the single most important index update
#: this product performs.
_REVISION_STAMP: Final[str] = (
    "SELECT COUNT(*) AS n, MAX(created_at) AS latest FROM knowledge_revision"
)
_BINDING_STAMP: Final[str] = "SELECT COUNT(*) AS n, MAX(created_at) AS latest FROM binding"


def _match_expression(query: str) -> str:
    """An FTS5 MATCH expression that cannot be a syntax error.

    Terms come from `adopt_ask.retrieve.content_terms`, which is above the port
    and is the single definition of "the words this question is about" -- the
    ranking layer applies its coverage rule to the same list, and two
    tokenizations would mean the filter judging different words than the search
    found.

    Each term is double-quoted, which makes it a literal string in FTS5 grammar
    even when it collides with a keyword like `OR` or `NEAR`; internal double
    quotes are doubled. Terms are OR-ed rather than AND-ed because a question is
    a sentence, and requiring every word of it to appear would answer almost
    nothing -- there is no stemmer here, so "exist" would miss "exists".
    Breadth here, judgement above the port.
    """
    terms = content_terms(query)
    if not terms:
        return ""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


@contextmanager
def open_search(
    store: SqliteStore, *, repo_root: Path | None = None, clock: Clock | None = None
) -> Iterator["SqliteSearchRecords"]:
    """Open the annex beside `store` and yield its retrieval port.

    The annex is resolved from the store's own path, so the index and the canon
    it derives from always travel together. An index beside a *different* store
    would answer questions about knowledge that store never held.
    """
    connection = connect_annex(annex_path(store.path), repo_root=repo_root)
    try:
        yield SqliteSearchRecords(store, connection, clock=clock)
    finally:
        connection.close()


class SqliteSearchRecords:
    """Realizes `adopt_ask.SearchRecords` structurally.

    Holds two handles on purpose: the canonical store, read-only in practice and
    never written here, and the annex connection, which is the only thing this
    class writes.
    """

    def __init__(
        self,
        store: SqliteStore,
        annex: sqlite3.Connection,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._annex = annex
        self._clock: Clock = clock if clock is not None else SystemClock()

    # -- refresh ----------------------------------------------------------

    def _store_stamp(self) -> tuple[int, str | None, int, str | None]:
        revisions = self._store.query(_REVISION_STAMP)[0]
        bindings = self._store.query(_BINDING_STAMP)[0]
        return (
            int(revisions["n"]),
            None if revisions["latest"] is None else str(revisions["latest"]),
            int(bindings["n"]),
            None if bindings["latest"] is None else str(bindings["latest"]),
        )

    def _index_stamp(self) -> tuple[int, str | None, int, str | None] | None:
        with closing(
            self._annex.execute(
                "SELECT revision_count, max_revision_created_at, "
                "binding_count, max_binding_created_at FROM ask_index_stamp WHERE id = 1;"
            )
        ) as cursor:
            row = cursor.fetchone()
        if row is None:
            return None
        return (
            int(row["revision_count"]),
            None if row["max_revision_created_at"] is None else str(row["max_revision_created_at"]),
            int(row["binding_count"]),
            None if row["max_binding_created_at"] is None else str(row["max_binding_created_at"]),
        )

    def refresh(self, *, force: bool = False) -> RefreshOutcome:
        """Rebuild the index when it is absent, disagrees with the store, or is forced.

        The comparison is a stamp rather than a diff because the index is cheap
        to rebuild and expensive to reason about half-updated. An incremental
        path would have to be right about every way a revision can change what
        is servable; a full rebuild is right by construction, and the stamp is
        only ever asked whether it is equal.
        """
        current = self._store_stamp()
        if not force:
            stored = self._index_stamp()
            if stored is None:
                return self._rebuild(current, reason="no index")
            if stored == current:
                return RefreshOutcome(rebuilt=False, indexed=self._count(), reason="current")
            return self._rebuild(current, reason="store changed since the index was built")
        return self._rebuild(current, reason="forced")

    def _count(self) -> int:
        with closing(self._annex.execute("SELECT COUNT(*) AS n FROM ask_passage;")) as cursor:
            return int(cursor.fetchone()["n"])

    def _rebuild(
        self, stamp: tuple[int, str | None, int, str | None], *, reason: str
    ) -> RefreshOutcome:
        """Empty the index and refill it from the store, in one annex transaction.

        Emptying and refilling together matters: a rebuild that failed after the
        delete would leave an empty index whose stamp said it was current, and
        every question would then answer UNKNOWN over a store full of knowledge.
        The stamp is written last for the same reason -- if anything above it
        raises, the next run finds no stamp and rebuilds.
        """
        uris: dict[str, list[str]] = {}
        for row in self._store.query(_ITEM_URIS):
            uris.setdefault(str(row["item_id"]), []).append(str(row["uri"]))

        rows = self._store.query(_HEAD_PASSAGES, (_VERIFIED,))
        self._annex.execute("BEGIN;")
        try:
            # The annex index is derived, not canon: it is rebuilt from the store
            # on every disagreement, so emptying it loses nothing the refill below
            # does not put back, inside the same transaction. A predicate would be
            # decoration -- the operation *is* "discard all of it".
            # no-destructive-sql: ok -- derived index, refilled in this transaction
            self._annex.execute("DELETE FROM ask_passage;")
            self._annex.executemany(
                "INSERT INTO ask_passage "
                "(revision_id, item_id, title, body_md, identity_uris) "
                "VALUES (?, ?, ?, ?, ?);",
                [
                    (
                        str(row["revision_id"]),
                        str(row["item_id"]),
                        str(row["title"]),
                        "" if row["body_md"] is None else str(row["body_md"]),
                        " ".join(uris.get(str(row["item_id"]), ())),
                    )
                    for row in rows
                ],
            )
            self._annex.execute(
                "INSERT INTO ask_index_stamp "
                "(id, revision_count, max_revision_created_at, binding_count, "
                "max_binding_created_at, built_at) VALUES (1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "revision_count = excluded.revision_count, "
                "max_revision_created_at = excluded.max_revision_created_at, "
                "binding_count = excluded.binding_count, "
                "max_binding_created_at = excluded.max_binding_created_at, "
                "built_at = excluded.built_at;",
                (*stamp, _stamp_time(self._clock)),
            )
        except BaseException:
            self._annex.execute("ROLLBACK;")
            raise
        self._annex.execute("COMMIT;")
        return RefreshOutcome(rebuilt=True, indexed=len(rows), reason=reason)

    # -- reads ------------------------------------------------------------

    def search(self, query: str, *, limit: int) -> Sequence[Passage]:
        """Ranked retrieval, best first.

        `bm25()` is lower-is-better and negative in FTS5; the sign is flipped
        here so every caller above the port compares scores the obvious way.
        Column weights are left at their defaults deliberately -- weighting title
        over body is a tunable whose correct value needs usage data we do not
        have, and R5 says ship the unweighted version until it does.
        """
        expression = _match_expression(query)
        if not expression:
            return ()
        with closing(
            self._annex.execute(
                "SELECT revision_id, item_id, title, body_md, identity_uris, "
                "bm25(ask_passage) AS rank FROM ask_passage "
                "WHERE ask_passage MATCH ? ORDER BY rank LIMIT ?;",
                (expression, limit),
            )
        ) as cursor:
            return tuple(_passage(row, score=-float(row["rank"])) for row in cursor)

    def verified_in_store(self, revision_ids: Sequence[str]) -> frozenset[str]:
        """Which of `revision_ids` the canonical store reports as verified.

        Reads `knowledge_revision`, never `ask_passage`. The index holds only
        verified revisions by construction, so asking *it* would always answer
        "all of them" -- a check that cannot fail, which is the shape of every
        blind gate this repository has found.
        """
        if not revision_ids:
            return frozenset()
        placeholders = ", ".join("?" for _ in revision_ids)
        rows = self._store.query(
            # S608: `placeholders` emits only `?` characters, one per id.
            "SELECT id FROM knowledge_revision "  # noqa: S608
            f"WHERE id IN ({placeholders}) AND verification = ?",
            (*revision_ids, _VERIFIED),
        )
        return frozenset(str(row["id"]) for row in rows)

    def lookup_uris(self, uris: Sequence[str]) -> Sequence[Passage]:
        """Every indexed passage bound to any of `uris`.

        Matched against the stored URI list rather than through FTS, because a
        canonical URI is an exact key: `…/orders-api/prod/endpoint/-/POST /v1/orders`
        tokenizes into words that also appear in unrelated documents, and a
        substring of one URI is never a match for another.
        """
        if not uris:
            return ()
        wanted = set(uris)
        with closing(
            self._annex.execute(
                "SELECT revision_id, item_id, title, body_md, identity_uris FROM ask_passage;"
            )
        ) as cursor:
            rows = cursor.fetchall()
        return tuple(
            _passage(row)
            for row in rows
            if wanted.intersection(str(row["identity_uris"]).split(" "))
        )


def _stamp_time(clock: Clock) -> str:
    moment = clock.now()
    if moment.tzinfo is None:  # pragma: no cover -- every injected clock is aware
        moment = moment.replace(tzinfo=_dt.UTC)
    return moment.isoformat()


def _passage(row: sqlite3.Row, *, score: float = 0.0) -> Passage:
    stored = str(row["identity_uris"])
    return Passage(
        revision_id=str(row["revision_id"]),
        item_id=str(row["item_id"]),
        title=str(row["title"]),
        body_md=str(row["body_md"]),
        identity_uris=tuple(uri for uri in stored.split(" ") if uri),
        score=score,
    )
