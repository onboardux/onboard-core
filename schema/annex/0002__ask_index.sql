-- Runtime annex, ask retrieval index -- v6.1 §6 Build 3, plan decision D5.
--
-- This file is NOT part of the canonical schema and NOT part of `schema_version`,
-- for the same reason `0001__agent_run.sql` is not: it holds a **derived**
-- artifact. Every row here is a copy of something the canonical store already
-- holds, reproducible from it at any time, and reproduced whenever the two
-- disagree. It is never exported, no bundle has a file for it, and deleting the
-- annex costs a rebuild rather than a fact about a client's system.
--
-- That is also why the index may not be canon and is not merely "not canon yet":
-- an FTS index that survived a store it no longer matches would answer questions
-- about knowledge that has been superseded, retired or unverified since -- which
-- is precisely the rot this product exists to delete. The stamp table below is
-- what makes disagreement detectable rather than invisible.
--
-- back-out: drop the annex file. Nothing to migrate, nothing to preserve.
-- annex-version: 2

-- The searchable text. `revision_id` and `item_id` are UNINDEXED because they are
-- citation keys rather than search terms: an FDE asking about "rev" must not
-- match every row by its identifier. `identity_uris` IS indexed, so a question
-- that names a canonical URI reaches the passage bound to it by ordinary search
-- as well as by exact lookup.
--
-- `unicode61` with the default separators is the tokenizer: it folds case and
-- diacritics and needs no extension. `remove_diacritics 2` is the current
-- recommended form and is spelled out rather than defaulted, because the default
-- changed between SQLite releases and an index built under one setting and
-- queried under another silently under-matches.
CREATE VIRTUAL TABLE IF NOT EXISTS ask_passage USING fts5(
    revision_id UNINDEXED,
    item_id UNINDEXED,
    title,
    body_md,
    identity_uris,
    tokenize = "unicode61 remove_diacritics 2"
);

-- What the index was built from. One row, enforced by the CHECK.
--
-- The four counters are the whole input to the index: verified head revisions
-- supply the text, bindings supply the URIs. A stamp over only the revisions
-- would miss a binding added to an unchanged document -- the index would keep
-- serving that passage without its new URI, and the miss would look like a
-- retrieval quality problem rather than a stale index.
CREATE TABLE IF NOT EXISTS ask_index_stamp (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    revision_count          INTEGER NOT NULL,
    max_revision_created_at TEXT,
    binding_count           INTEGER NOT NULL,
    max_binding_created_at  TEXT,
    built_at                TEXT NOT NULL
);
