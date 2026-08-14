"""Extractors shared across every archetype.

S1.1 shipped `common.stub` only -- a deliberately trivial extractor, so the write
path was provable before any real extraction existed. S1.2 adds
`common.stub_tree`, whose output depends on the tree, because a rename cannot be
demonstrated against an extractor that reads nothing (B1-CR-49). S1.3 fills the
pack with `common.config`, `common.secrets`, `common.failure`, `common.ctags` and
`common.regex`.

**`pack()` registers `common.stub_tree` and not `common.stub`** -- B1-CR-61.
`03` §5.10's table lists both in this pack, and the two are not the same kind of
thing. `common.stub_tree` **reads the tree**: it recovers one `symbol` per
top-level Python declaration with its signature, which is a real extractor that
happens to have a stub's name, and it is the Python rung the ladder needs.
`common.stub` reads **nothing** and emits four fixed facts by design -- it exists
so the write path was provable before extraction existed. Registering it would
write an endpoint, a config key and a symbol into every client's identity set
regardless of whether they exist, which is precisely the invention `01` §1.6
forbids. It stays importable, because the S1.1 suite is the regression evidence
for the write path, and out of `pack()`, because a fixture that reaches
production by default is not a fixture.
"""

from adopt_map.schemas import Extractor

from adopt_extractors_common.config import ConfigExtractor
from adopt_extractors_common.ctags import CtagsExtractor
from adopt_extractors_common.failure import FailureExtractor
from adopt_extractors_common.regex import RegexExtractor
from adopt_extractors_common.secrets import SecretsExtractor
from adopt_extractors_common.stub import MANIFEST, StubExtractor
from adopt_extractors_common.stub_tree import MANIFEST as TREE_MANIFEST
from adopt_extractors_common.stub_tree import StubTreeExtractor

__all__ = [
    "MANIFEST",
    "TREE_MANIFEST",
    "ConfigExtractor",
    "CtagsExtractor",
    "FailureExtractor",
    "RegexExtractor",
    "SecretsExtractor",
    "StubExtractor",
    "StubTreeExtractor",
    "pack",
]


def pack() -> tuple[Extractor, ...]:
    """Every `common` extractor a real run may use, in manifest-id order.

    Ordered here as well as in the registry, so that a reader of this list and a
    reader of the run plan see the same sequence -- `02` §7 obligation 3 is an
    extractor obligation, and the pack keeping its own half of it costs one
    `sorted`.
    """
    return (
        ConfigExtractor(),
        CtagsExtractor(),
        FailureExtractor(),
        RegexExtractor(),
        SecretsExtractor(),
        StubTreeExtractor(),
    )
