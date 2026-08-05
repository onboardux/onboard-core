"""What counts as a content field, derived rather than listed.

Contracts §8 rule 2: content fields are *the deny-list* plus any field whose
manifest column type is `md` or `text` on an exportable knowledge table.

**The cross-reference in §8 rule 2 was wrong and is repaired with this code**
(CR-39). It pointed at §10, which is *Seam signatures* and contains no
deny-list; the list is `03` §4.2's, the eight field names the structured logger
drops. A validator built from a broken pointer is a validator built from a
guess, and this one is the egress gate.

**Why the manifest half is derived at runtime.** A hand-kept list of content
columns is correct until the next `md` column is added by someone with no reason
to look here -- and the failure mode is silent: the new column simply stops being
recognised as content and starts being allowed out under `metadata_only`. Reading
the manifest makes the deny-list widen by itself, which is the direction a
fail-closed control has to move in on its own.

**Matching is by field name at any depth**, because an envelope's `payload` is
free-form JSON (contracts §8) and a content field nested two levels down is
still a content field. Name matching is deliberately conservative: it will
occasionally reject a payload whose key happens to be called `source` and means
something innocent. That is the correct direction for this gate to be wrong in.
"""

from collections.abc import Iterator, Mapping, Sequence
from functools import cache
from typing import Any, Final

from adopt_obs import DENIED_FIELDS
from adopt_schema.manifest import Manifest, load_manifest

__all__ = ["content_fields", "find_content_fields"]

#: The manifest column types that hold prose. `03` §2's type table maps both to
#: `TEXT`/`text`; the distinction that matters here is that these are the two a
#: human wrote, as against `id`, `slug`, `uri`, `enum`, `ts` and the numerics.
_CONTENT_COLUMN_TYPES: Final[frozenset[str]] = frozenset({"md", "text"})


def _manifest_content_columns(manifest: Manifest) -> frozenset[str]:
    """Every `md`/`text` column name on an exportable table."""
    return frozenset(
        column.name
        for _, table in manifest.exportable_tables()
        for column in table.columns
        if column.type in _CONTENT_COLUMN_TYPES
    )


@cache
def _cached_content_fields() -> frozenset[str]:
    return frozenset(DENIED_FIELDS) | _manifest_content_columns(load_manifest())


def content_fields(manifest: Manifest | None = None) -> frozenset[str]:
    """The full content-field set: the logger deny-list plus the manifest's prose columns.

    Args:
        manifest: Injected manifest, for tests asserting the derivation itself.
            Defaults to the canonical one, loaded once.
    """
    if manifest is None:
        return _cached_content_fields()
    return frozenset(DENIED_FIELDS) | _manifest_content_columns(manifest)


def _walk(value: Any, path: str) -> Iterator[tuple[str, str]]:
    """Yield `(field_name, json_path)` for every key at every depth."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            name = str(key)
            here = f"{path}.{name}" if path else name
            yield name, here
            yield from _walk(nested, here)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, nested in enumerate(value):
            yield from _walk(nested, f"{path}[{index}]")


def find_content_fields(payload: Any, *, manifest: Manifest | None = None) -> tuple[str, ...]:
    """Every JSON path in `payload` whose key is a content field, sorted.

    Sorted so that a rejection message is the same on every run -- a violation
    list whose order came from dict iteration would make two identical refusals
    read as two different ones in a log.
    """
    denied = content_fields(manifest)
    return tuple(sorted({path for name, path in _walk(payload, "") if name in denied}))
