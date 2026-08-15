"""`platform.sf_metadata` -- objects and fields from a Salesforce retrieve.

`01` F8.3: *"components, fields, transports (`metadata_component`) from an export
bundle. **No live platform connection in this build.**"* The subject is the
directory `sfdx force:source:retrieve` produced, handed in with
`--export-bundle`, and `01` §10 rules a live connection out of v1 outright --
*"security-review cost exceeds v1 value; export bundles suffice"*.

**This is the archetype the design says out loud we are worse at.** Design
Appendix B: *"on packaged ERP platforms the metadata is retrievable but
meaningless alone -- a field called `ZFIELD_003` tells you nothing -- so day-one
competence is genuinely worse until a human does a labelling pass."* This
extractor's job is to make that visible rather than to paper over it: a field the
bundle labels gets its label, a field it does not gets **no label at all** and
lands in the unlabelled bucket (`01` F12.6, `adopt_map.unlabeled`). It never
derives a label from an API name -- `01` §8 puts labelling in the *Human,
required, auto-promotion **never*** row.

**No Salesforce SDK, no `sfdx`, no import of anything in the bundle.** XML is
read through `adopt_map.xmlsafe`, which declines a document carrying a DTD or an
entity declaration (`02` §7 obligation 1, `01` N8).
"""

from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.fileindex import FileEntry
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from adopt_map.xmlsafe import Element, child_text, iter_elements, local_name, parse_xml

from adopt_extractors_platform._component import component_fact

__all__ = ["MANIFEST", "NAMESPACE", "SfMetadataExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="platform.sf_metadata",
    version="1.0.0",
    pack="platform",
    archetypes=["platform"],
    kinds=["metadata_component"],
    # **`reflection`, and the reason is the artefact rather than the number.**
    # `01` F9.1's three non-ladder methods say *how we know*: `grammar` is "we
    # parsed the language", `declared` is "a person wrote it down", `reflection`
    # is "the system emitted a description of itself". A retrieve is the second
    # of those two -- Salesforce's metadata API generating a document from the
    # org's live configuration -- and S1.4 already placed a *hand-maintained*
    # OpenAPI file at `reflection` (`05` S1.4). A retrieve is strictly more
    # authoritative than that file, so calling it `declared` would rank a
    # generated description below a written one. Not `grammar`: we parse XML, not
    # a programming language, and claiming a parse nobody did is the failure S1.5
    # named in the other direction.
    method="reflection",
)

#: `02` §3.1's `metadata_component` namespace for this platform.
NAMESPACE: Final[str] = "salesforce"

#: Root elements this reader claims. A document declares what it is; a filename
#: is a habit (`adopt_map.documents`' argument, applied within one pack).
_CLAIMED_ROOTS: Final[frozenset[str]] = frozenset({"CustomObject", "CustomField"})

#: Cheap pre-filter so a bundle's non-Salesforce XML is never parsed at all.
#: Every Salesforce metadata document carries the metadata namespace.
_MARKER: Final[str] = "soap.sforce.com/2006/04/metadata"

#: The vendor's own type words, used verbatim (see `_component`).
_OBJECT_TYPE: Final[str] = "CustomObject"
_FIELD_TYPE: Final[str] = "CustomField"


class SfMetadataExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        # True unconditionally: the discrimination is per document, below. A
        # directory heuristic here would have to walk the tree a second time,
        # which `03` §5.8's one-walk guarantee exists to prevent, and would
        # answer about the tree rather than about each file in it.
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files(language="xml"):
            ctx.budget.check()
            text = ctx.text(entry)
            if _MARKER not in text:
                continue
            root = parse_xml(text)
            if root is None or local_name(root.tag) not in _CLAIMED_ROOTS:
                # A declining `parse_xml` is a gap, not a failure: the run
                # continues over the rest of the bundle (`01` F9.2).
                continue
            yield from _object_facts(root, entry)


def _object_facts(root: Element, entry: FileEntry) -> Iterator[SurfaceFact]:
    object_name = _object_name(entry.path)

    yield component_fact(
        namespace=NAMESPACE,
        local_key=f"{_OBJECT_TYPE}.{object_name}",
        component_type=_OBJECT_TYPE,
        api_name=object_name,
        title=f"Salesforce object {object_name}",
        path=entry.path,
        blob_sha=entry.blob_sha,
        label=child_text(root, "label"),
        help_text=child_text(root, "description"),
    )

    for field in iter_elements(root, "fields"):
        api_name = child_text(field, "fullName")
        if api_name is None:
            # A field with no `fullName` has no platform API name, so it has no
            # stable key. Skipped rather than keyed by position -- a reorder
            # would otherwise read as a rename on the next run.
            continue
        targets = [target for target in (child_text(field, "referenceTo"),) if target is not None]
        yield component_fact(
            namespace=NAMESPACE,
            local_key=f"{_FIELD_TYPE}.{object_name}.{api_name}",
            component_type=_FIELD_TYPE,
            api_name=api_name,
            title=f"Salesforce field {object_name}.{api_name}",
            path=entry.path,
            blob_sha=entry.blob_sha,
            data_type=child_text(field, "type"),
            # `None` when the bundle does not label it. That absence is the
            # whole point of the unlabelled bucket, and inventing a label from
            # `api_name` here would empty the bucket by lying into it.
            label=child_text(field, "label"),
            help_text=child_text(field, "inlineHelpText"),
            relationship_targets=targets,
        )


def _object_name(path: str) -> str:
    """`objects/Account/Account.object-meta.xml` -> `Account`.

    The API name is the file's own stem with the metadata suffix removed, which
    is how a retrieve names every object file. Derived from the path rather than
    from a `<fullName>` element because `CustomObject` documents do not carry
    one -- the file *is* the name, which is a Salesforce convention rather than
    ours.
    """
    stem = PurePosixPath(path).name
    for suffix in (".object-meta.xml", ".object"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return PurePosixPath(stem).stem
