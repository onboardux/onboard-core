"""`read_bundle` and `apply_bundle` — the import half of contracts §11.

The order of the checks is the contract, not an implementation detail:

1. **Version negotiation first.** An `export_version` outside the supported range
   is refused **naming the range**, because "unsupported" without the range sends
   an integrator to the source and the whole point of pinning `export_version`
   (§1.6, owner decision 12) is that they should not have to go there.
2. **Every digest, before any row.** §11 says verified *before any row is
   applied*, and F9.5 says all or nothing. Verifying per table as it is applied
   would satisfy neither: the first table would already be in the store when the
   fourth file turned out to be corrupt.
3. **Then the rows**, all inside one transaction against an empty store.

**Rows are applied verbatim, not through the facades.** A facade generates ids;
import must preserve them, because a re-export whose ULIDs differ is not
byte-identical and every URI in the bundle would resolve to a different row. That
is also why the applier takes whole validated models: it restores what a bundle
recorded and computes nothing.
"""

import json as _json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from adopt_const import (
    EXPORT_NDJSON_MAX_LINE_BYTES,
    MAX_SUPPORTED_EXPORT_VERSION,
    MIN_SUPPORTED_EXPORT_VERSION,
)
from adopt_export.bundle import (
    ENCODING,
    MANIFEST_FILENAME,
    BundleManifest,
    read_text,
    sha256_of_bytes,
    table_relative_path,
)
from adopt_export.ports import ImportRecords
from adopt_model import MODEL_FOR_TABLE
from adopt_obs import AdoptError, ErrorCode, get_logger
from adopt_schema.manifest import Manifest, canonical_path, load_manifest

__all__ = ["EXPORT_COMPAT_FILENAME", "apply_bundle", "read_bundle"]

_LOGGER: Final = get_logger("adopt_export")

#: Which `schema_version` each `export_version` implies. The two are versioned
#: independently (§1.6), so the mapping is data rather than an equality anyone
#: could assume from the fact that both happen to start at 3 (CR-13).
EXPORT_COMPAT_FILENAME: Final[str] = "export_compat.json"


def _malformed(message: str, hint: str) -> AdoptError:
    return AdoptError(ErrorCode.EXPORT_BUNDLE_MALFORMED, message=message, hint=hint)


def _load_compat(path: Path | None = None) -> dict[str, dict[str, int]]:
    source = path if path is not None else canonical_path().parent / EXPORT_COMPAT_FILENAME
    parsed: Any = _json.loads(source.read_text(encoding=ENCODING))
    return {str(key): dict(value) for key, value in parsed.items()}


def read_bundle(source: Path, *, compat_path: Path | None = None) -> BundleManifest:
    """Parse and validate `manifest.json`, negotiate the version, verify nothing else.

    Raises:
        AdoptError: ``EXPORT_BUNDLE_MALFORMED`` when the manifest is missing or
            does not match §11. ``EXPORT_VERSION_UNSUPPORTED`` when the bundle's
            `export_version` is outside the supported range or its declared
            `schema_version` is not the one that version implies.
    """
    manifest_path = source / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise _malformed(
            f"no {MANIFEST_FILENAME} at {source}",
            "A bundle is a directory whose manifest names every file in it. Point "
            "import at the bundle directory, not at its `tables/` subdirectory.",
        )

    try:
        bundle = BundleManifest.model_validate_json(read_text(manifest_path))
    except ValidationError as error:
        raise _malformed(
            f"{manifest_path} does not match the contracts §11 manifest shape: {error}",
            "The manifest is closed: every key is declared in §11, and an unknown one "
            "is a bundle from a different format rather than a newer version of this one.",
        ) from error

    if not MIN_SUPPORTED_EXPORT_VERSION <= bundle.export_version <= MAX_SUPPORTED_EXPORT_VERSION:
        raise AdoptError(
            ErrorCode.EXPORT_VERSION_UNSUPPORTED,
            message=f"bundle export_version {bundle.export_version} is outside the supported "
            f"range {MIN_SUPPORTED_EXPORT_VERSION}-{MAX_SUPPORTED_EXPORT_VERSION}",
            hint="Integrators pin `export_version` (contracts §1.6). Read the bundle with "
            "a binary whose supported range includes it; nothing has been applied.",
        )

    implied = _load_compat(compat_path).get(str(bundle.export_version))
    if implied is not None and implied.get("schema_version") != bundle.schema_version:
        raise AdoptError(
            ErrorCode.EXPORT_VERSION_UNSUPPORTED,
            message=f"export_version {bundle.export_version} implies schema_version "
            f"{implied.get('schema_version')}, and the bundle declares {bundle.schema_version}",
            hint="`schema/export_compat.json` records which schema version each export "
            "version carries. A bundle disagreeing with it was assembled by hand or by "
            "a build that is not this one.",
        )
    return bundle


