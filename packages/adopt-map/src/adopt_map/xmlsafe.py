"""The one XML seam -- `03` §6, `01` N8. **B1-CR-76.**

S1.6 is the first sprint whose subject is an **export bundle**, and every bundle
this build reads is XML: a Salesforce metadata retrieve, a ServiceNow update set,
a Power Platform solution. Two packs need to read it, and a pack may not import
another pack (B1-CR-74), so the reader lives here beside `netguard` and
`execseam` -- the other two places where a capability with a hazard attached is
given one home instead of a habit.

**The hazard is entity expansion, and it is not hypothetical.** Python's
`xml.etree.ElementTree` is documented-vulnerable to the billion-laughs and
quadratic-blowup attacks: a document of a few hundred bytes can expand to
gigabytes inside the parser, before any of our code runs. `01` N8 is *"no client
execution -- the poisoned fixture does not detonate"*, and a client tree that
detonates our **parser** fails the same promise by a different route. So:

1. **A document declaring a DTD or an entity is refused, unparsed.** The check
   is a text scan of the head, before `ElementTree` sees the bytes, because a
   check that runs after parsing runs after the damage. This costs us documents
   that legitimately carry a `<!DOCTYPE>`; that is the intended trade, and the
   caller records a gap rather than pretending the file was empty.
2. **A refusal returns `None`, never an exception.** The same contract
   `execseam` uses for an absent tool (`01` F9.2): the ladder degrades and the
   run continues. An unreadable bundle entry is a gap in a map, not a failed
   run.
3. **No network, ever.** `ElementTree` resolves no external reference here
   because nothing carrying one survives rule 1 -- which is why the rule is
   about *declarations* rather than about the `SYSTEM` keyword alone.

**Why not `defusedxml`.** It is the obvious answer and it is a new third-party
distribution, which is B1-CR-50's and B1-CR-65's argument again: a dependency
changes the SBOM's locked-runtime union, Build 0 CR-58's exact payload
inventory, and the licence gate treats an undeclared one as `in-binary` and
fails closed -- against ~40 lines whose whole job is to *refuse* input. The
refusal is strictly stronger than `defusedxml`'s default posture anyway: that
library disarms entity expansion, this module declines the document.

**Determinism** (`02` §7 obligation 3) comes from the parser: `ElementTree`
yields children in document order, which is a property of the file rather than
of the machine.

**`Element` is re-exported here on purpose.** An extractor that wanted to name
the type would otherwise have to `import xml.etree.ElementTree`, and `xml` is
not in `plugins.PERMITTED_IMPORT_ROOTS` -- correctly, because that import is the
hazard this module exists to contain. Re-exporting the *type* lets a pack write
an honest signature while the only call into the parser stays here.
"""

import re
from collections.abc import Iterator
from typing import Final
from xml.etree.ElementTree import Element, ParseError, XMLParser, fromstring

from adopt_const import MAP_XML_MAX_DEPTH

__all__ = [
    "ENTITY_DECLARATION",
    "Element",
    "child",
    "child_text",
    "iter_elements",
    "local_name",
    "parse_xml",
    "text_of",
]

#: A DTD or entity declaration anywhere in the head of a document. Both spellings
#: matter: `<!DOCTYPE` opens the door and `<!ENTITY` walks through it, and a
#: document carrying the second without the first is malformed in a way we still
#: decline to hand to a parser.
ENTITY_DECLARATION: Final[re.Pattern[str]] = re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)

#: How much of a document is scanned for a declaration. A DTD is declared in the
#: prolog, before the root element, so a head scan is not a heuristic -- it is
#: where the grammar puts the thing. Matched to `adopt_map.documents`, which
#: reads a head for the same structural reason.
_HEAD_CHARS: Final[int] = 4096


def parse_xml(text: str) -> Element | None:
    """The document's root element, or `None` if it cannot be read safely.

    Args:
        text: The file's decoded text.

    Returns:
        The root `Element`, or `None` when the document declares a DTD or an
        entity, or is not well-formed. **The caller records a gap** -- returning
        `None` rather than raising is what lets a bundle with one bad entry
        still produce a map of the other entries (`01` F9.2).
    """
    if ENTITY_DECLARATION.search(text[:_HEAD_CHARS]):
        return None
    try:
        # A fresh parser per document. The default `XMLParser` carries no
        # entity-resolution state we want reused across client files, and
        # constructing it here keeps that true whatever a future default does.
        parser = XMLParser()  # noqa: S314 -- see the module docstring: declarations are refused above
        return fromstring(text, parser=parser)  # noqa: S314 -- ditto; this is the guarded call site
    except (ParseError, ValueError):
        return None


def local_name(tag: str) -> str:
    """An element's name without its namespace.

    A Salesforce retrieve declares a default namespace, so every tag arrives as
    `{http://soap.sforce.com/2006/04/metadata}fields`, while a ServiceNow update
    set declares none and arrives as `sys_update_xml`. Extractors ask about the
    **name**, so the namespace is stripped in one place rather than in each of
    them -- and stripping it is safe here because a bundle is one vendor's
    document, not a mixed-vocabulary one where two namespaces could collide on a
    local name.
    """
    return tag.rpartition("}")[2] if tag.startswith("{") else tag


def text_of(element: Element | None) -> str | None:
    """One element's text, stripped, or `None` when it is absent or empty.

    An empty element and a missing one are the same fact to every caller here --
    *the bundle does not say* -- and collapsing them once means no extractor has
    to remember which shape a given vendor emits.
    """
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


def child(element: Element, name: str) -> Element | None:
    """The first direct child with local name `name`, in document order."""
    for candidate in element:
        if local_name(candidate.tag) == name:
            return candidate
    return None


def child_text(element: Element, name: str) -> str | None:
    """The text of the first direct child named `name`. The common case."""
    return text_of(child(element, name))


def iter_elements(root: Element, name: str, *, depth: int = 0) -> Iterator[Element]:
    """Every descendant with local name `name`, in document order.

    Depth-bounded (`MAP_XML_MAX_DEPTH`) and namespace-insensitive. Document order is
    what makes an extractor built on this deterministic without sorting
    afterwards, which matters because two runs over one bundle must emit one
    sequence (`02` §7 obligation 3).
    """
    if depth >= MAP_XML_MAX_DEPTH:
        return
    for element in root:
        if local_name(element.tag) == name:
            yield element
        yield from iter_elements(element, name, depth=depth + 1)
