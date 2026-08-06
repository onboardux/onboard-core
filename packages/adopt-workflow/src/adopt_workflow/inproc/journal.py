"""The in-process backend's durable record: one append-only NDJSON file.

**Why a file and not a database.** Three rules meet here and leave exactly one
option. `no-raw-sqlite` names `adopt_workflow` a source module and follows
indirect chains, so this package cannot reach a driver even through a helper.
`no-foreign-tables` forbids a `CREATE TABLE` outside `schema/migrations` and the
manifest emitters. And CR-11 keeps workflow execution state out of the canonical
schema entirely, because it is not client knowledge and must never appear in a
handover bundle. A file journal satisfies all three and needs no schema, no
driver and no migration.

**Why append-only.** The same reason the four revision families are: a record
that can be rewritten is a record a crash can half-rewrite. Every line is written
with one `os.write` to a file opened `O_APPEND` and then `fsync`ed, so a kill
between two lines loses the second and never corrupts the first.

**The effect boundary is a journal append.** `dedupe` writes an `effect` record
and returns `True` only on the first one. That the record *is* the effect is what
makes it exactly-once: writing a dedupe marker before the caller's side effect
loses the effect when a crash lands between them, and writing it afterwards
duplicates the effect instead. A caller whose side effect is external -- a
payment, an email -- needs a backend whose transaction can enclose both, which is
what the DBOS backend on Postgres provides. That limit is stated rather than
hidden: this backend exists for CI and local development (`03` §4.14).
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

__all__ = ["Journal", "JournalRecord"]

#: One record per line. Keys sorted so a journal written by two processes at
#: different times compares byte-for-byte in a test.
_JSON_ARGS: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}

JournalRecord = dict[str, Any]


class Journal:
    """Append-only NDJSON, fsynced per record."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: JournalRecord) -> None:
        """Write one record durably. One `write`, then one `fsync`.

        `O_APPEND` makes the write atomic against other writers for a line this
        size, so a reader never sees half a record even mid-crash.
        """
        line = (json.dumps(record, **_JSON_ARGS) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def records(self) -> Iterator[JournalRecord]:
        """Every complete record, in order.

        A trailing partial line -- the signature of a kill mid-write -- is
        skipped rather than raising. Refusing to open a journal because the last
        write was interrupted would make the crash unrecoverable, which is the
        opposite of what a durable log is for.
        """
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.endswith("\n"):
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:  # pragma: no cover -- partial line
                    continue
                if isinstance(parsed, dict):
                    yield parsed
