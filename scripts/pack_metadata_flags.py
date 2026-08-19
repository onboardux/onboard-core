"""Which distributions the packed binary may carry `importlib.metadata` for.

**Nuitka refuses metadata for a distribution whose package it is not
compiling** -- `FATAL: Error, including metadata for distribution 'X' without
including related package 'Y'` -- so the set passed to
`--include-distribution-metadata` must be exactly what ends up inside the
binary, not every distribution the release publishes. Passing all fifteen
failed the `v0.3.1` tag run on all three platforms, because `adopt-workflow` is
a library `adopt-cli` does not depend on and therefore is not in the binary.

**Why the binary needs any of it.** Modules resolve their own version through
`importlib.metadata`, and they do not all ask about the same distribution:
`version --json` asks for `adopt-cli`, while `adopt_store.writer_identity` --
the value stamped into `schema_meta.written_by` and into every exported bundle
manifest -- asks for `adopt-store`. The `0.3.0` binary carried metadata for the
first only, so the second took a silent fallback and wrote
`adopt-core/0.0.0+unknown` into a contract-governed field.

**The set is derived, never listed**, and the derivation is only trustworthy
because `first-party-deps` gates every first-party import as declared: the
declared closure equals the compiled closure exactly when no module imports
something its manifest omits. Those two gates hold each other up.
"""

import importlib.metadata as metadata
import os
import re
import sys
from collections import deque
from typing import Final

#: The entry point Nuitka compiles. Everything reachable from here is in the
#: binary; everything else is not.
ROOT_DISTRIBUTION: Final[str] = "adopt-cli"

#: Below this the run is not evidence: a closure of one means the walk found no
#: requirements at all, which would silently reproduce the `0.3.0` defect.
MIN_CLOSURE: Final[int] = 2  # const-sync: ok -- a discovery floor, not a tunable


def closure(first_party: set[str]) -> list[str]:
    """`ROOT_DISTRIBUTION` plus every first-party distribution it can reach."""
    seen: set[str] = set()
    queue: deque[str] = deque([ROOT_DISTRIBUTION])
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        for requirement in metadata.requires(name) or []:
            dependency = re.split(r"[<>=!~;\[ ]", requirement)[0].strip()
            if dependency in first_party:
                queue.append(dependency)
    return sorted(seen)


def main() -> int:
    declared = os.environ.get("FIRST_PARTY", "").split()
    if not declared:
        print("VIOLATION: FIRST_PARTY is empty; refusing to guess.", file=sys.stderr)
        return 1
    try:
        reachable = closure(set(declared))
    except metadata.PackageNotFoundError as exc:
        print(f"VIOLATION: {ROOT_DISTRIBUTION} is not installed: {exc}", file=sys.stderr)
        return 1
    if len(reachable) < MIN_CLOSURE:
        print(
            f"VIOLATION: closure of {ROOT_DISTRIBUTION} is {len(reachable)}, below "
            f"{MIN_CLOSURE}. A closure that found no requirements would pack metadata "
            "for one distribution and silently reproduce the 0.3.0 provenance defect.",
            file=sys.stderr,
        )
        return 1
    for distribution in reachable:
        print(f"--include-distribution-metadata={distribution}")
    excluded = sorted(set(declared) - set(reachable))
    if excluded:
        print(
            f"note: not in the binary, so no metadata: {', '.join(excluded)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
