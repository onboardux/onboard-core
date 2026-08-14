"""The four `.adopt/config.toml` sections `adopt map` reads -- strictly.

`05` S1.1 asked for *"strict config parsing; unknown keys under
`[map]`/`[extractors]`/`[emit]`/`[agent]` reject"* and marked the box `[x]`.
**S1.4 found that nothing implemented it.** `adopt_cli.config.load_config_file`
reads the file and then drops every table:

    return {str(k).upper(): str(v) for k, v in data.items() if not isinstance(v, dict)}

`isinstance(v, dict)` is exactly the four sections, so a typo under one of them was
not rejected -- it was **accepted by being ignored**, which is the inverse of the
requirement and the failure mode a config file has to be safe from. A silently
skipped setting is the "it works on my machine" report that
`adopt_cli.config`'s own docstring says the module exists to answer.

That mattered here rather than in the abstract: `01` §9 makes
`extractors.web.enabled` the flag S1.4 flips, and until this module there was no
path from a configuration file to pack enablement at all -- the enabled set was a
frozen constant no operator could reach.

**Why closed Pydantic models rather than a hand-written key walk.** `02` §1.1
already settled the mechanism: *"a field that does not exist cannot reach the
store, an artifact or a log line"*. The same argument applies to a config file one
layer earlier, and it is the difference between rejecting the unknown keys
somebody thought to list and rejecting every unknown key.

**The pack names are read from the manifest, never restated.** `ExtractorManifest.pack`
is a closed `Literal`, and a second copy of its six members is a second thing to
keep in step -- the move `adopt_map.plugins` already makes for `IdentityKind`.
"""

import tomllib
from pathlib import Path
from typing import Final, Literal, get_args

from adopt_map.schemas import ExtractorManifest
from pydantic import BaseModel, ConfigDict, ValidationError

from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "DEFAULT_CONFIG_SECTIONS",
    "PACK_NAMES",
    "AgentSection",
    "EmitSection",
    "ExtractorsSection",
    "MapConfig",
    "MapSection",
    "load_map_config",
]

#: The six pack names, read from `ExtractorManifest.pack`'s `Literal` rather than
#: restated. Adding a pack to the manifest widens this automatically; a pack that
#: exists in one place and not the other cannot happen.
PACK_NAMES: Final[tuple[str, ...]] = tuple(
    sorted(get_args(ExtractorManifest.model_fields["pack"].annotation))
)

#: The four sections `05` S1.1 names. A *fifth* top-level table is rejected too:
#: `store.write.enabled` is a real flag (`01` §9) but it is `--dry-run`'s, and
#: admitting an unlisted section here would reopen the hole this module closes.
DEFAULT_CONFIG_SECTIONS: Final[frozenset[str]] = frozenset({"map", "extractors", "emit", "agent"})

#: `strict=True` as well as `extra="forbid"`, and the strictness is the half that
#: is easy to leave off. Pydantic's lax mode coerces `enabled = "yes"` to `True`,
#: and TOML has a real boolean type -- so a quoted string there is a mistake, and
#: silently accepting it is the same "looks like it worked" failure an unknown key
#: produces. `"no"` would coerce to `False` just as quietly, which is the direction
#: that actually costs somebody a run.
_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Toggle(BaseModel):
    """A `<name>.enabled = <bool>` sub-table.

    TOML renders `web.enabled = true` under `[extractors]` as a nested table, so
    the flag names in `01` §9 -- `extractors.web.enabled`, `map.moves.enabled` --
    are spelled in a file exactly as the PRD spells them, with no translation
    step for a reader to get wrong.
    """

    model_config = _CONFIG

    enabled: bool


class MapSection(BaseModel):
    """`[map]` -- `01` §9's `map.moves.enabled`, default on from S1.2."""

    model_config = _CONFIG

    moves: _Toggle | None = None


