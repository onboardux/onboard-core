"""`common.config` -- configuration keys, whatever declares them (`03` §5.10).

Every archetype has configuration and none of them agrees on where it lives, so
this extractor is `common` rather than `web`: a `.env`, a `settings.toml`, a
`values.yaml` and an `appsettings.json` are the same *kind* of referent
(`config_key`) under different namespaces (`02` §3.1).

**It reads keys and never values.** The attribute model carries `key_path`,
`value_type`, `default`, `required` -- and `default` is only recorded when the
value is a literal that could not be a credential (`02` §5.1 rule 4's argument,
one file wider). Anything that looks like a secret is handed to `common.secrets`,
which mints it as a reference with **no field to put the value in**.

**Declared, not grammar.** The evidence is a declaration in a config file, which
`02` §7's `EvidenceMethod` calls `declared` and `03` §3 bands at
`MAP_CONF_DECLARED`. Calling it `grammar` would claim a parse we did not do.
"""

import json
import re
import tomllib
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Final

import yaml
from adopt_map.context import ExtractorContext
from adopt_map.schemas import ExtractorManifest, SourceRef, SurfaceFact

from adopt_const import MAP_CONFIG_VALUE_MAX_CHARS
from adopt_extractors_common.secrets import looks_secret

__all__ = ["MANIFEST", "ConfigExtractor"]

MANIFEST: Final[ExtractorManifest] = ExtractorManifest(
    id="common.config",
    version="1.0.0",
    pack="common",
    archetypes=["web", "ai", "data", "lowcode", "platform"],
    kinds=["config_key"],
    method="declared",
)

#: Filename -> the `02` §3.1 namespace for its keys. Matched on the file's
#: **name**, because the namespace names the *source* of a key rather than the
#: directory somebody filed it in.
_NAMESPACE_BY_NAME: Final[dict[str, str]] = {
    ".env": "env",
    ".env.example": "env",
    "values.yaml": "helm",
    "values.yml": "helm",
    "appsettings.json": "appsettings",
    "settings.py": "django",
    "pyproject.toml": "pyproject",
}

#: Suffix -> namespace, for files the name table does not claim.
_NAMESPACE_BY_SUFFIX: Final[dict[str, str]] = {
    ".env": "env",
    ".toml": "toml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "ini",
}

#: A `KEY=value` line in a dotenv-shaped file.
_DOTENV_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$"
)

#: An uppercase module-level assignment -- Django's settings convention, and the
#: one Python shape that is a configuration key rather than a variable.
_PY_SETTING_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Z][A-Z0-9_]{2,})\s*=", re.MULTILINE)

#: How deep a nested document is flattened into dotted key paths. Bounded because
#: a deeply nested `values.yaml` would otherwise mint an identity per leaf of a
#: structure nobody configures directly.
_MAX_DEPTH: Final[int] = 6


#: Markers that identify a document as an **API contract** rather than a
#: configuration source. `02` §3.1's `config_key` namespace names *where a key
#: comes from* -- `django`, `env`, `helm`, `appsettings` -- and a published
#: contract is not one of those: it is `web.openapi`'s or `web.graphql`'s subject,
#: and minting a `config_key` per leaf of it would put
#: `paths./api/v1/orders/.get.summary` in the identity set beside the endpoint it
#: describes. Two extractors claiming one file is how an identity set fills with
#: things nobody addresses.
#:
#: Added with YAML support (B1-CR-67): before it, no YAML file was read at all,
#: so the overlap could not arise and nothing revealed it.
_CONTRACT_MARKERS: Final[tuple[str, ...]] = (
    "openapi:",
    '"openapi"',
    "swagger:",
    '"swagger"',
    "asyncapi:",
    '"asyncapi"',
    "$schema",
)


def _namespace(path: str) -> str | None:
    name = PurePosixPath(path).name
    if name in _NAMESPACE_BY_NAME:
        return _NAMESPACE_BY_NAME[name]
    if name.startswith(".env"):
        return "env"
    return _NAMESPACE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


def _is_contract(text: str) -> bool:
    """Whether a document declares itself an API contract."""
    head = text[:4096].lower()
    return any(marker in head for marker in _CONTRACT_MARKERS)


