"""The grammar rung for this pack -- `01` F9.2.

**A parser, and nothing else.** tree-sitter turns text into a syntax tree and
hands it back; there is no evaluator to reach and no import of the thing being
parsed. An AI deployment is the archetype where that guarantee is least
negotiable: importing `app/graph.py` to introspect the graph would construct
model clients, read `os.environ` and, in a client's repository, quite possibly
open a socket. So this pack reads text, exactly like the web pack reads Django.

**This file is a deliberate copy of the web pack's rung helper, not a shared
module.** A pack that imported another pack's private module would make the two
packs one deployable unit and would let a change made for `web` alter what `ai`
observes. The shared home becomes worth building when a **third** consumer
arrives -- S1.6 ships three more packs -- and it belongs in `adopt_map` beside
the other framework seams rather than in whichever pack happened to be first.

**Determinism** (`02` §7 obligation 3) comes from tree-sitter itself: matches
arrive in document order, which is a property of the file rather than of the
machine.
"""

from collections.abc import Iterator
from functools import cache
from typing import Final

from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_language_pack import get_language, get_parser

__all__ = [
    "SUPPORTED_LANGUAGES",
    "docstring_of",
    "is_module_level",
    "keyword_arguments",
    "matches",
    "node_text",
    "parse",
    "string_value",
]

#: The languages this pack asks the grammar rung for. A language absent from the
#: pack degrades to the ladder's next rung rather than failing a run (`01` F9.2).
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"python", "javascript", "typescript"})


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
    than text, and every caller needs the buffer to read a slice back.

    Args:
        language: A member of `SUPPORTED_LANGUAGES`.
        text: The file's decoded text.
    """
    data = text.encode("utf-8")
    return _parser(language).parse(data).root_node, data


def matches(language: str, pattern: str, root: Node) -> Iterator[dict[str, list[Node]]]:
    """Every match of `pattern`, in document order."""
    cursor = QueryCursor(_query(language, pattern))
    for _match_id, captures in cursor.matches(root):
        yield captures


def node_text(node: Node, data: bytes) -> str:
    """One node's source slice, decoded."""
    return data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def string_value(node: Node, data: bytes) -> str:
    """A string literal's **content**, with quotes and prefixes removed.

    Escapes are deliberately not interpreted: a prompt template is sent to a
    model as the literal characters the file carries, and un-escaping here would
    digest a body nobody ever sends.
    """
    raw = node_text(node, data)
    while raw and raw[0].isalpha():
        raw = raw[1:]
    for quote in ('"""', "'''", '"', "'", "`"):
        if raw.startswith(quote) and raw.endswith(quote) and len(raw) >= 2 * len(quote):
            return raw[len(quote) : -len(quote)]
    return raw


def is_module_level(node: Node) -> bool:
    """Whether `node` is a statement of the module rather than of a function.

    **Two shapes, because the grammar has both.** `tree-sitter-python` wraps a
    module-level assignment in an `expression_statement` in some versions and
    attaches it straight to `module` in others -- verified on the pinned pack,
    where `triage_model = ChatOpenAI(...)` is a direct child of `module`. A query
    that anchors `(module (expression_statement (assignment ...)))` therefore
    matches nothing at all, silently, which is how the first run of this pack
    reported zero model pins on a tree holding three.

    Asked here rather than expressed in the pattern so the shape lives in one
    place instead of in every query that wants a module-level declaration.
    """
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "module":
        return True
    return (
        parent.type == "expression_statement"
        and parent.parent is not None
        and (parent.parent.type == "module")
    )


def docstring_of(definition: Node, data: bytes) -> str | None:
    """A function's docstring, whichever way the grammar wraps it."""
    body = definition.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type == "expression_statement" and first.named_children:
        first = first.named_children[0]
    if first.type != "string":
        return None
    return " ".join(string_value(first, data).split()) or None


def keyword_arguments(call: Node, data: bytes) -> dict[str, Node]:
    """`{name: value node}` for one call's keyword arguments.

    A dict rather than a query capture because the callers here ask for named
    arguments they may or may not find -- `temperature`, `max_tokens`, `top_p` --
    and a query per optional argument would multiply the patterns by the
    arguments.
    """
    found: dict[str, Node] = {}
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return found
    for child in arguments.named_children:
        if child.type != "keyword_argument":
            continue
        name = child.child_by_field_name("name")
        value = child.child_by_field_name("value")
        if name is not None and value is not None:
            found[node_text(name, data)] = value
    return found
