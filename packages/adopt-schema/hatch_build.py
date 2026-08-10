"""Ship `schema/` inside the wheel, from wherever the build can see it.

**The artefacts this repository builds could not create a store**, and the
release dry run is what found it. `adopt_store`, `adopt_schema.manifest` and
`adopt_store.annex` each located `schema/` by walking `parents[N]` from
`__file__` to the checkout root -- a convention the annex module's own comment
names as shared. It resolves correctly in a source checkout and in the editable
install every test runs against, and to an arbitrary directory from an installed
wheel: for a virtualenv it lands on the environment root, which holds no
`schema/` at all.

Nothing failed loudly there. `_ordered_files` globs a directory that does not
exist, finds nothing, and reports zero pending migrations -- so `adopt store
migrate` created an empty database and failed later with `no such table: firm`,
naming a symptom four layers below the cause. That is this build's recurring
failure once more: **a measurement that succeeds by having nothing to measure.**

So the assets travel with the package that owns them. This hook is the half that
cannot be expressed statically, because `uv build` builds the sdist first and the
wheel *from that sdist*, and the two see `schema/` at different paths:

* **From the checkout** -- `packages/adopt-schema/../../schema` exists, and this
  hook force-includes it at `adopt_schema/_assets/schema`.
* **From the sdist** -- the static `sdist.force-include` in `pyproject.toml` has
  already placed it at `src/adopt_schema/_assets/schema`, where the wheel's
  `packages = ["src/adopt_schema"]` collects it as ordinary package data. The
  checkout path does not exist, so this hook correctly adds nothing.

A static `wheel.force-include` cannot do this: pointing it at the checkout makes
every wheel-from-sdist build fail with `Forced include not found`, which is how
this hook came to exist rather than a fourth line of TOML.

**There is still exactly one authored copy.** `schema/` at the repository root
remains the only place a human edits, `adopt-schema generate` still writes there,
and `generate --check` still judges it. The copy inside the wheel is made at
build time and never committed, so the two cannot drift -- which is why this is
a build hook rather than a fifth generated target checked into the tree.
"""

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

#: Mirrors the repository layout exactly, so a resolved assets root and a
#: checkout root are interchangeable and every `root / "schema" / ...` call site
#: reads the same in both. `adopt_schema.assets` is the reader.
_TARGET = "adopt_schema/_assets/schema"


class CustomBuildHook(BuildHookInterface):
    """Force-include the checkout's `schema/` when the checkout is visible."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        # `self.root` is `packages/adopt-schema`; two up is the repository root.
        # From an unpacked sdist there is no such directory, and that is the
        # signal that the assets are already inside `src/` -- not an error.
        checkout = Path(self.root).parents[1] / "schema"
        if checkout.is_dir():
            build_data["force_include"][str(checkout)] = _TARGET
