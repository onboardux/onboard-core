"""Files in, `Document` values out -- the ingest reader.

Markdown and plain text only, which is v6.1 §6 Build 2's v1 scope stated as a
capability rather than as an intention: nothing here can open a PDF or a DOCX,
so the richer-ingestion trigger has to fire before richer ingestion exists.

**Three heuristics, each overridable by frontmatter, each with a stated
default.** Title, kind and audience are guesses about a document a human wrote
for their own reasons, and a guess that cannot be corrected is a guess that
becomes wrong permanently. Frontmatter wins over the heuristic, always.

**The digest is over the body, not the file.** Frontmatter that changed while
the prose did not is not a new revision of the knowledge -- it is a new answer
to "who is this for", which lands on `audience_tag` instead. This is the same
reasoning H5 applies to identities one level up: what changed has to be the
thing the row is about.
"""

import hashlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from adopt_const import MAP_MAX_FILE_BYTES
from adopt_model._enums import ItemKind
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "AUDIENCES",
    "DEFAULT_AUDIENCE",
    "DEFAULT_KIND",
    "Document",
    "body_digest",
    "discover",
    "read_document",
]

#: The audience vocabulary v6.1 §6 Build 4 names for pack assembly. `audience_tag`
#: holds free text -- the manifest declares no enum -- so the vocabulary lives
#: here, where the writer is, and `--audience` accepts anything: a firm with a
#: fifth audience should not have to patch the tool, and a typo is visible in
#: `adopt gaps` rather than silently unmatched.
AUDIENCES: Final[tuple[str, ...]] = ("technical", "client_ops", "end_user", "admin")
DEFAULT_AUDIENCE: Final[str] = "technical"
#: An ingested document describes how something is done. `rationale` is what
#: harvest mines (why it is done that way) and is deliberately not the default
#: here: labelling every README a decision record would fill the decision
#: appendix of every future pack with installation instructions.
DEFAULT_KIND: Final[ItemKind] = "procedure"

_SUFFIXES: Final[frozenset[str]] = frozenset({".md", ".markdown", ".mdown", ".txt"})
_FENCE: Final[str] = "---"
_DIGEST_PREFIX: Final[str] = "sha256"

#: Path fragments that name an audience more reliably than the prose does. Order
#: matters: the first hit wins, so the more specific fragments come first.
_AUDIENCE_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("runbook", "client_ops"),
    ("operations", "client_ops"),
    ("ops/", "client_ops"),
    ("admin", "admin"),
    ("install", "admin"),
    ("deploy", "admin"),
    ("user-guide", "end_user"),
    ("user_guide", "end_user"),
    ("guide", "end_user"),
    ("tutorial", "end_user"),
    ("getting-started", "end_user"),
)


@dataclass(frozen=True, slots=True)
class Document:
    """One ingestable document, resolved.

    `path` is POSIX-relative to the scanned root and is what
    `provenance.source_ref` records, so a bundle written on Windows and read on
    Linux cites the same file.
    """

    path: str
    title: str
    kind: ItemKind
    audiences: tuple[str, ...]
    body_md: str
    digest: str


def body_digest(body: str) -> str:
    """`sha256:<hex>` over the document body.

    Newlines are normalised first. A checkout with `core.autocrlf=true` must
    not present every document as changed -- the CRLF lesson the 0.3.1 release
    paid for, applied where the next writer would meet it.
    """
    normalised = body.replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return f"{_DIGEST_PREFIX}:{digest}"


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """`(frontmatter, body)`. Malformed or absent frontmatter yields `({}, text)`.

    A document whose frontmatter does not parse is ingested as prose rather than
    refused: the YAML is an optional convenience, and refusing the file would
    lose the knowledge in it over a typo in a field nobody required.
    """
    if not text.startswith(_FENCE):
        return {}, text
    lines = text.splitlines(keepends=True)
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            block = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            try:
                loaded = yaml.safe_load(block)
            except yaml.YAMLError:
                return {}, text
            return (loaded if isinstance(loaded, dict) else {}), body
    return {}, text


def _title_of(frontmatter: dict[str, Any], body: str, path: Path) -> str:
    declared = frontmatter.get("title")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return path.stem.replace("-", " ").replace("_", " ").strip() or path.name


def _kind_of(frontmatter: dict[str, Any]) -> ItemKind:
    declared = frontmatter.get("kind")
    if isinstance(declared, str) and declared in _ITEM_KINDS:
        # `ItemKind` is a Literal alias, so the membership test is the narrowing.
        return declared  # type: ignore[return-value]
    return DEFAULT_KIND


