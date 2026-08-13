"""Plant a violation so a gate can be watched failing.

A gate nobody has seen fail is a gate nobody should trust. Every gate in this
repository was proven by a planted violation before it was wired in, and this
script is how the schema gates are proven -- in CI, on every run, not once during
development and never again.

Each kind writes a backup beside the file it edits, so `--revert` restores the
tree exactly without depending on the state of the index or the working tree.

    python scripts/plant_violation.py --kind drop-column
    uv run adopt-schema lint --base HEAD~1     # must fail SCHEMA_NON_ADDITIVE
    python scripts/plant_violation.py --revert
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
MANIFEST: Final[Path] = REPO_ROOT / "schema" / "canonical.yaml"
BACKUP_SUFFIX: Final[str] = ".planted-backup"

#: The column `drop-column` removes. A nullable leaf column nothing references,
#: whose name appears **exactly once** in the manifest -- so the planted
#: violation is unambiguously "this one column was removed" rather than a
#: coincidental match across several tables, which would make the gate proof
#: report a different table from the one this script claims to have edited.
DROPPED_TABLE: Final[str] = "engagement"
DROPPED_COLUMN: Final[str] = "client_label"

#: Where `revision-update` plants its statement: the module that legitimately
#: writes every revision family. Planting it here rather than in a scratch file
#: is the point -- the gate has to catch the mutation in the one place a
#: well-meaning contributor would actually add it, reaching for a one-line "fix"
#: to a revision that came out wrong.
REVISION_WRITER: Final[Path] = (
    REPO_ROOT / "packages" / "adopt-store" / "src" / "adopt_store" / "revisions.py"
)

#: The statement itself lives in a fixture, not in this file. `no-revision-update`
#: scans `scripts/`, so a script carrying the literal would fail the gate on a
#: clean tree -- which reads like a broken build rather than a working gate.
#: `tests/fixtures/planted` is the directory the contract already allows for
#: exactly this, so this is the pack's own mechanism rather than a new exemption.
PLANTED_SQL_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures" / "planted"

#: Where `covered-cache-write` plants its statement: the SQLite realization that
#: already, legitimately, updates the `identity` parent row for `last_seen`. That
#: adjacency is the whole point -- it is the file where the mistake is one line
#: away from correct code, and where a reviewer scanning a diff full of `UPDATE
#: identity SET ...` is least likely to notice one more.
IDENTITY_RECORDS: Final[Path] = (
    REPO_ROOT / "packages" / "adopt-store" / "src" / "adopt_store" / "sqlite" / "records.py"
)


def _backup(path: Path) -> None:
    """Byte-for-byte, so `--revert` restores the file and not an approximation.

    Text-mode round-tripping would silently rewrite line endings, which leaves
    the tree dirty after a gate proof and teaches people to ignore that dirtiness.
    """
    Path(str(path) + BACKUP_SUFFIX).write_bytes(path.read_bytes())


def plant_drop_column() -> str:
    """Remove a shipped column from the manifest -- PRD F2.2's `column-removed`."""
    original = MANIFEST.read_bytes()
    needle = f"{{ name: {DROPPED_COLUMN},".encode()
    kept = [line for line in original.splitlines(keepends=True) if needle not in line]
    if len(kept) == len(original.splitlines(keepends=True)):
        raise SystemExit(
            f"{DROPPED_TABLE}.{DROPPED_COLUMN} is not in the manifest in the expected form, "
            "so nothing was planted. Update this script rather than leaving the gate unproven."
        )
    _backup(MANIFEST)
    MANIFEST.write_bytes(b"".join(kept))
    return f"removed {DROPPED_TABLE}.{DROPPED_COLUMN} from {MANIFEST.name}"


