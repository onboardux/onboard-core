"""The JSON Schema every exported row conforms to (contracts §11).

Third parties integrate against the export, never against our tables, so this
file is the shape that has to stay honest. It covers exportable tables only:
`schema_meta` is the store's own migration log, and a bundle already carries the
versions in its manifest.
"""

import json
from typing import Any, Final

from adopt_const import EXPORT_VERSION, SCHEMA_VERSION
from adopt_schema.emitters._shared import GENERATED_NOTICE, resolve_enum
from adopt_schema.manifest import Column, Manifest

__all__ = ["emit"]

TYPE_MAP: Final[dict[str, str]] = {
    "id": "string",
    "slug": "string",
    "uri": "string",
    "text": "string",
    "md": "string",
    "int": "integer",
    "real": "number",
    "bool": "boolean",
    "ts": "string",
}


def _column_schema(manifest: Manifest, column: Column) -> dict[str, Any]:
    enum = resolve_enum(manifest, column)
    if enum is not None:
        values: list[Any] = list(enum.values)
        if column.nullable:
            values.append(None)
        return {"enum": values}

    if column.type == "json":
        kinds: list[str] = ["object", "array"]
        if column.nullable:
            kinds.append("null")
        return {"type": kinds}

    base = TYPE_MAP[column.type]
    schema: dict[str, Any] = {"type": [base, "null"] if column.nullable else base}
    if column.type == "ts":
        schema["format"] = "date-time"
    return schema


def _table_schema(
    manifest: Manifest, name: str, table_purpose: str, columns: list[Column]
) -> dict[str, Any]:
    return {
        "title": name,
        "description": table_purpose,
        "type": "object",
        # Closed, like every other boundary in the product: an unknown field in a
        # bundle is a bundle from a schema this binary does not know.
        "additionalProperties": False,
        "properties": {c.name: _column_schema(manifest, c) for c in columns},
        "required": [c.name for c in columns if not c.nullable],
    }


def emit(manifest: Manifest) -> str:
    document: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://adopt.dev/schema/export.schema.json",
        "title": "adopt export bundle rows",
        "description": GENERATED_NOTICE,
        "x-schema-version": SCHEMA_VERSION,
        "x-export-version": EXPORT_VERSION,
        "$defs": {
            name: _table_schema(manifest, name, table.purpose, table.columns)
            for name, table in manifest.exportable_tables()
        },
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
