"""`lowcode.solution_package` -- flows, forms, apps and connectors from an export.

`01` F8.4: *"Low-code: flows, forms, connectors (`metadata_component` with a
low-code namespace)."* The subject is an unpacked Power Platform solution --
`solution.xml` plus `customizations.xml` -- passed with `--export-bundle`, for
the same reason the packaged-platform pack takes one: there is no source tree,
and `01` §10 rules a live connection out of this build.

**Two kinds, and the second one is the point.** Components are
`metadata_component`; a solution's **connection references** are `config_key`
under `namespace = "secret:connection"` (`02` §3.1 rule 2). A low-code solution
is mostly integration -- the flows are the glue between systems -- so the
credentials it reaches for are a large part of what it *is*, and enumerating them
without ever being able to hold one is exactly what `02` §5.1 rule 4's value-free
attribute model is for. There is no field a credential could occupy.

**`connectionreferencelogicalname` is a name, not a secret.** What travels in a
solution is a *reference* to a connection configured in the environment; the
credential itself lives in the platform and never appears in the export. That is
also why every one of these is flagged `outside_vcs`: the thing the reference
points at can be repointed in a vendor UI with no commit anywhere (`01` F8.6).
"""

from collections.abc import Iterator
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from adopt_map.xmlsafe import Element, child_text, iter_elements, local_name, parse_xml

__all__ = ["MANIFEST", "NAMESPACE", "SECRET_NAMESPACE", "SolutionPackageExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="lowcode.solution_package",
    version="1.0.0",
    pack="lowcode",
    archetypes=["lowcode"],
    kinds=["metadata_component", "config_key"],
    # `reflection`: the platform emitted this document about itself (`01` F9.1).
    # See `platform.sf_metadata` for the full argument -- one reading of the
    # method vocabulary, applied to all three vendors rather than restated.
    method="reflection",
)

#: `02` §3.1's `metadata_component` namespace for this platform.
NAMESPACE: Final[str] = "powerapps"

#: `02` §3.1 rule 2's `secret:<source>` namespace for a connection reference.
#: The source is the platform's connection store, which is where the credential
#: actually lives -- not an environment file and not a vault.
#:
#: `S105` is suppressed for the same reason `adopt_map.schemas.attributes`
#: suppresses it on `SECRET_NAMESPACE_PREFIX`: this is the opposite of a
#: hard-coded credential -- it is the marker that routes a fact to the one
#: attribute model with **no value field**.
SECRET_NAMESPACE: Final[str] = "secret:connection"  # noqa: S105

#: The root element both solution documents share.
_CLAIMED_ROOT: Final[str] = "ImportExportXml"

#: Cheap pre-filter so no other vendor's XML in the bundle is parsed here.
_MARKER: Final[str] = "ImportExportXml"

#: Element name -> the component type recorded for it. Held as data because the
#: alternative is a branch per component type, which is `02` §4.2's argument
#: about projections applied to a smaller thing.
#:
#: The third column is the **attribute** holding the unique name, or `""` when
#: the vendor puts it in a child element instead. Power Platform does both and
#: the difference is per component type, not a spelling we get to pick: a
#: `<Workflow Name="…">` carries it as an attribute and a `<CanvasApp><Name>`
#: as a child. Writing `"Name"` for the canvas app cost the fixture's only app,
#: silently — the extractor found the element, read an attribute that was not
#: there, and skipped it as unnamed. The labeled set is what caught it.
_COMPONENT_ELEMENTS: Final[tuple[tuple[str, str, str], ...]] = (
    # (element name, component type, attribute holding the unique name)
    ("Workflow", "Workflow", "Name"),
    ("CanvasApp", "CanvasApp", ""),
    ("Entity", "Entity", ""),
)


class SolutionPackageExtractor:
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
            yield from _solution_facts(root, entry.path, entry.blob_sha)
            yield from _component_facts(root, entry.path, entry.blob_sha)
            yield from _connection_facts(root, entry.path, entry.blob_sha)


