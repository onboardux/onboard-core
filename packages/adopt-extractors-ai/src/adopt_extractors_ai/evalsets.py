"""`ai.evalsets` -- the cases and thresholds a deployment judges itself against.

`01` F8.2 files eval sets under `config_key`, and `02` §3.1's `config_key`
namespace names *the source of a key*. The source here is an eval set, so the
namespace is **`evalset`** -- a value this sprint adds to §3.1's list, recorded
as B1-CR-72 and `docs/pack/OPEN-DECISIONS.md` OD-13 rather than chosen quietly.

**Two kinds of key and both are behaviour-bearing.** A case is what the
deployment is asked to get right; a threshold is the bar it has to clear. A
change to either changes what "passing" means, which is exactly the sort of thing
`01` §1.1 says this build exists to enumerate -- and neither is visible in the
prompts, the pins or the graph.

**`common.config` no longer claims these documents.** A document declaring
`evalset:` is this extractor's subject, the same rule B1-CR-67 established for an
OpenAPI document, and it lives in `_documents.py` so both halves of the decision
are in one place.
"""

from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, Final

from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

from adopt_const import MAP_CONFIG_VALUE_MAX_CHARS
from adopt_extractors_ai._documents import EVALSET_KEYS, declared_section, load_document

__all__ = ["MANIFEST", "NAMESPACE", "EvalsetsExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="ai.evalsets",
    version="1.0.0",
    pack="ai",
    archetypes=["ai"],
    kinds=["config_key"],
    method="declared",
)

#: `02` §3.1's `config_key` namespace for an eval set (B1-CR-72).
NAMESPACE: Final[str] = "evalset"

_NAME_KEYS: Final[tuple[str, ...]] = ("name", "suite", "id")
_CASE_KEYS: Final[tuple[str, ...]] = ("cases", "examples", "samples")
_THRESHOLD_KEYS: Final[tuple[str, ...]] = ("thresholds", "gates", "targets")
_CASE_ID_KEYS: Final[tuple[str, ...]] = ("id", "name", "case")


class EvalsetsExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        for entry in ctx.files():
            ctx.budget.check()
            document = load_document(entry.path, ctx.text(entry))
            if document is None:
                continue
            section = declared_section(document, EVALSET_KEYS)
            if section is None:
                continue
            suite = str(_first(section, _NAME_KEYS) or _stem(entry.path))
            yield from _threshold_facts(section, suite, entry.path, entry.blob_sha)
            yield from _case_facts(section, suite, entry.path, entry.blob_sha)


def _threshold_facts(
    section: Mapping[str, Any], suite: str, path: str, blob_sha: str
) -> Iterator[SurfaceFact]:
    thresholds = _first(section, _THRESHOLD_KEYS)
    if not isinstance(thresholds, Mapping):
        return
    for name in sorted(thresholds):
        yield SurfaceFact(
            identity_kind="config_key",
            namespace=NAMESPACE,
            local_key=f"{suite}.thresholds.{name}",
            title=f"{suite} threshold {name}",
            attributes={
                "key_path": f"{suite}.thresholds.{name}",
                "value_type": type(thresholds[name]).__name__,
                "default": _bounded(thresholds[name]),
                "required": True,
                "group_label": suite,
            },
            source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
        )


def _case_facts(
    section: Mapping[str, Any], suite: str, path: str, blob_sha: str
) -> Iterator[SurfaceFact]:
    cases = _first(section, _CASE_KEYS)
    if not isinstance(cases, Sequence) or isinstance(cases, str):
        return
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        identifier = _first(case, _CASE_ID_KEYS)
        if identifier is None:
            # A case with no id has no stable key: its position would change the
            # moment somebody reorders the file, and every reorder would then
            # read as a rename. Skipped rather than keyed by index.
            continue
        yield SurfaceFact(
            identity_kind="config_key",
            namespace=NAMESPACE,
            local_key=f"{suite}.cases.{identifier}",
            title=f"{suite} case {identifier}",
            attributes={
                "key_path": f"{suite}.cases.{identifier}",
                "value_type": "case",
                "required": True,
                "group_label": suite,
                # The expectation, not the question: what the case *asserts* is
                # the behaviour-bearing half, and it is bounded like every other
                # recorded value (`MAP_CONFIG_VALUE_MAX_CHARS`).
                "description": _bounded(_first(case, ("expects", "expected", "assert"))),
            },
            source_refs=[SourceRef(path=path, blob_sha=blob_sha)],
        )


def _bounded(value: Any) -> str | None:
    """A value recorded as text, truncated to `MAP_CONFIG_VALUE_MAX_CHARS`."""
    if value is None:
        return None
    text = str(value)
    return text[:MAP_CONFIG_VALUE_MAX_CHARS]


def _first(section: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in section and section[key] is not None:
            return section[key]
    return None


def _stem(path: str) -> str:
    return PurePosixPath(path).stem
