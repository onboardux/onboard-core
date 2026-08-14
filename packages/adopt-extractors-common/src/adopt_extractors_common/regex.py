"""`common.regex` -- the ladder's last rung before declining (`01` F9.2).

Below this there is only decline-and-record-the-gap, and that is the point: `01`
§1.6 makes *"silence beats guessing"* an invariant, so the bottom of the ladder
has to be a rung somebody can see rather than a heuristic that quietly fills in.

`MAP_CONF_REGEX` is **0.45** against a `MAP_MIN_EMIT_CONFIDENCE` of **0.40**.
That gap is deliberate and it is narrow: a regex fact is knowledge, written with
a confidence that says out loud how it was recovered, and one retune of either
constant turns the whole rung into gaps instead. `01` §6's M6 caps the regex
share at 0.10 of shipped archetypes for exactly that reason -- a map that is
mostly this extractor is a map whose grammars are missing, and the fix is a
grammar rather than tolerance.

**It never claims more than it saw.** A declaration matched by pattern gets a
name and a location and no signature, because a signature recovered by regex from
a language we have no grammar for is a guess with a format.
"""

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "RegexExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.regex",
    version="1.0.0",
    pack="common",
    archetypes=["web", "ai", "data"],
    kinds=["symbol"],
    method="regex",
    # Nothing below this rung: the ladder's next step is decline, which is the
    # absence of a fallback rather than a fallback named "decline". A named one
    # would be an extractor somebody could later give a confidence.
    fallback=None,
)

#: Declaration shapes across the languages this rung covers, in one alternation
#: so a single pass cannot leave one syntax unmatched behind another. Anchored at
#: line start with optional indentation, because a `function` inside a string is
#: not a declaration and matching it would mint a referent that does not exist.
_DECLARATION_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    ^[ \t]*
    (?:
        (?:export\s+)?(?:async\s+)?function\s+(?P<js>[A-Za-z_$][\w$]*)
      | (?:public|private|protected|internal)?\s*(?:static\s+)?
        (?:class|interface|struct)\s+(?P<type>[A-Za-z_][\w]*)
      | func\s+(?:\([^)]*\)\s*)?(?P<go>[A-Za-z_][\w]*)
      | def\s+(?P<rb>[A-Za-z_][\w?!]*)
      | sub\s+(?P<pl>[A-Za-z_][\w]*)
    )
    """,
    re.MULTILINE | re.VERBOSE,
)

#: Languages this rung will attempt at all. A binary or a data file has no
#: declarations, and running a declaration pattern over JSON would mint referents
#: out of coincidence.
_SKIPPED_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"json", "yaml", "toml", "ini", "dotenv", "markdown", "xml", "sql"}
)


class RegexExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `symbol` per matched declaration, in file then position order."""
        for entry in ctx.files():
            ctx.budget.check()
            # `01` F9.2: a lower rung never runs where a higher one succeeded.
            # Two rungs over one language mint one URI twice, with different
            # digests -- which is an identity fork, not a duplicate.
            if ctx.covers(entry):
                continue
            if entry.language is None or entry.language in _SKIPPED_LANGUAGES:
                continue
            text = ctx.text(entry)
            module = ".".join(
                [*PurePosixPath(entry.path).parts[:-1], PurePosixPath(entry.path).stem]
            )
            seen: set[str] = set()
            for match in _DECLARATION_RE.finditer(text):
                name = next(group for group in match.groups() if group is not None)
                key = f"{module}.{name}"
                if key in seen:
                    continue
                seen.add(key)
                yield SurfaceFact(
                    identity_kind="symbol",
                    namespace=entry.language,
                    local_key=key,
                    title=name,
                    attributes={},
                    source_refs=[
                        SourceRef(
                            path=entry.path,
                            start_line=text.count("\n", 0, match.start()) + 1,
                            blob_sha=entry.blob_sha,
                        )
                    ],
                )