class ExtractorsSection(BaseModel):
    """`[extractors]` -- one `<pack>.enabled` toggle per `03` §5.10 pack.

    Every pack is declared explicitly and optional. Declaring them rather than
    accepting an open mapping is the point: `extractors.wbe.enabled = true` is a
    typo that silently disables a pack, and the only reading of it that helps an
    operator is a refusal naming the six valid names.
    """

    model_config = _CONFIG

    common: _Toggle | None = None
    web: _Toggle | None = None
    ai: _Toggle | None = None
    data: _Toggle | None = None
    lowcode: _Toggle | None = None
    platform: _Toggle | None = None

    def enabled_packs(self, *, defaults: frozenset[str]) -> frozenset[str]:
        """The packs this configuration turns on, over the sprint defaults.

        A pack absent from the file keeps its default, which is what makes the
        file a set of *overrides* rather than a redeclaration an operator has to
        keep complete.
        """
        enabled = set(defaults)
        for name in PACK_NAMES:
            toggle = getattr(self, name, None)
            if toggle is None:
                continue
            if toggle.enabled:
                enabled.add(name)
            else:
                enabled.discard(name)
        return frozenset(enabled)


class EmitSection(BaseModel):
    """`[emit]` -- `02` §8's `--format` values, as a default for the flag."""

    model_config = _CONFIG

    formats: list[Literal["md", "json", "mermaid", "d2"]] | None = None


class AgentSection(BaseModel):
    """`[agent]` -- `04` §2 G-1's half that lives in configuration.

    `enabled` defaults to **off** and stays off: `01` F12.1 requires `--agent`
    *and* `agent.enabled`, neither alone. **No adapter default and no model
    default** -- `04` §3's *"No provider is named in Build 1 code or config
    defaults"*.
    """

    model_config = _CONFIG

    enabled: bool = False
    adapter: str | None = None


class MapConfig(BaseModel):
    """The four sections, closed at every level."""

    model_config = _CONFIG

    map: MapSection = MapSection()
    extractors: ExtractorsSection = ExtractorsSection()
    emit: EmitSection = EmitSection()
    agent: AgentSection = AgentSection()


def load_map_config(path: Path) -> MapConfig:
    """Read and strictly validate the four sections of one config file.

    A missing file is normal and yields the defaults. A file that exists is held
    to the closed schema: an unknown section, an unknown key under a known
    section, or a value of the wrong type is `MAP_USAGE` and exit **2**.

    **Scope keys are not read here.** `ADOPT_FIRM_ID` and its three siblings are
    top-level scalars owned by `adopt_cli.config`, and they are deliberately left
    alone: this function validates the four *sections* and ignores every
    top-level scalar, so the two readers cannot fight over one file.

    Args:
        path: A `.adopt/config.toml`. Absence is not an error.

    Returns:
        The validated configuration, frozen.

    Raises:
        AdoptError: ``MAP_USAGE`` on malformed TOML, an unknown section, an
            unknown key, or a mistyped value.
    """
    if not path.exists():
        return MapConfig()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise AdoptError(
            ErrorCode.MAP_USAGE,
            message=f"{path} is not valid TOML: {error}",
            hint="A malformed config file is refused rather than skipped. A skipped file "
            "is the failure that reads as 'the setting had no effect'.",
        ) from error

    sections = {key: value for key, value in data.items() if isinstance(value, dict)}
    unknown = sorted(set(sections) - DEFAULT_CONFIG_SECTIONS)
    if unknown:
        raise AdoptError(
            ErrorCode.MAP_USAGE,
            message=f"{path} declares unknown section(s) {unknown}",
            hint=f"`adopt map` reads {sorted(DEFAULT_CONFIG_SECTIONS)}. A section nobody "
            "reads is a setting that silently has no effect, which is worse than a "
            "refusal because it looks like it worked.",
        )

    try:
        return MapConfig.model_validate(sections)
    except ValidationError as error:
        raise AdoptError(
            ErrorCode.MAP_USAGE,
            message=f"{path} has {error.error_count()} invalid setting(s)",
            hint=f"{error.errors(include_url=False)}. The four sections are closed "
            "(`05` S1.1): every key is declared, so a typo is refused by name rather "
            "than ignored. Valid pack names are "
            f"{list(PACK_NAMES)}.",
        ) from error
