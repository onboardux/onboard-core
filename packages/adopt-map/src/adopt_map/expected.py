"""The recall floor -- `adopt map --check-expected` (v6.1 §6 H1, invariant #1).

**Why a curated list and not a coverage percentage.** A recall ratio over a
golden corpus improves when the denominator shrinks, and every incentive around
it points at shrinking the denominator: exclude the hard directory, narrow the
corpus, redefine what counts as extractable. Nobody has to be dishonest for it to
happen -- the number goes up and the map gets worse, and the metric reports
success either way. That is the whole class of failure this repository has now
hit five times, most recently in the instrument written to catch the other four
(CR-52), and once inside a gate whose blind output was `0/0 covered (100%)`
(CR-67).

A named list cannot be gamed that way. Either
`onboard-v1://.../endpoint/-/POST%20%2Flogin%2Faccess-token` is in the store or
it is not, and the failure **names it**. Removing an entry to make the check pass
is a visible edit to a reviewed file, which is exactly the property a coverage
ratio lacks.

**The list encodes a belief, and the belief can be wrong.** Reference repository
#1's first list carried two environment variables its author wrote from memory
that do not exist at that pin. That is not an argument against the list -- it is
the argument *for* checking it against a real store rather than trusting anyone's
recollection, including ours.
"""

from collections.abc import Iterable, Sequence

__all__ = ["load_expected", "missing_identities"]


def load_expected(text: str) -> tuple[str, ...]:
    """The URIs a curated expected-identities file names, in file order.

    Blank lines and `#` comments are skipped, so the file can explain itself --
    and reference repository #1's does, at length, because *why* an entry is on
    the list is what a future reader needs in order to judge whether removing it
    is honest.

    Duplicates are collapsed rather than refused: a list assembled by two people
    is allowed to name the same endpoint twice, and failing on that would make
    the check about the file's tidiness rather than about the map's recall.
    """
    seen: dict[str, None] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        seen.setdefault(stripped, None)
    return tuple(seen)


def missing_identities(expected: Iterable[str], present: Iterable[str]) -> tuple[str, ...]:
    """Every expected URI absent from the store, in the order the file named them.

    File order, not sorted order: the list is written in the order a person
    reads a repository, so the misses come back grouped the way they were
    grouped when someone decided they mattered.
    """
    stored: Sequence[str] = tuple(present)
    known = set(stored)
    return tuple(uri for uri in expected if uri not in known)
