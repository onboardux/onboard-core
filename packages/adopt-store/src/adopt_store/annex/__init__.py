"""The runtime annex realization: contracts §12, CR-08, CR-45.

**Why this lives in `adopt-store` and not in `adopt-agent`.** `no-raw-sqlite`
names `adopt_agent` a source module and follows indirect chains, so the seam
cannot reach a driver even through a helper. `adopt-store` is the package the
contract permits to hold a dialect, so the annex realization is here and
`adopt_agent.AnnexRecords` is what the seam actually talks to -- the CR-34
pattern, realized **structurally**: nothing in this module imports the protocol,
and `test_annex.py` asserts the shapes still match.

**This is a second store, not a second schema authority.** `open_annex` opens
`.adopt/runtime.db`, applies `schema/annex/0001__agent_run.sql`, and never
touches `PRAGMA user_version` -- the annex is outside `schema_version` by
ratification (CR-08), and stamping it with a version number is precisely the
confusion that would invite someone to migrate the two together.

**It is never reachable from the export.** `adopt_export` iterates
`Manifest.exportable_tables()` and `agent_run` is not in the manifest at all, so
exclusion is by construction rather than by a filter someone could forget --
which is the same argument S5 recorded for the annex in the first place.
"""

from adopt_store.annex.sqlite_annex import SqliteAnnexRecords, annex_path, open_annex

__all__ = ["SqliteAnnexRecords", "annex_path", "open_annex"]
