"""Generation is a pure function of the manifest.

*Fails when* an emitter acquires an unordered or ambient dependency -- set
iteration, dictionary ordering, a timestamp, a path, an environment variable.
*Matters because* `generate --check` is a byte comparison: an emitter that is
even slightly non-deterministic turns the drift gate into a coin toss, and N15
promises a client auditor that codegen reproduces across runs and machines.
*No other instrument catches it because* a single run always agrees with itself,
so the defect is invisible until two machines disagree in CI.
"""

import pytest

from adopt_schema.generate import render_all
from adopt_schema.manifest import Manifest, load_manifest


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return load_manifest()


@pytest.mark.property
def test_two_generations_are_byte_identical(manifest: Manifest) -> None:
    assert render_all(manifest) == render_all(manifest)


@pytest.mark.property
def test_generation_does_not_depend_on_manifest_object_identity(manifest: Manifest) -> None:
    """A second load of the same file must produce the same bytes.

    Separates "the emitter is deterministic given one object" from "the loader
    is deterministic given one file" -- a dictionary rebuilt in a different
    insertion order would pass the first and fail this.
    """
    assert render_all(manifest) == render_all(load_manifest())


@pytest.mark.property
def test_generated_output_contains_no_absolute_path_or_timestamp(manifest: Manifest) -> None:
    """Provenance without a date. A generated header carrying the time of
    generation makes every run differ from the last for no information gain."""
    for path, content in render_all(manifest).items():
        assert "/home/" not in content, path
        assert "C:\\" not in content, path
        assert "generated at" not in content.lower(), path
