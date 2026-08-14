"""The four run artifacts -- `02` §9, `03` §5.9.

`surface.md` for a human, `surface.json` for review and the labeled-corpus
tooling, and a Mermaid or D2 diagram. **None of them is an interchange
contract**: Build 0's JSON export is the contract, and `01` F11.1 forbids this
build from defining a second one (B1-CR-09).

Every emitter here reads the in-memory `RunResult` and never the store, so a
store failure cannot silently change the output, and every one of them is
testable without a database.
"""

from adopt_map.emit.d2 import render_d2
from adopt_map.emit.json_report import SURFACE_JSON_NAME, render_surface_json
from adopt_map.emit.markdown import SURFACE_MD_NAME, render_markdown, render_stage1
from adopt_map.emit.mermaid import render_mermaid

__all__ = [
    "SURFACE_JSON_NAME",
    "SURFACE_MD_NAME",
    "render_d2",
    "render_markdown",
    "render_mermaid",
    "render_stage1",
    "render_surface_json",
]
