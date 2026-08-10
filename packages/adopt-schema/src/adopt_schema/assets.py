"""Where `schema/` is, for a reader — checkout, wheel or packed binary.

**Three modules resolved this independently and all three were wrong off a
checkout** (CR-53). `adopt_schema.manifest`, `adopt_store.api` and
`adopt_store.annex.sqlite_annex` each walked `parents[4]` or `parents[5]` from
`__file__` to the repository root, and the annex module's comment named it as
the shared convention. From an installed wheel those walks land on the
environment root — for the venv the release dry run built, `…/AppData/Local/Temp`
— which holds no `schema/` at all.

**Nothing said so.** `_ordered_files` globs `*.sql` in a directory that does not
exist, finds nothing, and reports zero migrations pending; `open_store` then
created an empty database and the first query failed with `no such table: firm`.
Four layers below the cause, and phrased as if the *store* were at fault. That
is this build's recurring failure again — a measurement that succeeds by having
nothing to measure — and here it had reached the shipped artefact.

So resolution happens once, in the package that owns the manifest, and **the
"not found" branch raises**. An unreadable schema is not zero migrations.

The order is deliberate:

1. **`ADOPT_SCHEMA_ASSETS_ROOT`** — an operator override, and how a test points
   at a fixture tree without touching the process's own layout.
2. **The bundled copy**, `adopt_schema/_assets/`. Its presence is proof we are
   running from a built artefact, because it is created at build time by
   `hatch_build.py` and never committed. Checked first for that reason: in an
   artefact it is the only correct answer, and a `parents[4]` walk from
   `site-packages` could in principle strike some unrelated directory that
   happens to contain a `schema/`.
3. **The checkout**, four parents up. Correct for a source tree and for the
   editable install every test runs against — which is exactly why the bug
   survived to the release job with a green suite behind it.

`generate` deliberately does **not** use this. It is a writer: `adopt-schema
generate` rewrites the four targets in the checkout, so resolving to a bundled
read-only copy inside a wheel would be wrong in a way no error could report.
"""

import os
from pathlib import Path
from typing import Final

from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "ASSETS_ROOT_ENV",
    "SCHEMA_DIRNAME",
    "assets_root",
    "checkout_root",
    "schema_dir",
]

#: Points at a directory *containing* `schema/`, not at `schema/` itself, so an
#: override and a checkout root are the same kind of thing.
ASSETS_ROOT_ENV: Final[str] = "ADOPT_SCHEMA_ASSETS_ROOT"

SCHEMA_DIRNAME: Final[str] = "schema"

#: Written into the wheel by `hatch_build.py`; absent from every checkout.
_BUNDLED: Final[Path] = Path(__file__).resolve().parent / "_assets"

#: `packages/adopt-schema/src/adopt_schema/<module>.py` -> four parents up.
_CHECKOUT_DEPTH: Final[int] = 4  # const-sync: ok -- a path depth, not a tunable.


def checkout_root(module_file: Path) -> Path | None:
    """The checkout `module_file` sits in, or `None` when there is no such depth.

    **`parents[4]` raises `IndexError` on a path with fewer than five parents,
    and a packed binary is exactly that** (CR-55). Nuitka's `--onefile` unpacks
    to `/tmp/onefile_<pid>_<n>/`, so this module runs as
    `/tmp/onefile_x/adopt_schema/assets.py` -- three parents, not five -- and the
    walk crashed the binary at import before any of this module's careful "not
    found" handling could run.

    So the answer is `None`, not an exception: *"there is no checkout here"* is
    an ordinary, expected state for an installed artefact, and `assets_root`
    already knows what to do with it. Raising from a module-scope constant turns
    a missing directory into an unimportable package.

    Shared with `generate`, which carried the same eager `parents[4]` and would
    have failed identically the moment anything in a binary imported it.
    """
    parents = module_file.resolve().parents
    if len(parents) <= _CHECKOUT_DEPTH:
        return None
    return parents[_CHECKOUT_DEPTH]


_CHECKOUT: Final[Path | None] = checkout_root(Path(__file__))


def _holds_schema(root: Path) -> bool:
    return (root / SCHEMA_DIRNAME).is_dir()


def assets_root() -> Path:
    """The directory holding `schema/`. Raises rather than guessing.

    Returns a root interchangeable with the repository root, so every existing
    `root / "schema" / …` call site reads identically in a checkout and in a
    wheel.
    """
    override = os.environ.get(ASSETS_ROOT_ENV)
    if override:
        root = Path(override)
        if _holds_schema(root):
            return root
        raise AdoptError(
            ErrorCode.SCHEMA_ASSETS_MISSING,
            message=f"{ASSETS_ROOT_ENV}={override} holds no {SCHEMA_DIRNAME}/ directory",
            hint=f"Point {ASSETS_ROOT_ENV} at the directory that *contains* "
            f"{SCHEMA_DIRNAME}/, not at {SCHEMA_DIRNAME}/ itself, or unset it to "
            "use the copy inside the installed package.",
        )

    # `_CHECKOUT` is `None` inside a packed binary, where there is no checkout at
    # this depth to look in. That is an ordinary state, not an error -- the
    # bundled copy is the answer there.
    for candidate in (_BUNDLED, _CHECKOUT):
        if candidate is not None and _holds_schema(candidate):
            return candidate

    above = _CHECKOUT if _CHECKOUT is not None else "no checkout at this depth"
    raise AdoptError(
        ErrorCode.SCHEMA_ASSETS_MISSING,
        message=(
            f"no {SCHEMA_DIRNAME}/ directory beside the installed package "
            f"({_BUNDLED}) or in a checkout above it ({above})"
        ),
        hint="This build of adopt is incomplete -- the schema assets that ship "
        "inside the wheel are absent, so no store can be created or migrated. "
        "Reinstall from a released artefact, or run from a source checkout. If "
        "you built this yourself, the packer dropped the package data: see "
        f"`hatch_build.py`. {ASSETS_ROOT_ENV} overrides the search.",
    )


def schema_dir() -> Path:
    """`schema/` itself, for the callers that want it directly."""
    return assets_root() / SCHEMA_DIRNAME
