"""The generic pack -- what every system has, whatever it is built from.

Five extractors: declared dependencies, config keys, environment variables,
scheduled jobs, and CI workflows. v6.1 §6 also names "files-of-interest", which
is deliberately the narrowest of the five here: a curated well-known set, not
"every file". An inventory that lists every file is not an inventory.

**Every extractor in this module is a pure function of the tree.** No writes, no
network, no subprocess, no import of anything found in the tree. Parsing is
manifest-first: `tomllib`, `json` and `yaml` for the formats that have them, and
narrow regular expressions only where a format has no parser worth the
dependency.
"""

import ast
import json
import re
import tomllib
from collections.abc import Iterator
from typing import Final

import yaml

from adopt_map.keys import (
    CI_NAMESPACE,
    ENV_NAMESPACE,
    FILE_NAMESPACE,
    dependency_namespace,
    path_key,
)
from adopt_map.observation import Observation, Span
from adopt_map.tree import SourceTree, TreeFile

__all__ = [
    "CiWorkflowExtractor",
    "ConfigKeyExtractor",
    "DependencyExtractor",
    "EnvVarExtractor",
    "FilesOfInterestExtractor",
    "ScheduledJobExtractor",
    "SettingsClassExtractor",
]


def _whole_file(entry: TreeFile) -> Span:
    """A span for a referent the whole file represents."""
    return Span(path=entry.path, start_line=1, end_line=1)


def _line_of(text: str, index: int) -> int:
    """1-based line number of a character offset."""
    return text.count("\n", 0, index) + 1


class DependencyExtractor:
    """Declared dependencies from the manifests that declare them.

    Declared, never resolved: a lock file records what one machine resolved on
    one day, and treating that as the system's dependency set would make every
    lock refresh a system change.
    """

    name = "generic.dependencies"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_named("pyproject.toml"):
            yield from self._pyproject(tree, entry)
        for entry in tree.iter_named("package.json"):
            yield from self._package_json(tree, entry)
        for entry in tree.iter_named("requirements.txt"):
            yield from self._requirements(tree, entry)

    def _pyproject(self, tree: SourceTree, entry: TreeFile) -> Iterator[Observation]:
        text = tree.text(entry)
        if text is None:
            return
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            # A manifest we cannot parse is not a run we should fail. It is
            # simply not a source of observations, and the file stays in the
            # unmapped count where a reader can see it.
            return
        project = data.get("project")
        if not isinstance(project, dict):
            return
        declared = project.get("dependencies")
        if not isinstance(declared, list):
            return
        for requirement in declared:
            if not isinstance(requirement, str):
                continue
            package = _PEP508_NAME.match(requirement)
            if package is None:
                continue
            name = package.group(0)
            yield Observation(
                kind="metadata_component",
                key=(name,),
                namespace=dependency_namespace("pypi"),
                # The constraint, not the resolved version: the constraint is
                # what the system declares, and it is what changing is a change.
                attributes={"name": name, "requirement": requirement.strip()},
                span=_whole_file(entry),
            )

    def _package_json(self, tree: SourceTree, entry: TreeFile) -> Iterator[Observation]:
        text = tree.text(entry)
        if text is None:
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        for section in ("dependencies", "devDependencies"):
            declared = data.get(section)
            if not isinstance(declared, dict):
                continue
            for name, constraint in sorted(declared.items()):
                yield Observation(
                    kind="metadata_component",
                    key=(str(name),),
                    namespace=dependency_namespace("npm"),
                    attributes={
                        "name": str(name),
                        "requirement": str(constraint),
                        "scope": section,
                    },
                    span=_whole_file(entry),
                )

    def _requirements(self, tree: SourceTree, entry: TreeFile) -> Iterator[Observation]:
        text = tree.text(entry)
        if text is None:
            return
        for offset, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "-")):
                continue
            package = _PEP508_NAME.match(stripped)
            if package is None:
                continue
            name = package.group(0)
            yield Observation(
                kind="metadata_component",
                key=(name,),
                namespace=dependency_namespace("pypi"),
                attributes={"name": name, "requirement": stripped},
                span=Span(path=entry.path, start_line=offset, end_line=offset),
            )


