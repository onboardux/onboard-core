"""`common.stub_tree` -- a stub whose output depends on the tree, and why S1.2 needs one.

`05` S1.2's Final Output Validation says: *"Rename a stub fixture file, re-run,
and confirm one `moved` revision with `alias_of_identity_id` set and zero
orphans."* **That line is not executable against `common.stub`** (B1-CR-49):
S1.1's stub reads nothing and emits four fixed facts, deliberately, so that the
write path could be proven before any real extraction existed. There is no file
to rename and no output that a rename could change.

So S1.2 adds the smallest extractor that makes a rename observable, and it is a
genuine extractor rather than a test double: it emits one `symbol` fact per
top-level `def` or `class`, keyed `<dotted-module-path>.<name>`, with the
declared signature as its semantic content. That shape is exactly what the move
rule needs to be exercised honestly:

* **Renaming a file** changes the module half of every key and leaves every
  signature alone -- `sem` matches, so the rule emits a move.
* **Editing a signature** changes `sem`, so the rule declines: a referent that
  moved *and* changed is not one the digest can vouch for.
* **Two files with identical declarations** produce two identical `sem` digests,
  which is the ambiguity case, reached without contriving anything.

**It reads and never executes** (`02` §7 obligation 1). Declarations are
recovered by a line-anchored regex over the file's text -- no `import`, no
`exec`, no `compile`, no `ast` walk of client code that could be made to run.
S1.3's plugin audit enforces that class of rule for every extractor; this one is
written to pass it on the day it arrives.

**It is deterministic** (obligation 3): files are walked in sorted order and
declarations are emitted in the order they appear in the file, so two runs over
one tree emit one sequence.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

__all__ = ["MANIFEST", "StubTreeExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.stub_tree",
    version="1.0.0",
    pack="common",
    archetypes=["web", "ai", "data"],
    kinds=["symbol"],
    method="grammar",
)

#: A top-level `def` or `class`, anchored at column zero so nested declarations
#: are ignored: a nested function's identity is its enclosing scope's business,
#: and emitting one keyed as top-level would fork the referent.
_DECLARATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:def|class)\s+(?P<name>[A-Za-z_]\w*)\s*(?:\((?P<params>[^)]*)\))?",
    re.MULTILINE,
)

_SOURCE_SUFFIX: Final[str] = ".py"


class StubTreeExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Whether the tree holds anything this extractor can read."""
        return any(Path(root).rglob(f"*{_SOURCE_SUFFIX}"))

    def extract(self, root: str) -> Iterator[SurfaceFact]:
        """One fact per top-level declaration, in a deterministic order."""
        base = Path(root)
        for path in sorted(base.rglob(f"*{_SOURCE_SUFFIX}")):
            relative = path.relative_to(base)
            module = ".".join([*relative.parts[:-1], relative.stem])
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in _DECLARATION_RE.finditer(text):
                name = match.group("name")
                parameters = (match.group("params") or "").strip()
                yield SurfaceFact(
                    identity_kind="symbol",
                    namespace="python",
                    local_key=f"{module}.{name}",
                    title=name,
                    attributes={"signature": f"{name}({parameters})"},
                    source_refs=[
                        SourceRef(
                            path=relative.as_posix(), start_line=_line_of(text, match.start())
                        )
                    ],
                )


def _line_of(text: str, offset: int) -> int:
    """The 1-based line a match starts on."""
    return text.count("\n", 0, offset) + 1
