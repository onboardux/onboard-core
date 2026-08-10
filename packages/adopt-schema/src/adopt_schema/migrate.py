"""Forward-only migrations, one transaction per file, each with a back-out note.

Two design points, both learned here rather than assumed:

**Atomicity belongs to the target, not to this module.** An earlier shape exposed
`begin`/`executescript`/`commit` and drove the transaction from here. SQLite's
`executescript` *implicitly commits* any open transaction before it runs, so that
shape silently broke the one-transaction-per-file guarantee it was written to
provide: a migration failing halfway would have left every statement before the
failure applied. `apply_migration` therefore hands the whole unit to the target,
which owns its dialect's transaction semantics.

**A migration declares the schema version it produces.** The file number orders
migrations; it is not a version. `0001__init_v3.sql` produces version 3, and the
next migration might produce 4 -- or might be the second of three files that all
produce 4. Deriving one from the other works exactly until it doesn't.

There are no down-migrations, permanently. Recovery from a newer store is older
code opening it read-only, which is the property additive-only keeps true.
"""

import re
from pathlib import Path
from typing import Final, Protocol

from adopt_const import EXPORT_VERSION, SCHEMA_VERSION
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "BACK_OUT_MARKER",
    "SCHEMA_VERSION_MARKER",
    "MigrationTarget",
    "apply",
    "declared_version",
    "migrations_dir",
    "new_migration",
    "pending",
]

BACK_OUT_MARKER: Final[str] = "-- back-out:"
SCHEMA_VERSION_MARKER: Final[str] = "-- schema-version:"

#: `NNNN__slug.sql`. The number orders them; the slug is for humans reading a
#: directory listing six months later.
_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<number>\d{4})__(?P<slug>[a-z0-9_]+)\.sql$")
_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_VERSION_RE: Final[re.Pattern[str]] = re.compile(rf"^{SCHEMA_VERSION_MARKER}\s*(\d+)\s*$", re.M)

_TEMPLATE: Final[str] = """-- {slug}
--
-- Forward-only. One transaction per file. Additive-only from the 0.3.0 tag:
-- no DROP, no RENAME, no type narrowing. Removal is `retired_in_version` in
-- schema/canonical.yaml, which keeps the physical object and writes it NULL.
--
{version_marker} {next_version}
{marker} TODO -- state exactly what to do if this migration is deployed and the
{marker} change has to be undone. A migration without this note cannot be applied.

"""


class MigrationTarget(Protocol):
    """What a migration needs from a store, and nothing more.

    Deliberately minimal so that both realizations and a test double satisfy it
    without either side importing the other -- `adopt_schema` may not import
    `sqlite3` at all under the `no-raw-sqlite` contract.
    """

    def current_version(self) -> int:
        """The schema version this store is already at; `0` for an empty store."""
        ...

    def apply_migration(
        self, sql: str, schema_version: int, export_version: int, written_by: str
    ) -> None:
        """Apply the script and append the `schema_meta` row **atomically**.

        The implementation owns the transaction because the dialect owns the
        rules. On failure it must leave the store exactly as it found it and
        raise: a partly-applied migration is the one state no version of the
        binary knows how to read.
        """
        ...


def migrations_dir(root: Path, dialect: str) -> Path:
    return root / "schema" / "migrations" / dialect


def declared_version(sql: str) -> int | None:
    match = _VERSION_RE.search(sql)
    return int(match.group(1)) if match else None