class EnvVarExtractor:
    """Environment variables the code reads, and those a template declares.

    Read sites are the authority. A `.env.example` says what someone documented;
    `os.environ[...]` says what the system will actually fail without.
    """

    name = "generic.env_vars"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        seen: set[str] = set()
        for entry in tree.iter_suffix(".py"):
            text = tree.text(entry)
            if text is None:
                continue
            for match in _ENV_READ.finditer(text):
                variable = match.group("name")
                if variable in seen:
                    continue
                seen.add(variable)
                yield Observation(
                    kind="config_key",
                    key=(variable,),
                    namespace=ENV_NAMESPACE,
                    attributes={"name": variable, "source": "code"},
                    span=Span(
                        path=entry.path,
                        start_line=_line_of(text, match.start()),
                        end_line=_line_of(text, match.end()),
                    ),
                )
        for entry in tree.iter_named(".env.example", ".env.sample", ".env.template"):
            text = tree.text(entry)
            if text is None:
                continue
            for offset, line in enumerate(text.splitlines(), start=1):
                declaration = _ENV_DECLARATION.match(line)
                if declaration is None:
                    continue
                variable = declaration.group("name")
                if variable in seen:
                    continue
                seen.add(variable)
                yield Observation(
                    kind="config_key",
                    key=(variable,),
                    namespace=ENV_NAMESPACE,
                    attributes={"name": variable, "source": "template"},
                    span=Span(path=entry.path, start_line=offset, end_line=offset),
                )


class SettingsClassExtractor:
    """Typed settings classes -- `pydantic-settings`, and the same shape elsewhere.

    A `BaseSettings` subclass *is* an environment-variable declaration: each
    annotated attribute is read from the environment by name. Missing these
    means missing nearly every environment variable in a modern FastAPI or
    Litestar application, because such applications deliberately never call
    `os.environ` -- which is exactly what the first real repository showed.

    Found by the recall floor on reference repository #1: `SECRET_KEY`,
    `POSTGRES_SERVER` and `FIRST_SUPERUSER` were all absent from the map while
    the code plainly required them.
    """

    name = "generic.settings_class"
    version = "1"

    #: Base classes that make a class a settings declaration. Matched on the
    #: attribute or name as written, because `pydantic_settings.BaseSettings`
    #: and a bare imported `BaseSettings` are the same base.
    _SETTINGS_BASES: Final[frozenset[str]] = frozenset({"BaseSettings"})

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_suffix(".py"):
            text = tree.text(entry)
            if text is None:
                continue
            try:
                module = ast.parse(text, filename=entry.path)
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(module):
                if not isinstance(node, ast.ClassDef) or not self._is_settings(node):
                    continue
                yield from self._attributes(entry, node)

    def _is_settings(self, klass: ast.ClassDef) -> bool:
        for base in klass.bases:
            named = (
                base.id
                if isinstance(base, ast.Name)
                else base.attr
                if isinstance(base, ast.Attribute)
                else None
            )
            if named in self._SETTINGS_BASES:
                return True
        return False

    def _attributes(self, entry: TreeFile, klass: ast.ClassDef) -> Iterator[Observation]:
        for statement in klass.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(
                statement.target, ast.Name
            ):
                continue
            name = statement.target.id
            # `model_config = SettingsConfigDict(...)` and friends configure the
            # loader; they are not settings the system reads from the
            # environment, and listing them would put our library's plumbing in
            # the client's inventory.
            if name.startswith("_") or name == "model_config":
                continue
            yield Observation(
                kind="config_key",
                key=(name,),
                namespace=ENV_NAMESPACE,
                attributes={
                    "name": name,
                    "source": "settings_class",
                    "type": ast.unparse(statement.annotation),
                    # A settings attribute with no default is **required**: the
                    # application cannot start without it. That is the single
                    # most useful fact about a config key at handover, so it is
                    # an attribute rather than a note.
                    "required": statement.value is None,
                },
                span=Span(
                    path=entry.path,
                    start_line=statement.lineno,
                    end_line=statement.end_lineno or statement.lineno,
                ),
            )


