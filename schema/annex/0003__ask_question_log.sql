-- Runtime annex, passive question log -- v6.1 §6 Build 3 F2, plan decision D5.
--
-- **Written only when `ADOPT_ASK_LOG_QUESTIONS=1`, which is not the default.**
-- The table exists on every store because the annex DDL is applied whole and
-- idempotently; its emptiness on a default install is the control working, not
-- a feature missing.
--
-- Why here rather than in canon: a question is not knowledge. It is a record of
-- what somebody did not know on a client engagement, it is never exported, it
-- has no revision chain and nothing cites it. Putting it in the canonical store
-- would make it exportable by default and would give a client bundle a
-- transcript of their delivery team's uncertainty.
--
-- Why here rather than in the structured log: `adopt_obs`'s deny-list drops a
-- `question` field by design (`03` §4.2), so there is no compliant way to write
-- one to a log line -- and a deny-list with an exemption carved into it for the
-- one field somebody wanted is not a deny-list. The annex is what is left, and
-- it is also the right answer: rows here are local, disposable and outside
-- `schema_version`.
--
-- **The escalation table is the other half and is deliberately not this one.**
-- An escalation is consented, canonical and answerable; a row here is none of
-- those. Merging them would mean either exporting questions nobody agreed to
-- store, or making a consented open question undeletable-but-invisible.
--
-- back-out: drop the annex file. Nothing to migrate, nothing to preserve.
-- annex-version: 3

CREATE TABLE IF NOT EXISTS ask_question_log (
    id         TEXT PRIMARY KEY,
    scope_ref  TEXT NOT NULL,
    question   TEXT NOT NULL,
    branch     TEXT NOT NULL,
    -- How many revisions the answer cited. A count rather than the ids: this
    -- table's purpose is spotting questions the store keeps failing to answer,
    -- and that needs the branch and the words, not a join back into canon.
    citations  INTEGER NOT NULL,
    asked_at   TEXT NOT NULL
);

-- Passive logging is read as "what do people keep asking that we cannot
-- answer", which is a scan by scope and time, never a lookup by id.
CREATE INDEX IF NOT EXISTS idx_ask_question_log_scope
    ON ask_question_log (scope_ref, asked_at);
