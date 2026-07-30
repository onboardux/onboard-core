"""Shared source-scanning helpers for the custom contracts."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SCANNED_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".sql"})

EXCLUDED_PARTS: Final[frozenset[str]] = frozenset(
    {".venv", "__pycache__", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache", "node_modules"}
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    text: str

    def render(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: {self.text.strip()}"


def iter_source_files(roots: list[str]) -> Iterator[Path]:
    """Every scannable file under the configured roots.

    Test files are deliberately **included**. A planted violation in a test
    fixture is exactly how these gates are proven to work, and the fixtures
    that exercise them live under a dedicated directory each contract excludes
    by name rather than by blanket-skipping all tests.
    """
    for root in roots:
        base = Path(root)
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if EXCLUDED_PARTS & set(path.parts):
                continue
            yield path


def is_under(path: Path, prefixes: list[str]) -> bool:
    """True when ``path`` sits under any of the given directory prefixes."""
    posix = path.as_posix()
    return any(posix.startswith(prefix.rstrip("/") + "/") or posix == prefix for prefix in prefixes)


def as_str_list(value: object) -> list[str]:
    """Read an import-linter ``ListField`` as the list it becomes at runtime.

    The framework declares fields as descriptor objects on the class and
    replaces them with parsed values on the instance, so a type checker sees
    the descriptor type. This converts once, with a real runtime check, rather
    than scattering suppressions through every contract.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected a list of strings from the contract options, got {type(value)}")
    return [str(item) for item in value]