def _audiences_of(frontmatter: dict[str, Any], relative_path: str) -> tuple[str, ...]:
    declared = frontmatter.get("audience", frontmatter.get("audiences"))
    if isinstance(declared, str) and declared.strip():
        return (declared.strip(),)
    if isinstance(declared, list):
        tags = tuple(item.strip() for item in declared if isinstance(item, str) and item.strip())
        if tags:
            return tags
    lowered = relative_path.lower()
    for fragment, audience in _AUDIENCE_HINTS:
        if fragment in lowered:
            return (audience,)
    return (DEFAULT_AUDIENCE,)


_ITEM_KINDS: Final[frozenset[str]] = frozenset(
    {"answer", "procedure", "rationale", "surface", "recipe"}
)


def read_document(path: Path, *, root: Path, audience: str | None = None) -> Document:
    """Read one file into a `Document`.

    Args:
        path: The file to read.
        root: What `path` is reported relative to.
        audience: An operator override that beats both frontmatter and the path
            heuristic, because the operator is looking at the document and the
            heuristic is looking at its name.

    Raises:
        AdoptError: ``KNOWLEDGE_SOURCE_UNREADABLE`` when the file cannot be read
            or decoded, and when it exceeds `MAP_MAX_FILE_BYTES`. Refused rather
            than skipped: a document named on the command line and silently
            dropped is a corpus that is quietly smaller than the operator
            believes, which is the shape of every defect Build 1 found by
            running on a real repository.
    """
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message=f"{str(path)!r} could not be read",
            hint="Name a readable Markdown or text file. An unreadable source is refused "
            "rather than skipped, because a smaller corpus that reports success is "
            "indistinguishable from a complete one.",
        ) from error
    if len(raw) > MAP_MAX_FILE_BYTES:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message=f"{str(path)!r} is larger than the {MAP_MAX_FILE_BYTES}-byte ingest bound",
            hint="Split the document, or point ingest at the sections worth binding. The "
            "bound is the walk's, shared so one file cannot make a run unbounded.",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AdoptError(
            ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
            message=f"{str(path)!r} is not valid UTF-8",
            hint="Ingest reads UTF-8 text. A file that is not text is not a document, and "
            "guessing an encoding would put mojibake into the knowledge store.",
        ) from error

    frontmatter, body = split_frontmatter(text)
    relative = _relative_to(path, root)
    return Document(
        path=relative,
        title=_title_of(frontmatter, body, path),
        kind=_kind_of(frontmatter),
        audiences=(audience,) if audience else _audiences_of(frontmatter, relative),
        body_md=body,
        digest=body_digest(body),
    )


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        # Outside the root: the absolute path is the honest citation, and a
        # `../..` chain would resolve differently from wherever it is later read.
        return path.resolve().as_posix()


def _candidates(paths: Sequence[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in _SUFFIXES
            )
        elif path.is_file():
            # A file named outright is ingested whatever its suffix: the
            # operator pointed at it, and the suffix filter exists to keep a
            # directory walk from sweeping up images, not to overrule a person.
            yield path
        else:
            raise AdoptError(
                ErrorCode.KNOWLEDGE_SOURCE_UNREADABLE,
                message=f"{str(path)!r} is neither a file nor a directory",
                hint="Name documents or directories that exist. A path that is not there "
                "is refused rather than contributing nothing, because a typo and an "
                "empty directory would otherwise look identical.",
            )


def discover(
    paths: Sequence[Path], *, root: Path, audience: str | None = None
) -> tuple[Document, ...]:
    """Every document under `paths`, deduplicated and ordered by path.

    Ordering is the writer's, exactly as it is for the export bundle: two runs
    over one tree must produce one sequence of items, or the ids differ and the
    review queue is a different queue.
    """
    seen: dict[str, Document] = {}
    for candidate in _candidates(paths):
        document = read_document(candidate, root=root, audience=audience)
        seen.setdefault(document.path, document)
    return tuple(seen[path] for path in sorted(seen))


def audience_is_known(audience: str) -> bool:
    """Whether an audience is one the pack builder recognises (v6.1 §6 B4)."""
    return audience in AUDIENCES


def unknown_audiences(documents: Iterable[Document]) -> tuple[str, ...]:
    """Audiences outside the vocabulary, sorted -- reported, never rejected."""
    return tuple(
        sorted(
            {
                audience
                for document in documents
                for audience in document.audiences
                if not audience_is_known(audience)
            }
        )
    )
