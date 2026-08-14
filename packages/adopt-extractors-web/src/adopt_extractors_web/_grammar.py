"""The grammar rung, shared by every extractor in this pack -- `01` F9.2.

**A parser, and nothing else.** tree-sitter turns text into a syntax tree and
hands it back; there is no evaluator to reach and no import of the thing being
parsed. That is what lets `01` F7.2's static-only guarantee survive a pack whose
whole job is reading client frameworks: `web.django.routes` never imports Django,
because it never imports anything from the tree at all.

**Why a query language rather than hand-written node walks.** Each extractor
declares the shape it is looking for as a pattern, and the pattern is the thing a
reviewer reads. A hand-rolled walk hides the shape inside control flow, and the
first framework whose syntax differs slightly gets a branch rather than a second
pattern.

**Determinism** (`02` §7 obligation 3) comes from tree-sitter itself: matches
arrive in document order, which is a property of the file rather than of the
machine. Nothing here iterates a set or a dict for ordering.

**Parsers are cached per process, not per run.** `03` §6 runs extractors in a
`spawn` pool, so each worker builds its own; a cache that crossed the boundary
would have to be picklable, and a `Parser` is not. Rebuilding costs milliseconds
once per language per process.
"""

from collections.abc import Iterator
from functools import cache
from typing import Final

from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language, get_parser

__all__ = [
    "SUPPORTED_LANGUAGES",
    "matches",
    "node_text",
    "parse",
    "string_value",
]

#: The languages this pack asks the grammar rung for. Each is verified loadable
#: by `test_web_grammar.py`; a language absent from the pack degrades to the
#: ladder's next rung rather than failing a run (`01` F9.2).
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"python", "javascript", "typescript", "proto", "graphql"}
)


@cache
def _parser(language: str) -> Parser:
    return get_parser(language)


@cache
def _language(language: str) -> Language:
    return get_language(language)


@cache
def _query(language: str, pattern: str) -> Query:
    return Query(_language(language), pattern)


def parse(language: str, text: str) -> tuple[Node, bytes]:
    """The root node of `text`, plus the bytes the nodes index into.

    Both are returned because a tree-sitter `Node` carries byte offsets rather
    than text, and every caller needs the buffer to read a slice back. Handing
    them out as a pair means no caller re-encodes the source and gets a different
    buffer than the one the offsets refer to -- which silently mis-slices exactly
    when the file is not pure ASCII.

    Args:
        language: A member of `SUPPORTED_LANGUAGES`.
        text: The file's decoded text.
    """
    data = text.encode("utf-8")
    return _parser(language).parse(data).root_node, data


def matches(language: str, pattern: str, root: Node) -> Iterator[dict[str, list[Node]]]:
    """Every match of `pattern`, in document order.

    Yields the capture mapping per match rather than a flat capture list, because
    a flat list loses which route went with which view -- the association is the
    only reason the pattern has two captures.
    """
    cursor = QueryCursor(_query(language, pattern))
    for _match_id, captures in cursor.matches(root):
        yield captures


def node_text(node: Node, data: bytes) -> str:
    """One node's source slice, decoded."""
    return data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def string_value(node: Node, data: bytes) -> str:
    """A string literal's **content**, with quotes and prefixes removed.

    Handles the prefixed forms all three languages use (`r`, `f`, `b`, `u`), the
    triple-quoted forms, and JavaScript backticks. It deliberately does **not**
    interpret escapes: a route is matched by the framework against the literal
    characters, and un-escaping would produce a key the framework never routes to.
    """
    raw = node_text(node, data)
    while raw and raw[0].isalpha():
        raw = raw[1:]
    for quote in ('"""', "'''", '"', "'", "`"):
        if raw.startswith(quote) and raw.endswith(quote) and len(raw) >= 2 * len(quote):
            return raw[len(quote) : -len(quote)]
    return raw