def _planted_statement(name: str) -> str:
    """The one non-comment line of a planted-SQL fixture."""
    path = PLANTED_SQL_DIR / name
    if not path.exists():  # pragma: no cover -- layout change
        raise SystemExit(
            f"{path.relative_to(REPO_ROOT)} is missing, so nothing was planted. The gate "
            "proof depends on it."
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return stripped.rstrip(";")
    raise SystemExit(f"{path.name} contains no statement to plant.")  # pragma: no cover


def plant_revision_update() -> str:
    """Add an `UPDATE` against a `*_revision` table -- PRD F6.2's grep gate.

    The four revision families are append-only, and the chain is the audit
    record: the moment a revision row is updated in place, "what did it say
    then" becomes permanently unanswerable and no later repair recovers the
    answer. `no-revision-update` is the only instrument that catches it before
    merge, so it is the one that most needs to be seen failing.
    """
    if not REVISION_WRITER.exists():  # pragma: no cover -- layout change
        raise SystemExit(
            f"{REVISION_WRITER.relative_to(REPO_ROOT)} does not exist, so nothing was "
            "planted. Update this script rather than leaving the gate unproven."
        )
    statement = _planted_statement("revision_update.sql")
    _backup(REVISION_WRITER)
    with REVISION_WRITER.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f'\n\n_PLANTED_VIOLATION = "{statement}"\n')
    return f"added an UPDATE against knowledge_revision to {REVISION_WRITER.name}"


#: Where `provider-sdk` plants its import: the deterministic detection module.
#:
#: **This is the one place the violation would actually be written.** `04` §4 makes
#: detection the first and only caller that may reach a model, and it may do so
#: only after the deterministic path has declined and only through the seam. A
#: contributor shortening that path writes `import anthropic` here, four lines
#: from the docstring saying they must not -- not in a scratch file, and not in
#: `adopt_agent.adapters`, where it is allowed.
DETECT_MODULE: Final[Path] = (
    REPO_ROOT / "packages" / "adopt-detect" / "src" / "adopt_detect" / "detect.py"
)

#: Where `uri-construction` plants its literal: Build 1's minting module.
#:
#: **This is the one place the violation would actually be written**, for the
#: same reason `DETECT_MODULE` is. `minting.py` is the module whose whole job is
#: turning a fact into a URI, so a contributor taking the short way -- an
#: f-string instead of `build_uri()` -- writes it here, not in a scratch file.
#:
#: The planted literal is **built from `adopt_const.URI_SCHEME` rather than
#: spelled out** (B1-CR-26). Spelling it out would reproduce, inside the proof
#: itself, exactly the defect the rule exists to prevent: the two audits this
#: replaces hard-coded `adopt://`, which CR-06 had already made unreachable, so
#: they scanned for a string that could never appear and reported clean forever.
#: A proof that hard-codes the value goes blind on the same day the rule does.
MINTING_MODULE: Final[Path] = (
    REPO_ROOT / "packages" / "adopt-map" / "src" / "adopt_map" / "minting.py"
)

#: The provider module named in the planted import. One of the six
#: `no-provider-sdk` forbids, and deliberately the one PRD F13.2 names first --
#: so the proof is against a module the pack actually expects someone to reach
#: for, rather than an obscure entry that happens to be on the list.
PLANTED_PROVIDER: Final[str] = "anthropic"


def plant_provider_sdk() -> str:
    """Import a provider SDK outside `adopt_agent.adapters` -- PRD F13.1's gate.

    **Under CR-46 this contract constrains nothing on a clean tree**, because the
    owner decided the hosted adapters speak HTTP over the standard library and no
    vendor SDK enters the wheel at all. That makes the gate *preventive* -- it
    fires the moment anyone adds one -- and it makes this proof more important,
    not less: a contract with no members in its forbidden set could be silently
    misconfigured and still report `KEPT` forever. `no-dbos` was in exactly this
    state inside `adopt-core` before S8, which is the reason that gate was worth
    having then.

    The import is planted **inside a function** so it is a real import edge that
    grimp records without the module having to be installed -- the tree has no
    `anthropic` distribution and, by CR-46, never will.
    """
    if not DETECT_MODULE.exists():  # pragma: no cover -- layout change
        raise SystemExit(
            f"{DETECT_MODULE.relative_to(REPO_ROOT)} does not exist, so nothing was "
            "planted. Update this script rather than leaving the gate unproven."
        )
    _backup(DETECT_MODULE)
    with DETECT_MODULE.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n\ndef _planted_violation() -> None:\n"
            f"    import {PLANTED_PROVIDER}\n\n"
            f"    del {PLANTED_PROVIDER}\n"
        )
    return f"added `import {PLANTED_PROVIDER}` to {DETECT_MODULE.name}"