def _flatten(payload: object, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, object]]:
    if depth >= _MAX_DEPTH:
        return
    if isinstance(payload, dict):
        for key, value in sorted(payload.items(), key=lambda item: str(item[0])):
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                yield from _flatten(value, path, depth + 1)
            else:
                yield path, value
    elif prefix:
        yield prefix, payload


def _value_type(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "list"
    return "str"


def _safe_default(key: str, value: object) -> str | None:
    """A default worth recording, or `None` when it might be a credential.

    The asymmetry is deliberate and it is the same one `01` §1.6 draws: a missing
    default costs a reader one lookup, and a recorded secret is a breach. So
    anything whose *key* looks secret-shaped records nothing, and so does any
    string long enough to be a token.
    """
    if looks_secret(key):
        return None
    if isinstance(value, str) and len(value) > MAP_CONFIG_VALUE_MAX_CHARS:
        return None
    return str(value) if value is not None else None


class ConfigExtractor:
    """Satisfies `adopt_map.schemas.Extractor` structurally."""

    def manifest(self) -> ExtractorManifest:
        return MANIFEST

    def applies_to(self, root: str) -> bool:
        """Always applicable: every archetype has configuration somewhere.

        The tree is not read here. `applies_to` runs during planning, before the
        index exists, and a second walk to answer a question the index will
        answer anyway would breach `03` §5.8's one-walk rule.
        """
        del root
        return True

    def extract(self, ctx: ExtractorContext) -> Iterator[SurfaceFact]:
        """One `config_key` per declared key, in file then key order."""
        for entry in ctx.files():
            ctx.budget.check()
            namespace = _namespace(entry.path)
            if namespace is None:
                continue
            text = ctx.text(entry)
            if _is_contract(text):
                continue
            for key, value in _keys(entry.path, text):
                if looks_secret(key):
                    # A secret *reference* is `common.secrets`' to mint: it takes
                    # the `secret:*` namespace whose model has no value field.
                    continue
                yield SurfaceFact(
                    identity_kind="config_key",
                    namespace=namespace,
                    local_key=key,
                    title=key,
                    attributes={
                        "key_path": key,
                        "value_type": _value_type(value),
                        "default": _safe_default(key, value),
                    },
                    source_refs=[SourceRef(path=entry.path, blob_sha=entry.blob_sha)],
                    # A `.env` is a file, but the *setting* it declares is
                    # resolved from the process environment at run time and can
                    # change without a commit -- `01` F8.6's outside-VCS case.
                    outside_vcs=namespace == "env",
                )


def _keys(path: str, text: str) -> Iterator[tuple[str, object]]:
    """`(key path, value)` pairs from one configuration file.

    Parsers are the standard library's and are **data parsers only** -- `tomllib`
    and `json` evaluate nothing. A dotenv and a Python settings module are read by
    regex over text, never imported: `02` §7 obligation 1 admits no exception for
    a file that looks like ours.
    """
    suffix = PurePosixPath(path).suffix.lower()
    name = PurePosixPath(path).name
    if suffix in {".yaml", ".yml"}:
        # **S1.3 declared two YAML namespaces and shipped no YAML parser**
        # (B1-CR-67). `_NAMESPACE_BY_NAME` maps `values.yaml` to `helm` and
        # `_NAMESPACE_BY_SUFFIX` maps `.yaml` to `yaml`, and this function had no
        # branch for either -- so every YAML configuration file in every client
        # tree yielded zero keys, silently. Found by scoring a run against a
        # labeled set that expected sixteen Helm values.
        #
        # `safe_load` constructs no Python objects, which is the only loader
        # `02` §7 obligation 1 permits over a client file.
        try:
            yield from _flatten(yaml.safe_load(text))
        except yaml.YAMLError:
            return
    elif suffix == ".toml":
        try:
            yield from _flatten(tomllib.loads(text))
        except tomllib.TOMLDecodeError:
            return
    elif suffix == ".json":
        try:
            yield from _flatten(json.loads(text))
        except json.JSONDecodeError:
            return
    elif name.startswith(".env") or suffix == ".env":
        for line in text.splitlines():
            match = _DOTENV_RE.match(line)
            if match:
                yield match.group(1), match.group(2).strip().strip("\"'")
    elif name == "settings.py":
        for match in _PY_SETTING_RE.finditer(text):
            yield match.group(1), None