def _ordered_files(directory: Path) -> list[tuple[int, Path]]:
    """Every migration in `directory`, in application order.

    **A directory that does not exist is a failure, not an empty result**
    (CR-53). `Path.glob` on a missing directory yields nothing, so this used to
    report zero migrations for an artefact whose migrations were never packaged
    -- `open_store` then created an empty database, and the first query failed
    with `no such table: firm`, four layers below the cause and blaming the
    store.

    An *existing but empty* directory is left to `pending`, not judged here:
    `new_migration` calls this on a directory it has just created, where empty
    is the correct answer and the whole point.
    """
    if not directory.is_dir():
        raise AdoptError(
            ErrorCode.SCHEMA_ASSETS_MISSING,
            message=f"no migrations directory at {directory}",
            hint="This artefact does not carry the schema assets it needs, so no store "
            "can be created. Reinstall from a released artefact or run from a source "
            "checkout; `ADOPT_SCHEMA_ASSETS_ROOT` overrides where they are looked for.",
        )
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise AdoptError(
                ErrorCode.SCHEMA_MIGRATION_FAILED,
                message=f"{path.name} is not a migration filename",
                hint="Migrations are `NNNN__slug.sql`. Create them with "
                "`adopt-schema migrate new <slug>` so the numbering cannot collide.",
            )
        found.append((int(match.group("number")), path))
    return sorted(found)


def new_migration(root: Path, dialect: str, slug: str, next_version: int | None = None) -> Path:
    """Scaffold the next migration file with an unfilled back-out note."""
    if not _SLUG_RE.match(slug):
        raise AdoptError(
            ErrorCode.SCHEMA_MIGRATION_FAILED,
            message=f"migration slug {slug!r} must be lowercase words joined by underscores",
            hint="For example: `add_probe_retry_budget`.",
        )
    directory = migrations_dir(root, dialect)
    directory.mkdir(parents=True, exist_ok=True)
    existing = _ordered_files(directory)
    number = (existing[-1][0] + 1) if existing else 1
    path = directory / f"{number:04d}__{slug}.sql"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            _TEMPLATE.format(
                slug=slug,
                marker=BACK_OUT_MARKER,
                version_marker=SCHEMA_VERSION_MARKER,
                next_version=SCHEMA_VERSION + 1 if next_version is None else next_version,
            )
        )
    return path


def pending(root: Path, dialect: str, from_version: int) -> list[Path]:
    """Migration files that produce a schema version above `from_version`.

    An empty store reports `0`, so everything is pending; a store already at the
    version a file produces has had it applied.
    """
    directory = migrations_dir(root, dialect)
    ordered = _ordered_files(directory)
    if not ordered:
        # The read path, so empty is never legitimate: every dialect ships at
        # least the initial migration. Reporting "nothing pending" here is the
        # same silent success a missing directory used to produce (CR-53), one
        # packaging accident further along.
        raise AdoptError(
            ErrorCode.SCHEMA_ASSETS_MISSING,
            message=f"{directory} holds no migrations",
            hint="The schema assets are present but incomplete -- every dialect ships "
            "at least the initial migration. Reinstall from a released artefact.",
        )
    found: list[Path] = []
    for _, path in ordered:
        produces = declared_version(path.read_text(encoding="utf-8"))
        if produces is None:
            raise AdoptError(
                ErrorCode.SCHEMA_MIGRATION_FAILED,
                message=f"{path.name} carries no `{SCHEMA_VERSION_MARKER}` line",
                hint="State the schema version this migration produces. Inferring it from "
                "the file number breaks the first time two migrations produce one version.",
            )
        if produces > from_version:
            found.append(path)
    return found


def apply(target: MigrationTarget, root: Path, dialect: str, written_by: str) -> list[Path]:
    """Apply every pending migration, one atomic unit per file."""
    applied: list[Path] = []
    for path in pending(root, dialect, target.current_version()):
        sql = path.read_text(encoding="utf-8")
        if BACK_OUT_MARKER not in sql:
            raise AdoptError(
                ErrorCode.SCHEMA_MIGRATION_FAILED,
                message=f"{path.name} carries no `{BACK_OUT_MARKER}` note",
                hint="Write what to do if this migration is deployed and has to be undone. "
                "The note is mandatory because the moment it is needed is the moment "
                "nobody has time to work it out.",
            )
        try:
            target.apply_migration(sql, SCHEMA_VERSION, EXPORT_VERSION, written_by)
        except Exception as error:
            raise AdoptError(
                ErrorCode.SCHEMA_MIGRATION_FAILED,
                message=f"{path.name} failed and was rolled back: {error}",
                hint="The store is unchanged by this file. Fix the migration and re-run; "
                "there is no partial state to clean up.",
            ) from error
        applied.append(path)
    return applied