def plant_covered_cache_write() -> str:
    """Add a cache write outside `adopt_coverage` -- PRD F7.4's gate.

    `identity.covered_cache` is a cache and `recompute_coverage` is the
    authority. A second writer is how the withdrawn `0.1.x` line's invisible
    coverage decay comes back: the cache drifts, the recompute disagrees, and
    because some other path also writes it there is no way to say which value was
    ever right. `no-covered-cache-write` is the only instrument that catches it
    before merge, so it is the one that most needs to be seen failing.
    """
    if not IDENTITY_RECORDS.exists():  # pragma: no cover -- layout change
        raise SystemExit(
            f"{IDENTITY_RECORDS.relative_to(REPO_ROOT)} does not exist, so nothing was "
            "planted. Update this script rather than leaving the gate unproven."
        )
    statement = _planted_statement("covered_cache_write.sql")
    _backup(IDENTITY_RECORDS)
    with IDENTITY_RECORDS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f'\n\n_PLANTED_VIOLATION = "{statement}"\n')
    return f"added a coverage-cache write to {IDENTITY_RECORDS.name}"


def plant_uri_construction() -> str:
    """Mint a URI by f-string -- PRD F2's acceptance signal, `02` §1.2's rule.

    `identity.uri` is `UNIQUE` and is written into every exported bundle a client
    keeps. A hand-assembled URI differs from `build_uri()`'s output by one
    normalization or one escape, and the store then holds the same referent twice
    under two names -- with no way afterwards to say which row anything meant.

    The literal is composed from `URI_SCHEME` at plant time, so this proof
    follows the constant the day `onboard-v2` is ratified. See `MINTING_MODULE`.
    """
    if not MINTING_MODULE.exists():  # pragma: no cover -- layout change
        raise SystemExit(
            f"{MINTING_MODULE.relative_to(REPO_ROOT)} does not exist, so nothing was "
            "planted. Update this script rather than leaving the gate unproven."
        )
    from adopt_const import URI_SCHEME

    forged = f"{URI_SCHEME}://northwind/acme-erp/orders-api/prod/symbol/python/x"
    _backup(MINTING_MODULE)
    with MINTING_MODULE.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f'\n\n_PLANTED_VIOLATION = "{forged}"\n')
    return f"added a hand-assembled URI literal to {MINTING_MODULE.name}"


KINDS: Final[dict[str, Callable[[], str]]] = {
    "covered-cache-write": plant_covered_cache_write,
    "drop-column": plant_drop_column,
    "provider-sdk": plant_provider_sdk,
    "revision-update": plant_revision_update,
    "uri-construction": plant_uri_construction,
}


def revert() -> list[str]:
    restored: list[str] = []
    for backup in sorted(REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}")):
        original = Path(str(backup)[: -len(BACKUP_SUFFIX)])
        original.write_bytes(backup.read_bytes())
        backup.unlink()
        restored.append(str(original.relative_to(REPO_ROOT)))
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--kind", choices=sorted(KINDS), help="The violation to plant.")
    parser.add_argument("--revert", action="store_true", help="Restore every planted file.")
    args = parser.parse_args(argv)

    if args.revert:
        restored = revert()
        print("reverted: " + (", ".join(restored) if restored else "nothing was planted"))
        return 0

    if not args.kind:
        parser.error("give --kind or --revert")

    print(f"planted {args.kind}: {KINDS[args.kind]()}")
    print("The gate must now FAIL. Run `--revert` afterwards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
