"""Configuration resolution: flag > environment > project file > user file > default.

Every key reports **where its value came from**, because "it works on my
machine" is almost always a resolution-order question, and answering it by
reading code takes an order of magnitude longer than reading it off
``adopt doctor``.

Secrets are marked in the registry and are reported by **presence and source
only, never by value**. A secret is read once at process start into a typed
object; it never enters a store, a trace, an error message or a log line.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

__all__ = [
    "REGISTRY",
    "ConfigKey",
    "Resolution",
    "Source",
    "load_config_file",
    "project_config_path",
    "resolve_all",
    "user_config_path",
]


class Source(StrEnum):
    """Where a resolved value came from. Order here is resolution order."""

    FLAG = "flag"
    ENV = "env"
    PROJECT_FILE = "project-file"
    USER_FILE = "user-file"
    DEFAULT = "default"


@dataclass(frozen=True)
class ConfigKey:
    name: str
    default: str | None
    description: str
    is_secret: bool = False


@dataclass(frozen=True)
class Resolution:
    key: str
    value: str | None
    source: Source
    is_secret: bool

    def render(self) -> dict[str, str | None]:
        """The `doctor` shape from contracts §14: ``{key, value, source}``.

        A secret renders as presence, never as value. There is no verbosity
        flag that reveals it: a flag that can print a secret will eventually be
        used in CI with logs attached to a ticket.
        """
        shown = ("<set>" if self.value else "<unset>") if self.is_secret else self.value
        return {"key": self.key, "value": shown, "source": str(self.source)}


#: The configuration registry from implementation spec §3.
#:
#: Feature flags are typed accessors and **default off**, without exception.
#: New behaviour arrives behind a flag that is off until it has earned being on.
REGISTRY: Final[tuple[ConfigKey, ...]] = (
    ConfigKey("ADOPT_STORE_PATH", ".adopt/store.db", "Canonical SQLite store location."),
    ConfigKey(
        "ADOPT_RUNTIME_PATH",
        ".adopt/runtime.db",
        "Runtime annex: agent-run idempotency and in-client audit. Never exported.",
    ),
    ConfigKey(
        "ADOPT_OFFLINE",
        "1",
        "Offline is the default posture. Network egress requires an explicit opt-in.",
    ),
    ConfigKey("ADOPT_ADAPTER", None, "Configured model adapter id. No default: none is required."),
    ConfigKey("ADOPT_ADAPTER_ENDPOINT", None, "OpenAI-compatible local endpoint, when configured."),
    ConfigKey(
        "ADOPT_MODEL",
        None,
        "Model identifier. Never hard-coded, including as a default -- a default here is "
        "how a tool aimed at every lab acquires a house lab.",
    ),
    ConfigKey("ADOPT_LOG_LEVEL", "info", "Minimum emitted log level."),
    ConfigKey("ADOPT_LOG_FORMAT", "json", "Log rendering. JSON is the only supported sink format."),
    ConfigKey("ADOPT_SCRATCH_DIR", None, "Scratch directory for bundle and export work."),
    ConfigKey(
        "ADOPT_PROMPTS_DIR",
        "prompts",
        "Immutable prompt versions (AI spec §5). `03` §1.2 places the directory at the "
        "repository root, which is not inside any package -- so its location is "
        "configuration rather than a path a module can compute, and `doctor` reports "
        "where it resolved from.",
    ),
    ConfigKey("ADOPT_FEATURE_AGENT_DISAMBIGUATION", "0", "Archetype disambiguation pass. Off."),
    ConfigKey("ADOPT_FEATURE_DBOS_BACKEND", "0", "DBOS workflow backend. Off."),
    ConfigKey("ADOPT_FEATURE_POSTGRES_STORE", "0", "Postgres store realization. Off."),
    ConfigKey("ADOPT_FEATURE_VECTOR_INDEX", "0", "Vector index behind the VectorIndex seam. Off."),
    ConfigKey("ADOPT_API_KEY", None, "Provider credential, when an adapter is configured.", True),
)


def project_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".adopt" / "config.toml"


def user_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "adopt" / "config.toml"


def load_config_file(path: Path) -> dict[str, str]:
    """Read a config file, tolerating absence but not malformation.

    A missing file is normal. A malformed file is not silently ignored: a
    typo'd config that is quietly skipped produces the exact "it works on my
    machine" failure this module exists to answer.
    """
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {str(k).upper(): str(v) for k, v in data.items() if not isinstance(v, dict)}


def resolve_all(
    *,
    flags: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    project: Mapping[str, str] | None = None,
    user: Mapping[str, str] | None = None,
) -> list[Resolution]:
    """Resolve every registered key, recording the winning source.

    All four layers are injectable so the resolution order can be tested
    without touching the real environment or the real filesystem.
    """
    layers: tuple[tuple[Source, Mapping[str, str]], ...] = (
        (Source.FLAG, flags or {}),
        (Source.ENV, os.environ if env is None else env),
        (Source.PROJECT_FILE, project or {}),
        (Source.USER_FILE, user or {}),
    )

    resolutions: list[Resolution] = []
    for key in REGISTRY:
        value: str | None = key.default
        source = Source.DEFAULT
        for candidate_source, layer in layers:
            found = layer.get(key.name)
            if found:
                value, source = found, candidate_source
                break
        resolutions.append(Resolution(key.name, value, source, key.is_secret))
    return resolutions