def _verify_digests(source: Path, bundle: BundleManifest, manifest: Manifest) -> dict[str, bytes]:
    """Every table file, read and digest-checked. Returns the verified bytes.

    Read once and kept, rather than read now and re-read while applying: a file
    that changed between the two reads would pass the digest check and apply
    something else, which is precisely the substitution the digest exists to
    prevent.
    """
    expected = {name for name, _ in manifest.exportable_tables()}
    declared = {entry.name for entry in bundle.tables}

    if declared != expected:
        missing = sorted(expected - declared)
        unexpected = sorted(declared - expected)
        raise _malformed(
            "the manifest's table set does not match schema version "
            f"{manifest.schema_version}: missing {missing}, unexpected {unexpected}",
            "Every exportable table produces a file, even when empty (§11), so a missing "
            "entry is a truncated bundle rather than an empty table. An unexpected entry "
            "is a table this schema version does not declare.",
        )

    verified: dict[str, bytes] = {}
    for entry in bundle.tables:
        path = source / table_relative_path(entry.name)
        if not path.is_file():
            raise _malformed(
                f"the manifest names {entry.name} but {path} does not exist",
                "Every table the manifest lists is present as a file, empty or not.",
            )
        payload = path.read_bytes()
        actual = sha256_of_bytes(payload)
        if actual != entry.sha256:
            raise AdoptError(
                ErrorCode.EXPORT_DIGEST_MISMATCH,
                message=f"{table_relative_path(entry.name)} digests to {actual}, and the "
                f"manifest records {entry.sha256}",
                hint="Nothing has been applied. The bundle is corrupt or was edited after "
                "it was written; re-export rather than repairing the file.",
            )
        verified[entry.name] = payload
    return verified


def _parse_rows(table: str, payload: bytes) -> Sequence[BaseModel]:
    model_type = MODEL_FOR_TABLE[table]
    models: list[BaseModel] = []
    for number, raw in enumerate(payload.decode(ENCODING).split("\n"), 1):
        if not raw:
            continue
        if len(raw.encode(ENCODING)) > EXPORT_NDJSON_MAX_LINE_BYTES:
            raise _malformed(
                f"{table} line {number} is over the {EXPORT_NDJSON_MAX_LINE_BYTES}-byte limit",
                "A line this long is not a row this format can carry. The bundle was not "
                "written by this version.",
            )
        try:
            models.append(model_type.model_validate_json(raw))
        except ValidationError as error:
            raise _malformed(
                f"{table} line {number} does not validate against its generated model: {error}",
                "The generated models are the only validators (contracts §1.4) and they are "
                "closed, so an unknown column is a row from a different schema rather than a "
                "richer version of this one.",
            ) from error
    return models


def apply_bundle(
    records: ImportRecords,
    source: Path,
    *,
    bundle: BundleManifest | None = None,
    manifest: Manifest | None = None,
    compat_path: Path | None = None,
) -> BundleManifest:
    """Verify a bundle whole, then apply every row in one transaction.

    Args:
        records: The write port, over a store already at the current schema version.
        source: The bundle directory.
        bundle: A manifest already read by `read_bundle`; read here if absent.
        manifest: The canonical manifest. Loaded if not supplied.
        compat_path: Override for `schema/export_compat.json`.

    Raises:
        AdoptError: ``EXPORT_VERSION_UNSUPPORTED``, ``EXPORT_DIGEST_MISMATCH``,
            ``EXPORT_BUNDLE_MALFORMED`` or ``EXPORT_TARGET_NOT_EMPTY``. In every
            case no row has been applied.
    """
    loaded = manifest if manifest is not None else load_manifest()
    read = bundle if bundle is not None else read_bundle(source, compat_path=compat_path)

    verified = _verify_digests(source, read, loaded)

    for table_name, _ in loaded.exportable_tables():
        held = records.row_count(table_name)
        if held:
            raise AdoptError(
                ErrorCode.EXPORT_TARGET_NOT_EMPTY,
                message=f"the target store already holds {held} row(s) in {table_name}",
                hint="Import restores a bundle into an empty store and is never a merge: "
                "the bundle carries the ids it was written with, so applying it over "
                "existing rows would collide or silently interleave two histories.",
            )

    # Parsed before the transaction opens, so a malformed row fails without ever
    # having started a write -- and the store is untouched rather than rolled back.
    parsed = {name: _parse_rows(name, payload) for name, payload in verified.items()}

    with records.transaction():
        # Foreign-key topological order: `exportable_tables()` is already sorted
        # by it, so a child never lands before its parent.
        for table_name, _ in loaded.exportable_tables():
            records.insert_rows(table_name, parsed[table_name])

    _LOGGER.info(
        "export.bundle_applied",
        tables=len(read.tables),
        rows=sum(entry.rows for entry in read.tables),
        export_version=read.export_version,
        schema_version=read.schema_version,
    )
    return read
