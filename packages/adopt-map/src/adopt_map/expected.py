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

from adopt_obs import AdoptError, ErrorCode

__all__ = ["load_expected", "missing_identities"]


def load_expected(text: str, *, source: str | None = None) -> tuple[str, ...]:
    """The URIs a curated expected-identities file names, in file order.

    Blank lines and `#` comments are skipped, so the file can explain itself --
    and reference repository #1's does, at length, because *why* an entry is on
    the list is what a future reader needs in order to judge whether removing it
    is honest.

    Duplicates are collapsed rather than refused: a list assembled by two people
    is allowed to name the same endpoint twice, and failing on that would make
    the check about the file's tidiness rather than about the map's recall.

    Args:
        text: The file's contents.
        source: The path, for the refusal's message only.

    Raises:
        AdoptError: ``MAP_EXPECTED_LIST_UNREADABLE`` when the file names **no
            URI at all** -- empty, or nothing but comments and blank lines.

            **This is the whole point of the code and it was not implemented**
            (B1-001, T1.7). §13's row for it already says the list is "refused
            rather than treated as an empty list, because an empty list passes
            every check", and the missing-file branch honoured that while a
            comment-only file did not: `adopt map --check-expected empty.txt`
            exited `0` and emitted no `expected` payload at all, so invariant
            #1's instrument reported perfect recall over nothing. Emptying the
            file is also the cheapest way to make a failing recall floor pass,
            which is precisely what a *named list* exists to make impossible.

            The same code as the missing file rather than a second one: the
            subject is one thing -- the list is not usable -- and the message
            names which way. `MAP_EXPECTED_IDENTITY_MISSING` is deliberately not
            reused: that is a **finding** about the map, exiting `4`, and this
            is a usage error about the invocation, exiting `2`.
    """
    seen: dict[str, None] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        seen.setdefault(stripped, None)
    if not seen:
        named = f"{source!r} " if source else ""
        raise AdoptError(
            ErrorCode.MAP_EXPECTED_LIST_UNREADABLE,
            message=f"the expected-identities list {named}names no URI",
            hint="Put one canonical URI per line. A list with no entries is refused "
            "rather than passing, because an empty list passes every check -- and "
            "emptying the file is the cheapest way to silence a real recall miss.",
        )
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
