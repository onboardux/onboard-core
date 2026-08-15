"""`platform.sap_transport` -- the objects a transport request carries.

`01` F8.3 names *"components, fields, **transports**"*, and a transport request
is how change moves through an SAP landscape: there is no `git diff` here, so the
request's object list is the change record.

**What is readable, and what is not.** A transport ships as a co-file and a data
file (`K900123.DEV`, `R900123.DEV`) whose contents are a binary cluster; nothing
honest can be read from them without SAP's own tooling, and `01` §10 rules a live
connection out. What a client can export instead -- from SE01/SE10, and what this
reader takes -- is the **object list**: one line per object, `<PGMID> <TYPE>
<NAME>`, exactly as the request stores it in E071.

**Every SAP object here is unlabelled, and that is the finding rather than a
shortfall.** An object list carries names and no descriptions: `ZCUSTOMER` is
retrievable and meaningless, which is design Appendix B's *"honest limit"* in its
original example. So this reader emits no label at all, every component lands in
the unlabelled bucket for a human pass (`01` F12.6), and the request's own short
text is attached to the **request**, where it was actually written -- not
sprayed onto the objects it happens to contain, which would be a label nobody
wrote.
"""

import re
from collections.abc import Iterator
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SurfaceFact

from adopt_extractors_platform._component import component_fact

__all__ = ["MANIFEST", "NAMESPACE", "SapTransportExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="platform.sap_transport",
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
NAMESPACE: Final[str] = "sap"

#: One object-list line: program id, object type, object name. `R3TR` is a whole
#: object and `LIMU` a sub-object; both are real E071 program ids and both are
#: carried, because a transport that moves only a sub-object is a real transport.
_OBJECT_LINE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<pgmid>R3TR|LIMU)\s+(?P<type>[A-Z0-9_]{3,10})\s+(?P<name>\S+)\s*$"
)

#: The request header a client's export writes above the list. The id is the
#: transport's own name and the text is the short description a person typed.
_REQUEST_HEADER: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:Request|REQUEST)[:\s]+(?P<id>[A-Z][A-Z0-9]{2}K[0-9]{6})\s*(?:[-:]\s*(?P<text>.*?))?\s*$"
)

_REQUEST_TYPE: Final[str] = "TransportRequest"

#: Suffixes worth reading. A transport's binary co-file is deliberately absent:
#: the file index skips it as binary, and nothing here would know what to do with
#: it if it did not.
_READABLE: Final[tuple[str, ...]] = (".txt", ".objlist", ".list", ".log")


class SapTransportExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files():
            ctx.budget.check()
            if not entry.path.endswith(_READABLE):
                continue
            text = ctx.text(entry)
            lines = text.splitlines()
            objects = [match for match in (_OBJECT_LINE.match(line) for line in lines) if match]
            if not objects:
                # A text file that is not an object list. Declining on the
                # document's own shape rather than on its name is the same rule
                # `adopt_map.documents` states: a filename is a habit.
                continue

            request = _request(lines)
            if request is not None:
                identifier, short_text = request
                yield component_fact(
                    namespace=NAMESPACE,
                    local_key=f"{_REQUEST_TYPE}.{identifier}",
                    component_type=_REQUEST_TYPE,
                    api_name=identifier,
                    title=f"SAP transport request {identifier}",
                    path=entry.path,
                    blob_sha=entry.blob_sha,
                    # The short text belongs to the request, and only to it.
                    label=short_text,
                )

            seen: set[str] = set()
            for match in objects:
                pgmid = match.group("pgmid")
                object_type = match.group("type")
                name = match.group("name")
                local_key = f"{pgmid}.{object_type}.{name}"
                if local_key in seen:
                    # A list may name one object twice; two facts for one URI
                    # would be B1-CR-68's situation created inside a single
                    # extractor, where no merge could help.
                    continue
                seen.add(local_key)
                yield component_fact(
                    namespace=NAMESPACE,
                    local_key=local_key,
                    component_type=f"{pgmid} {object_type}",
                    api_name=name,
                    title=f"SAP {object_type} {name}",
                    path=entry.path,
                    blob_sha=entry.blob_sha,
                    # No label. See the module docstring: an object list has
                    # none, and deriving one from `ZCUSTOMER` would be invention.
                )


def _request(lines: list[str]) -> tuple[str, str | None] | None:
    """The request id and its short text, if the export states them."""
    for line in lines:
        match = _REQUEST_HEADER.match(line)
        if match:
            text = (match.group("text") or "").strip()
            return match.group("id"), text or None
    return None
