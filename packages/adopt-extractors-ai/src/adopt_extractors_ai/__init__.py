"""`adopt-extractors-ai` -- the AI-deployment pack (`03` §5.10, `05` S1.5).

Six extractors over one archetype, normalizing into the same thirteen-kind
vocabulary every other pack uses. **What makes this pack different from the web
pack is not the parsing, it is where the behaviour lives**: an AI deployment
keeps a large share of what it does outside version control -- a prompt in a
vendor console, a model id resolved at start-up, a retrieval index configured in
a UI -- and `01` F8.6 makes stating that count the pack's first-screen job.

**No provider SDK, no framework import, no model call.** `04` §1 applies to F12
only, and none of F1-F11 contains a model call of any kind. These six read text.
"""

from adopt_map.schemas import Extractor

from adopt_extractors_ai.evalsets import EvalsetsExtractor
from adopt_extractors_ai.graph import GraphExtractor
from adopt_extractors_ai.model_pins import ModelPinsExtractor
from adopt_extractors_ai.prompts import PromptsExtractor
from adopt_extractors_ai.retrieval import RetrievalExtractor
from adopt_extractors_ai.tools import ToolsExtractor

__all__ = [
    "EvalsetsExtractor",
    "GraphExtractor",
    "ModelPinsExtractor",
    "PromptsExtractor",
    "RetrievalExtractor",
    "ToolsExtractor",
    "pack",
]


def pack() -> tuple[Extractor, ...]:
    """Every `ai` extractor a real run may use, in manifest-id order."""
    return (
        EvalsetsExtractor(),
        GraphExtractor(),
        ModelPinsExtractor(),
        PromptsExtractor(),
        RetrievalExtractor(),
        ToolsExtractor(),
    )
