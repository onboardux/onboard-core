"""Extractors shared across every archetype.

S1.1 shipped `common.stub` only -- a deliberately trivial extractor, so the write
path was provable before any real extraction existed. S1.2 adds
`common.stub_tree`, whose output depends on the tree, because a rename cannot be
demonstrated against an extractor that reads nothing (B1-CR-49). S1.3 fills the
pack with `common.config`, `common.secrets`, `common.failure`, `common.ctags` and
`common.regex`.
"""

from adopt_extractors_common.stub import MANIFEST, StubExtractor
from adopt_extractors_common.stub_tree import MANIFEST as TREE_MANIFEST
from adopt_extractors_common.stub_tree import StubTreeExtractor

__all__ = ["MANIFEST", "TREE_MANIFEST", "StubExtractor", "StubTreeExtractor"]