def _solution_facts(root: Element, path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    """The solution itself, where the manifest declares one."""
    for manifest in iter_elements(root, "SolutionManifest"):
        unique_name = child_text(manifest, "UniqueName")
        if unique_name is None:
            continue
        yield _component(
            component_type="Solution",
            api_name=unique_name,
            path=path,
            blob_sha=blob_sha,
            label=_localized_name(manifest),
            data_type=child_text(manifest, "Version"),
        )


def _component_facts(root: Element, path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    seen: set[str] = set()
    for element_name, component_type, name_attribute in _COMPONENT_ELEMENTS:
        for element in iter_elements(root, element_name):
            api_name = (
                element.get(name_attribute) if name_attribute else child_text(element, "Name")
            )
            if api_name is None:
                continue
            key = f"{component_type}.{api_name}"
            if key in seen:
                continue
            seen.add(key)
            yield _component(
                component_type=component_type,
                api_name=api_name,
                path=path,
                blob_sha=blob_sha,
                # A workflow's `Category` says whether it is a flow, a business
                # rule or a classic workflow -- the platform's own word for what
                # it is, recorded rather than mapped.
                data_type=element.get("Category"),
                label=_localized_name(element),
            )
            yield from _form_facts(element, api_name, path, blob_sha, seen)


def _form_facts(
    entity: Element, entity_name: str, path: str, blob_sha: str, seen: set[str]
) -> Iterator[SurfaceFact]:
    """Forms declared inside an entity. `01` F8.4 names them explicitly."""
    for form in iter_elements(entity, "systemform"):
        form_name = child_text(form, "Name") or form.get("Name")
        if form_name is None:
            continue
        key = f"Form.{entity_name}.{form_name}"
        if key in seen:
            continue
        seen.add(key)
        yield _component(
            component_type="Form",
            api_name=f"{entity_name}.{form_name}",
            path=path,
            blob_sha=blob_sha,
            data_type=child_text(form, "type"),
            # **A name is not a label, even when it reads like one.** The first
            # version of this line passed `form_name` through whenever the
            # element had a `<Name>` -- which labelled `ZFORM_017` "ZFORM_017"
            # and emptied the unlabelled bucket by lying into it. A label comes
            # from a dedicated display-name element or from nowhere; the cost is
            # that a form a person did name well still reaches the queue, and
            # that is the cheap direction of an asymmetric error.
            label=_localized_name(form),
        )


def _connection_facts(root: Element, path: str, blob_sha: str) -> Iterator[SurfaceFact]:
    """Connection references, as value-free secret references."""
    seen: set[str] = set()
    for reference in iter_elements(root, "connectionreference"):
        logical_name = reference.get("connectionreferencelogicalname") or child_text(
            reference, "connectionreferencelogicalname"
        )
        if logical_name is None or logical_name in seen:
            continue
        seen.add(logical_name)
        yield SurfaceFact(
            identity_kind="config_key",
            namespace=SECRET_NAMESPACE,
            local_key=logical_name,
            title=f"Power Platform connection {logical_name}",
            # `source` and `name`, and there is no third field in the model.
            attributes={"source": "connection", "name": logical_name},
            source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
            # The connection this points at is bound in the environment, not in
            # the solution: it can be repointed with no commit (`01` F8.6).
            outside_vcs=True,
        )


def _component(
    *,
    component_type: str,
    api_name: str,
    path: str,
    blob_sha: str,
    data_type: str | None = None,
    label: str | None = None,
) -> SurfaceFact:
    return SurfaceFact(
        identity_kind="metadata_component",
        namespace=NAMESPACE,
        local_key=f"{component_type}.{api_name}",
        title=f"Power Platform {component_type} {api_name}",
        attributes={
            key: value
            for key, value in (
                ("component_type", component_type),
                ("api_name", api_name),
                ("data_type", data_type),
                ("label", label),
            )
            if value is not None
        },
        source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
    )


def _localized_name(element: Element) -> str | None:
    """The display name a solution states, where it states one.

    Power Platform carries display names in `<LocalizedNames><LocalizedName
    description="…"/></LocalizedNames>`. Absent, the component is **unlabelled**
    and reaches `adopt_map.unlabeled` -- the logical name is not a label, and
    promoting it to one would be a label nobody wrote (`01` §8).
    """
    for localized in iter_elements(element, "LocalizedName"):
        description = localized.get("description")
        if description:
            return description
    return None
