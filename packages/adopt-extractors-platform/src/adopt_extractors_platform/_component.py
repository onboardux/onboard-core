"""One shape for a `metadata_component`, shared by the three platform readers.

Three vendors, one kind. `02` §3.1 gives `metadata_component` a `namespace` per
platform and a `local_key` that is *"the platform API name"*, and `03` §5.10's
whole argument for a pack is that an SAP transport object and a Salesforce field
land in the same shape. A helper here is what keeps that true when the third
reader is written six months after the first.

**Labelled and opaque are different facts, and this module is where the
distinction is enforced** (B1-CR-77):

* **Unlabelled** -- the bundle carries the component's definition and no human
  label. `ZFIELD_003__c` with a type, a length and no `<label>` is *fully
  readable*; what is missing is meaning for a person. It gets a real digest, and
  it goes in the unlabelled bucket (`01` F12.6) so a human can label it.
* **Opaque** -- the bundle *references* a component whose definition it does not
  contain. Nothing about it is recoverable, so `01` F8.7 applies: null semantic
  digest, no invented content.

Conflating them is not a naming quibble. `opaque=True` nulls the semantic digest
(`02` §4.1's `s-`), so if an unlabelled-but-defined field were marked opaque, a
later change to its **data type** would produce no revision and a run reporting
that nothing had happened -- the silent failure B1-CR-44 exists to refuse. This
is `01` F8.6 vs F8.7 in a second pair, after S1.5 drew it for model pins.
"""

from typing import Final

from adopt_map.schemas import SourceRef, SurfaceFact

__all__ = ["PLATFORM_NAMESPACES", "component_fact"]

#: `02` §3.1's `metadata_component` namespace list, restated nowhere else in this
#: pack: each reader names its member and the table is the authority.
PLATFORM_NAMESPACES: Final[tuple[str, ...]] = (
    "salesforce",
    "sap",
    "servicenow",
    "powerapps",
    "dbt",
)


def component_fact(
    *,
    namespace: str,
    local_key: str,
    component_type: str,
    api_name: str,
    title: str,
    path: str,
    blob_sha: str,
    data_type: str | None = None,
    label: str | None = None,
    help_text: str | None = None,
    relationship_targets: list[str] | None = None,
    layout_position: str | None = None,
    opaque: bool = False,
) -> SurfaceFact:
    """One platform component as a `metadata_component` fact.

    Args:
        namespace: A member of `PLATFORM_NAMESPACES` (`02` §3.1).
        local_key: The platform API name, in the form this pack states in §3.1.
        component_type: The vendor's own type word -- `CustomField`, `TABL`,
            `Script Include`. Recorded verbatim rather than mapped onto a
            vocabulary of ours, because a mapping is a claim about equivalence
            nobody asked us to make.
        api_name: The component's own name within its type.
        title: One line for a human reading the inventory.
        path: Bundle-relative path of the file that declared it.
        blob_sha: The declaring file's blob sha, for provenance.
        data_type: The field's type where the vendor declares one.
        label: The human label **as the bundle states it**. `None` means the
            bundle does not say -- never a name we derived from the API name,
            which would be a label nobody wrote (`01` §8: auto-promotion never).
        help_text: Vendor help text, where present.
        relationship_targets: API names this component points at.
        layout_position: Where the vendor places it, where stated.
        opaque: `True` only when the bundle **references** this component
            without defining it. See the module docstring.

    Returns:
        The fact. `attributes` carries only keys `MetadataComponentAttributes`
        declares, and an opaque component carries none of them beyond its own
        identity -- there is nothing else the bundle said.
    """
    if opaque:
        # No attributes at all. An opaque component's digest is null (`02` §4.1
        # `s-`), and populating fields we inferred rather than read is precisely
        # the invention `01` §1.6 forbids -- the same shape S1.5 shipped for an
        # unreadable prompt, whose `attributes == {}`.
        return SurfaceFact(
            identity_kind="metadata_component",
            namespace=namespace,
            local_key=local_key,
            title=title,
            attributes={},
            source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
            opaque=True,
        )

    attributes: dict[str, object] = {
        "component_type": component_type,
        "api_name": api_name,
    }
    if data_type is not None:
        attributes["data_type"] = data_type
    if label is not None:
        attributes["label"] = label
    if help_text is not None:
        attributes["help_text"] = help_text
    if relationship_targets:
        attributes["relationship_targets"] = relationship_targets
    if layout_position is not None:
        attributes["layout_position"] = layout_position

    return SurfaceFact(
        identity_kind="metadata_component",
        namespace=namespace,
        local_key=local_key,
        title=title,
        attributes=attributes,
        source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
    )
