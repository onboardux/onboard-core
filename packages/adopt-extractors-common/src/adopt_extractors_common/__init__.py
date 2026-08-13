"""Extractors shared across every archetype.

S1.1 ships `common.stub` only -- a deliberately trivial extractor, so the write
path is provable before any real extraction exists. S1.3 fills the pack with
`common.config`, `common.secrets`, `common.failure`, `common.ctags` and
`common.regex`.
"""

from adopt_extractors_common.stub import MANIFEST, StubExtractor

__all__ = ["MANIFEST", "StubExtractor"]
