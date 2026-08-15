"""`platform.snow_updateset` -- the records a ServiceNow update set moves.

An update set is ServiceNow's transport: an `<unload>` document of
`<sys_update_xml>` entries, one per customised record, each naming the table it
belongs to and the record it targets. Like the SAP request it is a change record
for a platform with no `git diff`.

**The payload is deliberately not read.** Each entry carries a `<payload>`
holding the customised record itself -- frequently a Script Include's *source
code*, a business rule's condition, a client script. `03` §5.9 invariant 4 and
`02` §9.3 both forbid client source content in any artefact this build emits, and
the surest way to keep that true is to never lift it out of the document. So this
reader takes names, tables and types, and steps over the one element that holds
the client's code.

**Unlabelled, like SAP and for the same reason.** An update-set entry names a
record; it carries no human label for it. `target_name` is the record's own name
and is recorded as the API name, not as a label -- so these components reach the
unlabelled bucket honestly instead of arriving pre-labelled with a name a person
never wrote as a description (`01` §8, auto-promotion **never**).
"""

import re
from collections.abc import Iterator
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from adopt_map.xmlsafe import Element, child_text, iter_elements, local_name, parse_xml

from adopt_extractors_platform._component import component_fact

__all__ = ["MANIFEST", "NAMESPACE", "SnowUpdateSetExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="platform.snow_updateset",
    version="1.0.0",
    pack="platform",
    archetypes=["platform"],
    kinds=["metadata_component"],
    # `reflection`: the platform emitted this document about itself (`01` F9.1).
    # See `platform.sf_metadata` for the full argument -- one reading of the
    # method vocabulary, applied to all three vendors rather than restated.
    method="reflection",
)

#: `02` §3.1's `metadata_component` namespace for this platform.
NAMESPACE: Final[str] = "servicenow"

#: The root element of an update-set export.
_CLAIMED_ROOT: Final[str] = "unload"

#: Cheap pre-filter, so no other vendor's XML is parsed by this reader.
_MARKER: Final[str] = "<sys_update_xml"

#: `<name>` is `<table>_<32-hex sys_id>`. The table is what identifies the record
#: within the platform's API, so the sys id -- which is per instance and changes
#: nothing about *what* the record is -- is stripped off the end.
_NAME_WITH_SYS_ID: Final[re.Pattern[str]] = re.compile(r"^(?P<table>.+?)_(?P<sys_id>[0-9a-f]{32})$")


class SnowUpdateSetExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files(language="xml"):
            ctx.budget.check()
            text = ctx.text(entry)
            if _MARKER not in text:
                continue
            root = parse_xml(text)
            if root is None or local_name(root.tag) != _CLAIMED_ROOT:
                continue

            seen: set[str] = set()
            for record in iter_elements(root, "sys_update_xml"):
                fact = _record_fact(record, entry.path, entry.blob_sha, seen)
                if fact is not None:
                    yield fact


def _record_fact(record: Element, path: str, blob_sha: str, seen: set[str]) -> SurfaceFact | None:
    name = child_text(record, "name")
    target = child_text(record, "target_name")
    if name is None:
        return None
    table = _table_of(name)
    api_name = target or name
    local_key = f"{table}.{api_name}"
    if local_key in seen:
        # One update set can carry two versions of one record; both describe the
        # same referent, and two facts for one URI is the situation
        # `02` §10 C1 forbids inside a single extractor.
        return None
    seen.add(local_key)
    return component_fact(
        namespace=NAMESPACE,
        local_key=local_key,
        component_type=child_text(record, "type") or table,
        api_name=api_name,
        title=f"ServiceNow {table} {api_name}",
        path=path,
        blob_sha=blob_sha,
        # No `label=`, no `help_text=`, and above all no `<payload>`: the
        # customised record's own source stays in the client's file.
    )


def _table_of(name: str) -> str:
    """`sys_script_include_9f8c…` -> `sys_script_include`."""
    match = _NAME_WITH_SYS_ID.match(name)
    return match.group("table") if match else name
