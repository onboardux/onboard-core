"""Running the extractors, with nothing to write to.

**This is the pure half of `run_map`, factored out rather than copied.** Build 6
already recorded why that distinction matters, in `_refresh_support`'s own
docstring: a second extraction path is two extraction paths, and two extraction
paths eventually disagree about what is in the tree. Build 8 needs extraction
without a store — `adopt ci-sense` runs in a customer's CI, observes what the
repository now contains, and posts it to the plane, which owns the canon and
does the classifying (v6.1 §6 Build 8 F4/D10, sprint plan D8-2). So the loop
moved here and `run_map` calls it; there is still exactly one place where an
extractor is invoked and exactly one place where a digest is computed.

What stayed behind in `run_map` is everything that touches a store: the
per-pack transaction, `IdentityFacade.observe`, move recording. What came here
is the part that is a pure function of `(tree, packs)`:

    packs + tree  ->  sightings (observation + digest + which extractor)
                  ->  outcomes  (ran | failed, counts, failure detail)

**Failure accounting is part of the pure half, deliberately.** A crashed
extractor is why every absence below it is unreliable evidence — Build 6's
failed-extractor exemption depends on knowing which extractor failed, and Build
8's ingestion needs the same fact to refuse to retire identities the CI never
looked for. Carrying it in the payload rather than recomputing it server-side
is what lets the plane apply the exemption the local cascade already applies.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from adopt_map.digest import attribute_digest
from adopt_map.observation import Extractor, Observation
from adopt_map.tree import SourceTree
from adopt_obs import AdoptError, get_logger

__all__ = ["ExtractorOutcome", "Pack", "PackSightings", "Sighting", "observe_tree"]

_log = get_logger("adopt_map")


@dataclass(frozen=True, slots=True)
class Pack:
    """A named group of extractors, selected by archetype."""

    name: str
    extractors: tuple[Extractor, ...]


@dataclass(slots=True)
class ExtractorOutcome:
    """What one extractor did, including when it did nothing because it broke."""

    extractor: str
    version: str
    pack: str
    observations: int = 0
    written: int = 0
    status: str = "ok"
    #: The exception *type name* when `status == "failed"`. The type is what
    #: distinguishes a `PermissionError` from a `FileNotFoundError` in one
    #: reading -- B-08 was undiagnosable for days because this was dropped at
    #: emission. The message is deliberately not carried: it can contain a client
    #: path, and this record is printed and logged.
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Sighting:
    """One observation, with the digest and the extractor that produced it.

    The digest is computed here rather than by the caller because it is a pure
    function of the attributes and the extractor version, and computing it in
    two places is how the local store and the plane would come to hold
    different digests for the same referent — which reads downstream as a
    semantic change nobody made (H5's failure, one level up).
    """

    pack: str
    extractor: str
    extractor_version: str
    observation: Observation
    digest: str


@dataclass(slots=True)
class PackSightings:
    """What one pack's extractors saw, and how each of them fared."""

    pack: str
    outcomes: list[ExtractorOutcome] = field(default_factory=list)
    sightings: list[Sighting] = field(default_factory=list)


def observe_tree(tree: SourceTree, *, packs: Iterable[Pack]) -> tuple[PackSightings, ...]:
    """Run every extractor in every pack over `tree`. Writes nothing.

    An extractor that raises is caught, recorded as `failed` with its exception
    type (or its error code, for a typed refusal), and the remaining extractors
    still run — one broken extractor should cost its own observations, not the
    other twenty-eight's. That rule is Build 1's and is unchanged; it lives here
    now because it is a statement about extraction rather than about storage.
    """
    results: list[PackSightings] = []
    for pack in packs:
        found = PackSightings(pack=pack.name)
        for extractor in pack.extractors:
            outcome = ExtractorOutcome(
                extractor=extractor.name, version=extractor.version, pack=pack.name
            )
            found.outcomes.append(outcome)
            observations = _extract(extractor, tree, pack=pack.name, outcome=outcome)
            if observations is None:
                continue
            outcome.observations = len(observations)
            for observation in observations:
                found.sightings.append(
                    Sighting(
                        pack=pack.name,
                        extractor=extractor.name,
                        extractor_version=extractor.version,
                        observation=observation,
                        digest=attribute_digest(
                            observation.attributes, extractor_version=extractor.version
                        ),
                    )
                )
        results.append(found)
    return tuple(results)


def _extract(
    extractor: Extractor, tree: SourceTree, *, pack: str, outcome: ExtractorOutcome
) -> list[Observation] | None:
    """`extractor`'s observations, or `None` when it failed (recorded on `outcome`)."""
    try:
        return list(extractor.extract(tree))
    except AdoptError as error:
        # A typed error from an extractor is still an extractor failure, not a
        # run failure: it is recorded with its code so the report says which
        # rule refused, and the run continues.
        outcome.status = "failed"
        outcome.detail = str(error.code)
    except Exception as error:
        outcome.status = "failed"
        outcome.detail = type(error).__name__
    _log.error("map.extractor_failed", extractor=extractor.name, pack=pack, detail=outcome.detail)
    return None
