"""`adopt export DIR` and `adopt import DIR --into PATH` — contracts §14.

Both emit the one envelope §14 declares for the pair --
`{bundle_path, export_version, tables[], bytes}` -- because they are two
directions of one operation and an integrator comparing what was written against
what was applied should not have to reconcile two shapes.

Exit codes are §13's, derived from the error's category and never chosen here: a
policy refusal (`EXPORT_SCOPE_AMBIGUOUS`, `EXPORT_VERSION_UNSUPPORTED`,
`EXPORT_TARGET_NOT_EMPTY`) exits `3`, an integrity failure
(`EXPORT_DIGEST_MISMATCH`, `EXPORT_BUNDLE_MALFORMED`) exits `1`.

**Export opens the store read-only and import opens its target for writing.**
Neither needs saying twice: the direction of the operation decides it, and a
read-only export is what makes "exporting cannot change what you exported"
structural rather than remembered.

The two commands live in one module because they share the envelope and the
`--json` flag; they are registered as separate top-level commands rather than a
`adopt interchange` group, because §14 names them `adopt export` and
`adopt import`.
"""

from pathlib import Path
from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_cli.store_option import open_configured_store, open_named_store, writer_identity
from adopt_export import BundleManifest, apply_bundle, table_relative_path, write_bundle

__all__ = ["export", "import_"]

BundleArgument = Annotated[
    Path, typer.Argument(metavar="DIR", help="The bundle directory.", show_default=False)
]
StoreOption = Annotated[
    Path | None,
    typer.Option("--store", help="Store path. Defaults to the resolved ADOPT_STORE_PATH."),
]
IntoOption = Annotated[
    Path,
    typer.Option("--into", help="The store to restore into. Created empty if absent."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def _bytes_on_disk(bundle_path: Path, manifest: BundleManifest) -> int:
    """Total size of the table files the manifest names.

    The table files and nothing else: `bytes` describes the payload an
    integrator moves, and counting the manifest and the JSON schema would make
    the number change whenever a comment in the schema did.
    """
    return sum(
        (bundle_path / table_relative_path(entry.name)).stat().st_size for entry in manifest.tables
    )


def _payload(bundle_path: Path, manifest: BundleManifest) -> dict[str, object]:
    return {
        "bundle_path": str(bundle_path),
        "export_version": manifest.export_version,
        "tables": [
            {"name": entry.name, "rows": entry.rows, "sha256": entry.sha256}
            for entry in manifest.tables
        ],
        "bytes": _bytes_on_disk(bundle_path, manifest),
    }


def export(
    directory: BundleArgument,
    store: StoreOption = None,
    json_output: JsonOption = False,
) -> None:
    """Write a portable bundle of every exportable table."""
    with open_configured_store(store, read_only=True) as handle:
        manifest = write_bundle(handle.export_records(), directory, written_by=writer_identity())
    emit(_payload(directory, manifest), as_json=json_output, title="adopt export")


def import_(
    directory: BundleArgument,
    into: IntoOption,
    json_output: JsonOption = False,
) -> None:
    """Verify a bundle whole, then restore it into an empty store."""
    with open_named_store(into, migrate=True) as handle:
        manifest = apply_bundle(handle.import_records(), directory)
    emit(_payload(directory, manifest), as_json=json_output, title="adopt import")
