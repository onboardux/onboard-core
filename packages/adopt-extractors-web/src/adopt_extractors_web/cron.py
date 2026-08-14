"""`web.cron` -- crontab files and declared cron schedules.

`02` §3.1 gives `job` the runner as namespace (`cron`) and *"task or schedule
identifier"* as the key. A crontab line has no name, so the **command** is the
identifier: it is what runs, it is stable across a schedule change, and two lines
running the same command on different schedules are genuinely one referent
observed twice.

**Schedule changes are semantic here, and that is deliberate.** `02` §4.2 puts
`job.schedule` in the semantic projection, so moving a nightly job to hourly
writes a revision. It should: the behaviour changed.

**A commented line is not a job.** Crontabs accumulate disabled entries, and a
reader who sees them minted as live jobs learns to distrust the whole inventory.
"""

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "CronExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="web.cron",
    version="1.0.0",
    pack="web",
    archetypes=["web"],
    kinds=["job"],
    method="declared",
)

_NAMESPACE: Final[str] = "cron"

#: Filenames that hold crontab entries.
_CRONTAB_NAMES: Final[frozenset[str]] = frozenset({"crontab", "crontabs", ".crontab"})

#: `@reboot`, `@daily` and friends -- a whole schedule in one token.
_SPECIALS: Final[frozenset[str]] = frozenset(
    {"@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"}
)

#: How many whitespace-separated fields precede the command in a standard entry.
#: Nobody may revise "minute hour day month weekday" to four or six, so a knob for
#: it in `adopt_const` would have no effect except to break parsing.
# const-sync: ok -- the five fields of a crontab schedule are the cron format, not a tunable.
_SCHEDULE_FIELDS: Final[int] = 5


class CronExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        base = Path(root)
        return any(any(base.rglob(pattern)) for pattern in ("crontab", "*.cron", "crontab.*"))

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `job` per enabled crontab entry, in file then line order."""
        for entry in ctx.files():
            ctx.budget.check()
            name = PurePosixPath(entry.path).name.lower()
            if name not in _CRONTAB_NAMES and PurePosixPath(name).suffix != ".cron":
                continue
            text = ctx.text(entry)
            for line_number, line in enumerate(text.splitlines(), start=1):
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                schedule, command = parsed
                yield SurfaceFact(
                    identity_kind="job",
                    namespace=_NAMESPACE,
                    local_key=command,
                    title=command.split()[0] if command.split() else command,
                    attributes={"schedule": schedule, "target_symbol": command},
                    source_refs=[
                        SourceRef(
                            path=entry.path,
                            start_line=line_number,
                            blob_sha=entry.blob_sha,
                        )
                    ],
                )


def _parse_line(line: str) -> tuple[str, str] | None:
    """`(schedule, command)` for an enabled entry, else `None`.

    Rejects blanks, comments and `NAME=value` environment lines. A `MAILTO=` line
    has five fields as often as not, so filtering on the `=` before the first
    space is what keeps it from minting as a job whose schedule is nonsense.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    head = stripped.split(" ", 1)[0]
    if "=" in head:
        return None
    if head in _SPECIALS:
        command = stripped[len(head) :].strip()
        return (head, command) if command else None
    fields = stripped.split(maxsplit=_SCHEDULE_FIELDS)
    if len(fields) <= _SCHEDULE_FIELDS:
        return None
    schedule = " ".join(fields[:_SCHEDULE_FIELDS])
    return schedule, fields[_SCHEDULE_FIELDS].strip()
