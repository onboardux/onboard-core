"""`common.ctags` -- the ladder's second rung (`01` F9.2, `03` §5.10).

When a language has no grammar, the ladder degrades to `ctags` before it degrades
to `regex`. This extractor is that rung: it shells `universal-ctags` through
`adopt_map.execseam` -- **the only sanctioned subprocess route** -- and turns its
JSON output into `symbol` facts at `MAP_CONF_CTAGS` (0.70).

**Three things about the licence, because they are the reason the seam exists.**
`universal-ctags` is GPL-2.0+. `03` §2 admits it *"subprocess only, never
linked"*, and `03` §7.3 permits copyleft in `subprocess` mode. It has a
`subprocess-deps.toml` row, without which the licence gate treats it as
`in-binary` and rejects it -- failing closed. Nothing here imports it, links it,
or ships it.

**A missing binary is a degradation, not a failure.** `execseam.run_tool` returns
`None` when the tool does not resolve, `applies_to` answers `False`, and the
ladder records `tool_unavailable` and drops to `regex`. That is the tool-absent
arm `05` S1.3 names explicitly, and it is the common case on a developer laptop.
"""

import json
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Final

from adopt_map.context import ExtractorContext
from adopt_map.execseam import run_tool, tool_available
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "CtagsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.ctags",
    version="1.0.0",
    pack="common",
    archetypes=["web", "ai", "data"],
    kinds=["symbol"],
    method="ctags",
    # Named so the scheduler and the ladder agree about where this rung falls
    # back to when the watchdog stops it (`01` F7.3).
    fallback="common.regex",
)

#: ctags kinds worth an identity. A local variable is not a referent anything
#: binds to, and minting one per local would make the identity set a function of
#: how somebody wrote a loop.
_EMITTED_KINDS: Final[frozenset[str]] = frozenset({"function", "class", "method", "member"})

#: Fixed argv. `--output-format=json` gives one object per line; `-f -` writes to
#: stdout. **The tree path is a separate element**, never interpolated -- a
#: repository directory named `; rm -rf ~` is a repository we index.
_ARGV: Final[tuple[str, ...]] = (
    "--output-format=json",
    "--fields=+n",
    "-f",
    "-",
)


class CtagsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Only when the binary is actually present on this machine."""
        del root
        return tool_available("ctags")

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `symbol` per tagged declaration, in file then line order.

        Files are passed **one at a time** rather than as one large argv. That
        costs process starts and buys two things worth more: the budget is
        checked between files (`02` §7 obligation 7), and a file that makes ctags
        fail costs that file rather than the whole run.
        """
        for entry in ctx.files():
            ctx.budget.check()
            # `01` F9.2: a lower rung never runs where a higher one succeeded.
            # Two rungs over one language mint one URI twice, with different
            # digests -- which is an identity fork, not a duplicate.
            if ctx.covers(entry):
                continue
            if entry.language is None:
                continue
            result = run_tool("ctags", [*_ARGV, entry.path], cwd=Path(ctx.root))
            if result is None or result.exit_status != 0:
                continue
            for line in result.stdout.splitlines():
                fact = _fact_from(line, entry.path, entry.blob_sha, entry.language)
                if fact is not None:
                    yield fact


def _fact_from(line: str, path: str, blob_sha: str, language: str) -> SurfaceFact | None:
    """One ctags JSON line -> a `symbol` fact, or `None` when it is not one.

    `json.loads` and nothing else: ctags output is data, and a parser that
    evaluated it would be executing a tool's opinion of client code.
    """
    try:
        tag = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(tag, dict) or tag.get("_type") != "tag":
        return None
    kind = tag.get("kind")
    name = tag.get("name")
    if kind not in _EMITTED_KINDS or not isinstance(name, str):
        return None
    scope = tag.get("scope")
    module = ".".join([*PurePosixPath(path).parts[:-1], PurePosixPath(path).stem])
    qualified = (
        f"{module}.{scope}.{name}" if isinstance(scope, str) and scope else f"{module}.{name}"
    )
    line_number = tag.get("line")
    return SurfaceFact(
        identity_kind="symbol",
        namespace=language,
        local_key=qualified,
        title=name,
        # ctags gives a name and a location, not a signature. Recording an empty
        # signature would be a claim; recording nothing is the honest shape, and
        # it is exactly why this rung bands lower than `grammar`.
        attributes={},
        source_refs=[
            SourceRef(
                path=path,
                start_line=line_number if isinstance(line_number, int) else None,
                blob_sha=blob_sha,
            )
        ],
    )
