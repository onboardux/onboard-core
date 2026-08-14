"""`ai.prompts` -- every prompt, and an honest statement about the ones we cannot read.

`02` §3.1 gives `prompt` three namespaces and `05` S1.5 names three sources, and
they are the same list read from two ends:

| Source | Namespace | Readable here? |
|---|---|---|
| A prompt **file** in the tree | `file` | Yes -- the body is the artefact |
| A template **literal** in code | `file`, keyed `<path>#<NAME>` | Yes |
| A **console** or **database** registration | `console` / `db` | **No** |

**The third row is the reason this extractor matters more than its fact count
suggests.** A prompt held in a vendor console is a behaviour-bearing setting that
changes without producing a commit (`01` F8.6), and its body is not in the tree
at all (`01` F8.7). So it is minted `outside_vcs=True` **and** `opaque=True`,
with an **empty** attribute set: the identity says *this exists and we cannot see
it*, and the run records a gap. Filling `template_body` with the id, the file
path, or anything else would be the invention `01` §1.6 forbids -- and it would
be worse than silence, because a digest over invented content compares equal to
itself on every later run and reports that nothing changed.

**A model pin is not this extractor's subject** even though it lives in the same
files: `ai.model_pins` reads those, and two extractors claiming one referent is
how an identity set fills with things nobody addresses (B1-CR-67).
"""

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.fileindex import FileEntry
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact
from tree_sitter import Node

from adopt_extractors_ai._grammar import (
    is_module_level,
    matches,
    node_text,
    parse,
    string_value,
)

__all__ = ["MANIFEST", "PromptsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="ai.prompts",
    version="1.0.0",
    pack="ai",
    archetypes=["ai"],
    kinds=["prompt"],
    method="grammar",
)

#: A directory whose contents are prompts by convention, and the suffixes a
#: prompt file carries. Both are required: `docs/*.md` is documentation, and
#: `prompts/*.py` is code that *holds* prompts rather than being one.
_PROMPT_DIRS: Final[tuple[str, ...]] = ("prompts", "prompt")
_PROMPT_SUFFIXES: Final[tuple[str, ...]] = (".md", ".txt", ".prompt", ".jinja", ".j2")

#: A module-level constant that holds a template. Named by suffix rather than by
#: content, because "this string looks like a prompt" is a judgement and
#: "the author called it a template" is a declaration.
_TEMPLATE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]*(?:_TEMPLATE|_PROMPT)$")

#: `NAME = "..."`. Module level is asserted afterwards through
#: `is_module_level`, not in the pattern -- see that function for why the
#: obvious `(module (expression_statement ...))` anchor matches nothing.
_ASSIGNMENT_PATTERN: Final[str] = """
(assignment
  left: (identifier) @name
  right: (string) @value) @assign
"""

#: Any call with a string first argument: `ConsolePrompt("support-greeting")`.
_CALL_PATTERN: Final[str] = """
(call
  function: [(identifier) @fn (attribute attribute: (identifier) @fn)]
  arguments: (argument_list . (string) @arg)) @call
"""

#: A registration call is a prompt registration when its callee names a prompt
#: **and** names where the prompt lives. Both halves are required: `Prompt("x")`
#: alone does not say the body is elsewhere, and `console("x")` is not a prompt.
_PROMPT_MARKER: Final[str] = "prompt"
_STORE_MARKERS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("console", ("console", "hub", "studio", "registry", "workspace")),
    ("db", ("db", "database", "sql", "table", "row")),
)

#: `{variable}` in a template body. Doubled braces are an escaped literal brace
#: in every templating dialect this build meets, so they are not variables.
_VARIABLE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")

#: A declared output schema: the shape a prompt states it returns.
#:
#: The **backticked** span is preferred over the rest of the line, because the
#: rest of the line keeps going: `Return JSON: `{...}`. Return `other` rather
#: than guessing.` is one sentence whose tail is instruction, not schema, and
#: digesting the tail would make an unrelated wording change read as a schema
#: change.
_OUTPUT_SCHEMA_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:Return|Respond with|Output)\s+(?:JSON|json)\s*:?\s*(?P<declaration>.+)$",
    re.MULTILINE,
)

#: The first backticked span in a declaration line.
_FENCED_RE: Final[re.Pattern[str]] = re.compile(r"`([^`]+)`")

#: A markdown heading -- presentation, per `02` §4.2's `prompt` row.
_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

#: An HTML comment in a prompt file: also presentation.
_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"<!--(.*?)-->", re.DOTALL)


class PromptsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Always true: a prompt can live in any tree, and `extract` declines per
        file. Deciding here would need a second walk (`03` §5.8)."""
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files():
            ctx.budget.check()
            if _is_prompt_file(entry.path):
                yield _file_fact(entry, ctx.text(entry))
            elif entry.language == "python":
                yield from _code_facts(entry, ctx.text(entry))


def _is_prompt_file(path: str) -> bool:
    parts = PurePosixPath(path)
    if parts.suffix.lower() not in _PROMPT_SUFFIXES:
        return False
    return any(part.lower() in _PROMPT_DIRS for part in parts.parts[:-1])


def _file_fact(entry: FileEntry, text: str) -> SurfaceFact:
    """One `prompt` per prompt file, keyed by its repo-relative path."""
    return SurfaceFact(
        identity_kind="prompt",
        namespace="file",
        local_key=entry.path,
        title=PurePosixPath(entry.path).name,
        attributes=_body_attributes(text),
        source_refs=[SourceRef(path=entry.path, start_line=1, blob_sha=entry.blob_sha)],
    )


def _code_facts(entry: FileEntry, text: str) -> Iterator[SurfaceFact]:
    """Template literals, then console and database registrations."""
    root, data = parse("python", text)
    yield from _template_facts(entry, root, data)
    yield from _registration_facts(entry, root, data)


def _template_facts(entry: FileEntry, root: Node, data: bytes) -> Iterator[SurfaceFact]:
    """`ESCALATION_TEMPLATE = "..."` -> `prompt/file/<path>#<NAME>`.

    The `#<NAME>` suffix is B1-CR-71: `02` §3.1 gives the `file` namespace *"a
    repo-relative path"*, which is one key for a file that may declare several
    templates. Without the fragment two templates in one module collapse into one
    identity, and the digest then alternates between them on every run --
    B1-CR-68's failure, arriving from the other direction.
    """
    for capture in matches("python", _ASSIGNMENT_PATTERN, root):
        names = capture.get("name") or []
        values = capture.get("value") or []
        assignments = capture.get("assign") or []
        if not (names and values and assignments):
            continue
        if not is_module_level(assignments[0]):
            # A template built inside a function is a value that function
            # computes, not a declaration the deployment carries.
            continue
        name = node_text(names[0], data)
        if not _TEMPLATE_NAME_RE.match(name):
            continue
        body = string_value(values[0], data)
        yield SurfaceFact(
            identity_kind="prompt",
            namespace="file",
            local_key=f"{entry.path}#{name}",
            title=name,
            attributes=_body_attributes(body),
            source_refs=[
                SourceRef(
                    path=entry.path,
                    start_line=values[0].start_point[0] + 1,
                    blob_sha=entry.blob_sha,
                )
            ],
        )


def _registration_facts(entry: FileEntry, root: Node, data: bytes) -> Iterator[SurfaceFact]:
    """A console or database prompt: an identity, a flag, and **no content**."""
    for capture in matches("python", _CALL_PATTERN, root):
        callees = capture.get("fn") or []
        arguments = capture.get("arg") or []
        if not (callees and arguments):
            continue
        namespace = _store_namespace(node_text(callees[0], data))
        if namespace is None:
            continue
        identifier = string_value(arguments[0], data)
        if not identifier:
            continue
        yield SurfaceFact(
            identity_kind="prompt",
            namespace=namespace,
            local_key=identifier,
            title=identifier,
            # Empty on purpose. `01` F8.7: unreadable content produces the
            # identity with a null semantic digest and an `opaque` marker --
            # never invented content.
            attributes={},
            source_refs=[
                SourceRef(
                    path=entry.path,
                    start_line=arguments[0].start_point[0] + 1,
                    blob_sha=entry.blob_sha,
                )
            ],
            outside_vcs=True,
            opaque=True,
        )


def _store_namespace(callee: str) -> str | None:
    """`console`, `db`, or `None` when the call is not a prompt registration."""
    lowered = callee.lower()
    if _PROMPT_MARKER not in lowered:
        return None
    for namespace, markers in _STORE_MARKERS:
        if any(marker in lowered for marker in markers):
            return namespace
    return None


def _declared_schema(declaration: str) -> str:
    """The schema out of a declaration line.

    A prompt states its output shape and then keeps talking -- *"Return JSON:
    `{...}`. Return `other` rather than guessing"* is one line whose tail is
    instruction. The **first backticked span** is the schema where there is one,
    so re-wording the instruction beside it does not read as a schema change.
    """
    fenced = _FENCED_RE.search(declaration)
    return (fenced.group(1) if fenced else declaration).strip()


def _body_attributes(body: str) -> dict[str, object]:
    """The `02` §4.2 `prompt` projection's fields, semantic and presentation.

    `template_body` carries the body **whitespace-normalized**, which is §4.2's
    own wording: re-indenting a prompt does not change what the model receives in
    any way this build can claim to detect, and treating it as a change would
    write a revision per reformat.
    """
    schema = _OUTPUT_SCHEMA_RE.search(body)
    comments = _COMMENT_RE.findall(body)
    declared = _declared_schema(schema.group("declaration")) if schema else None
    return {
        "template_body": " ".join(body.split()),
        "variables": sorted({match.group(1) for match in _VARIABLE_RE.finditer(body)}),
        "output_schema": declared,
        "headings": _HEADING_RE.findall(body),
        "file_comments": " ".join(" ".join(comments).split()) or None,
    }