class ConfigKeyExtractor:
    """Settings declared in configuration files, namespaced by the file.

    The namespace is the file stem, so `database.url` in `app.toml` and the same
    key in `worker.toml` stay two referents. Nesting renders dotted.
    """

    name = "generic.config_keys"
    version = "1"

    #: Manifests owned by other extractors or by the packaging ecosystem. Their
    #: keys are not the system's configuration and listing them would bury the
    #: keys that are.
    _NOT_CONFIG: Final[frozenset[str]] = frozenset(
        {"pyproject.toml", "package.json", "package-lock.json", "tsconfig.json", "uv.lock"}
    )

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.files:
            if entry.name in self._NOT_CONFIG or entry.suffix not in {".toml", ".json"}:
                continue
            text = tree.text(entry)
            if text is None:
                continue
            try:
                data = tomllib.loads(text) if entry.suffix == ".toml" else json.loads(text)
            except (tomllib.TOMLDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            namespace = entry.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for dotted, value in sorted(_flatten(data)):
                yield Observation(
                    kind="config_key",
                    key=(dotted,),
                    namespace=namespace,
                    # Key, type and default -- v6.1 §6's named attribute set for
                    # a config. The *value* is the declared default and is part
                    # of what the config is; the type is carried separately so a
                    # `1` becoming `"1"` reads as the change it is.
                    attributes={
                        "key": dotted,
                        "type": type(value).__name__,
                        "default": value,
                    },
                    span=_whole_file(entry),
                )


class ScheduledJobExtractor:
    """Scheduled work: crontab entries and cron-like schedule declarations."""

    name = "generic.jobs"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.iter_named("crontab", "Crontab", ".crontab"):
            text = tree.text(entry)
            if text is None:
                continue
            for offset, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                match = _CRON_LINE.match(stripped)
                if match is None:
                    continue
                schedule = match.group("schedule").strip()
                command = match.group("command").strip()
                yield Observation(
                    kind="job",
                    key=(command,),
                    namespace="cron",
                    # Schedule + target, v6.1 §6's attribute set for a job.
                    attributes={"schedule": schedule, "target": command},
                    span=Span(path=entry.path, start_line=offset, end_line=offset),
                )


class CiWorkflowExtractor:
    """CI workflows, and the jobs inside them.

    A workflow is a scheduled/triggered job in every sense that matters to an
    FDE: it is how the system gets built, tested and deployed, and "what runs on
    merge" is one of the first questions a handover has to answer.
    """

    name = "generic.ci"
    version = "1"

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.files:
            if not entry.path.startswith(".github/workflows/"):
                continue
            if entry.suffix not in {".yml", ".yaml"}:
                continue
            text = tree.text(entry)
            if text is None:
                continue
            try:
                document = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            if not isinstance(document, dict):
                continue
            workflow = str(document.get("name") or entry.name)
            jobs = document.get("jobs")
            job_names = sorted(str(name) for name in jobs) if isinstance(jobs, dict) else []
            # `on:` is YAML 1.1's boolean `True` when unquoted -- a real and
            # frequently-hit trap, and reading only the string key would report
            # every workflow as having no triggers.
            triggers = document.get("on", document.get(True))
            yield Observation(
                kind="job",
                key=(workflow,),
                namespace=CI_NAMESPACE,
                attributes={
                    "name": workflow,
                    "jobs": job_names,
                    "triggers": _trigger_names(triggers),
                },
                span=_whole_file(entry),
            )


class FilesOfInterestExtractor:
    """A curated set of well-known files, never "every file".

    These are the files a person opens first on an unfamiliar repository. The
    list is deliberately short: its value is that everything on it is worth
    someone's attention, and that property is lost the moment it grows to
    include whatever happened to be present.
    """

    name = "generic.files_of_interest"
    version = "1"

    _WELL_KNOWN: Final[frozenset[str]] = frozenset(
        {
            "README.md",
            "README.rst",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
            "Makefile",
            "Procfile",
            ".env.example",
        }
    )

    def extract(self, tree: SourceTree) -> Iterator[Observation]:
        for entry in tree.files:
            if entry.name not in self._WELL_KNOWN:
                continue
            yield Observation(
                kind="metadata_component",
                key=path_key(entry.path),
                namespace=FILE_NAMESPACE,
                # Deliberately **not** the file's content or its hash: this
                # identity says "this repository has a README", and a reworded
                # README is not a change to that fact. Content-bearing knowledge
                # is Build 2's `adopt ingest`, not Build 1's inventory.
                attributes={"name": entry.name, "path": entry.path},
                span=_whole_file(entry),
            )


def _flatten(data: dict[str, object], prefix: str = "") -> Iterator[tuple[str, object]]:
    """Leaf keys of a nested mapping, rendered dotted."""
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _flatten(value, f"{dotted}.")
        else:
            yield dotted, value


def _trigger_names(triggers: object) -> list[str]:
    if isinstance(triggers, str):
        return [triggers]
    if isinstance(triggers, list):
        return sorted(str(trigger) for trigger in triggers)
    if isinstance(triggers, dict):
        return sorted(str(trigger) for trigger in triggers)
    return []


#: PEP 508 distribution name at the start of a requirement string.
_PEP508_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")`, and the
#: `environ` forms of each. Narrow on purpose: a broader pattern matches the
#: word "environ" in prose and mints identities for documentation.
_ENV_READ: Final[re.Pattern[str]] = re.compile(
    r"""(?:os\.)?(?:environ\s*(?:\.get\s*)?\(\s*|environ\s*\[\s*|getenv\s*\(\s*)"""
    r"""["'](?P<name>[A-Z][A-Z0-9_]*)["']""",
)

#: `NAME=value` in a dotenv template, with an optional `export`.
_ENV_DECLARATION: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:export\s+)?(?P<name>[A-Z][A-Z0-9_]*)\s*="
)

#: Five schedule fields (or an `@yearly`-style nickname) then the command.
_CRON_LINE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<schedule>(?:@\w+)|(?:\S+\s+){4}\S+)\s+(?P<command>.+)$"
)
