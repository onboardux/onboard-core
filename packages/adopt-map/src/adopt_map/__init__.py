"""`adopt map` -- deterministic identity extraction from a repository. Build 1.

This package is the answer to Build 0's known gap: a complete, tested identity
substrate that nothing filled (`identity: 0` after a full `init` was the as-built
fact). Everything every later build queries starts here.

**Four properties, each a refusal rather than an intention:**

1. **No model call, anywhere on this path.** v6.1 §4 R3 makes the deterministic
   mode complete, not a fallback. `adopt map --glue` is *reserved* and is built
   only if measured deterministic coverage on the reference repositories leaves a
   material long tail. There is no call site for a model in this package.
2. **Nothing in the target tree is executed or written.** Extractors receive a
   read-only `SourceTree` and have no capability to do either.
3. **Idempotent by the write path, not by a check.** `IdentityFacade.observe` is
   keyed on the URI, so a re-run over an unchanged tree writes nothing. Build 1
   *records* attribute digests; Build 6 *compares* them and owns what a change
   means.
4. **A failing extractor is loud.** Every outcome is recorded with its exception
   type. A silent extractor failure and a genuinely smaller system look identical
   from outside, which is exactly how B-08 stayed undiagnosed.

The pack layout is one distribution with packs as modules (v6.1 §6), so
`pip install adopt-cli` always yields a working `adopt map`.
"""

from adopt_map.digest import attribute_digest, canonical_attributes
from adopt_map.expected import load_expected, missing_identities
from adopt_map.moves import (
    MoveCandidate,
    MoveOutcome,
    ObservedIdentity,
    StoredIdentity,
    detect_moves,
)
from adopt_map.observation import Extractor, Observation, Span
from adopt_map.packs import registry
from adopt_map.report import StoredRevision, build_report
from adopt_map.runner import (
    ExtractorOutcome,
    IdentityWriter,
    MapReport,
    Pack,
    run_map,
    select_packs,
)
from adopt_map.tree import SourceTree, TreeFile

__all__ = [
    "Extractor",
    "ExtractorOutcome",
    "IdentityWriter",
    "MapReport",
    "MoveCandidate",
    "MoveOutcome",
    "Observation",
    "ObservedIdentity",
    "Pack",
    "SourceTree",
    "Span",
    "StoredIdentity",
    "StoredRevision",
    "TreeFile",
    "attribute_digest",
    "build_report",
    "canonical_attributes",
    "detect_moves",
    "load_expected",
    "missing_identities",
    "registry",
    "run_map",
    "select_packs",
]
