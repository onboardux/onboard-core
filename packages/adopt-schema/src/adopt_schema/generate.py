"""Generate every target from the manifest, or prove none has drifted.

Four targets, one source. `--check` regenerates into memory and compares bytes;
a difference is `SCHEMA_GENERATED_DRIFT`, which is the only way a hand-edited
realization gets caught before it becomes the thing people trust.

The SQLite and Postgres targets are written **directly** to their initial
migration files. There is deliberately no separate "current DDL" artifact that a
migration is copied from: two files holding one schema diverge the first time
someone edits the convenient one.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Final

from adopt_obs import AdoptError, ErrorCode
from adopt_schema.emitters import jsonschema, postgres, pymodel, sqlite
from adopt_schema.manifest import Manifest, load_manifest

__all__ = ["OUT_ROOT_ENV", "TARGETS", "generate", "render_all", "repo_root"]

OUT_ROOT_ENV: Final[str] = "ADOPT_SCHEMA_OUT_ROOT"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[4]

MODEL_PACKAGE: Final[str] = "packages/adopt-model/src/adopt_model"
INITIAL_MIGRATION: Final[str] = "0001__init_v3.sql"

TARGETS: Final[tuple[str, ...]] = ("sqlite", "postgres", "jsonschema", "pymodel")


def repo_root() -> Path:
    """The checkout to generate *into* -- deliberately not `assets.assets_root`.

    Every other `schema/` lookup moved to `adopt_schema.assets` at CR-53, which
    prefers the read-only copy bundled inside the wheel. This one must not: it is
    a writer, and `adopt-schema generate` rewriting files inside an installed
    package would silently edit an artefact instead of the source of truth. A
    developer tool that cannot find a checkout should fail as a missing path,
    which is what the caller reports.
    """
    override = os.environ.get(OUT_ROOT_ENV)
    return Path(override) if override else _REPO_ROOT


def _render_sqlite(manifest: Manifest) -> dict[str, str]:
    return {f"schema/migrations/sqlite/{INITIAL_MIGRATION}": sqlite.emit(manifest)}


def _render_postgres(manifest: Manifest) -> dict[str, str]:
    return {f"schema/migrations/postgres/{INITIAL_MIGRATION}": postgres.emit(manifest)}


def _render_jsonschema(manifest: Manifest) -> dict[str, str]:
    return {"schema/export.schema.json": jsonschema.emit(manifest)}


def _render_pymodel(manifest: Manifest) -> dict[str, str]:
    return {
        f"{MODEL_PACKAGE}/_enums.py": pymodel.emit_enums(manifest),
        f"{MODEL_PACKAGE}/_tables.py": pymodel.emit_tables(manifest),
        f"{MODEL_PACKAGE}/__init__.py": pymodel.emit_package(manifest),
    }


_RENDERERS: Final[dict[str, Callable[[Manifest], dict[str, str]]]] = {
    "sqlite": _render_sqlite,
    "postgres": _render_postgres,
    "jsonschema": _render_jsonschema,
    "pymodel": _render_pymodel,
}


def render_all(manifest: Manifest, targets: tuple[str, ...] = TARGETS) -> dict[str, str]:
    """Every generated file as `{repo-relative path: content}`, in target order."""
    rendered: dict[str, str] = {}
    for target in targets:
        renderer = _RENDERERS.get(target)
        if renderer is None:
            raise AdoptError(
                ErrorCode.MANIFEST_INVALID,
                message=f"unknown generation target {target!r}",
                hint=f"Targets are: {', '.join(TARGETS)}.",
            )
        rendered.update(renderer(manifest))
    return rendered


def generate(
    *,
    targets: tuple[str, ...] = TARGETS,
    check: bool = False,
    manifest: Manifest | None = None,
) -> list[str]:
    """Write every target, or report which ones differ. Returns the paths touched."""
    loaded = manifest or load_manifest()
    rendered = render_all(loaded, targets)
    root = repo_root()

    if check:
        drifted = [
            path
            for path, content in rendered.items()
            if not (root / path).exists() or (root / path).read_text(encoding="utf-8") != content
        ]
        if drifted:
            raise AdoptError(
                ErrorCode.SCHEMA_GENERATED_DRIFT,
                message="checked-in artifacts differ from a fresh generation: "
                + ", ".join(sorted(drifted)),
                hint="Run `adopt-schema generate`. Never hand-edit a generated file: the "
                "manifest is the only schema authority, and an edit here is reverted by "
                "the next regeneration without anyone noticing it was ever made.",
            )
        return []

    written: list[str] = []
    for path, content in rendered.items():
        target_path = root / path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # Newline pinned so a Windows checkout and a Linux runner generate the
        # same bytes; `generate --check` compares bytes and nothing else.
        with target_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        written.append(path)
    return written
