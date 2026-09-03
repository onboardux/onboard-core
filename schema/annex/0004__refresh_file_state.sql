-- Runtime annex, refresh file-state snapshot -- v6.1 §6 Build 6, plan decision D7.
--
-- This file is NOT part of the canonical schema and NOT part of `schema_version`,
-- for the reason the other three annex files are not: every row is **derived**
-- and reproducible by re-walking the tree. It is never exported, no bundle has a
-- file for it, and losing it costs one degraded run rather than a fact about a
-- client's system.
--
-- What it holds: the sha256 of each walked file at the end of the last refresh,
-- per scope. Its only consumer is the RENDER-ONLY class -- "this file changed
-- and every identity extracted from it kept its attribute digest" -- which is
-- the class that proves a comment-only edit was *seen and judged cosmetic*
-- rather than missed. Nothing else may read it, and nothing may treat it as
-- evidence about identities: the attribute digest is the only authority on
-- whether a referent changed (H5), and a file hash that could stale knowledge
-- would reintroduce exactly the false staleness H5 exists to delete.
--
-- Why the annex rather than the canonical store: a file hash is a fact about a
-- working tree at a moment, not about the client's system. Putting it in the
-- manifest would export a listing of every file path in the client's repository
-- inside a bundle, which is a disclosure nobody asked for and the boundary
-- would have to be widened to permit.
--
-- Absence is honest: a store with no snapshot reports RENDER-ONLY as
-- *unavailable* rather than as zero. "No cosmetic changes" and "we could not
-- tell" are different statements and the first must never be printed for the
-- second.
--
-- back-out: drop the annex file. Nothing to migrate, nothing to preserve.
-- annex-version: 1

CREATE TABLE IF NOT EXISTS refresh_file_state (
  scope_ref  TEXT NOT NULL,          -- firm/engagement/system/environment slugs
  path       TEXT NOT NULL,          -- repo-relative, forward slashes
  sha256     TEXT NOT NULL,          -- of the file's bytes, not of its text
  observed_at TEXT NOT NULL,
  PRIMARY KEY (scope_ref, path)
);
