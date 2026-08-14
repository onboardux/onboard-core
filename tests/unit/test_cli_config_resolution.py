"""Configuration resolves flag > env > project file > user file > default, and
says where each value came from.

This is the one piece of T2 logic in an otherwise T4 package, and it gets the
package's only dedicated test. The CLI's dispatch, rendering and option plumbing
are swept transitively by the E2E journeys and by mypy; resolution order is not,
because getting it wrong produces a *working* program that reads the wrong
value.

Defect sentence: *fails when* a lower-precedence layer wins, or when a key
reports a source it did not come from. *Matters because* the source report is
the answer to "why is it using that value", and a wrong answer sends the reader
looking in the wrong file. *No other instrument catches it because* every layer
holds strings and the type checker sees them all as equivalent.
"""

from pathlib import Path
from typing import get_args

import pytest

from adopt_cli.commands import doctor as doctor_command
from adopt_cli.commands import version as version_command
from adopt_cli.config import REGISTRY, Source, load_config_file, resolve_all
from adopt_obs import AdoptError, ErrorCode

KEY = "ADOPT_LOG_LEVEL"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("layers", "expected_value", "expected_source", "why"),
    [
        ({}, "info", Source.DEFAULT, "nothing set anywhere"),
        ({"user": "u"}, "u", Source.USER_FILE, "user file beats default"),
        ({"project": "p", "user": "u"}, "p", Source.PROJECT_FILE, "project beats user"),
        ({"env": "e", "project": "p", "user": "u"}, "e", Source.ENV, "env beats both files"),
        (
            {"flags": "f", "env": "e", "project": "p", "user": "u"},
            "f",
            Source.FLAG,
            "an explicit flag beats everything",
        ),
        ({"env": "", "user": "u"}, "u", Source.USER_FILE, "an empty value does not win"),
    ],
)
def test_resolution_order(
    layers: dict[str, str], expected_value: str, expected_source: Source, why: str
) -> None:
    resolutions = resolve_all(
        flags={KEY: layers["flags"]} if "flags" in layers else {},
        env={KEY: layers["env"]} if "env" in layers else {},
        project={KEY: layers["project"]} if "project" in layers else {},
        user={KEY: layers["user"]} if "user" in layers else {},
    )
    resolved = next(r for r in resolutions if r.key == KEY)

    assert resolved.value == expected_value, why
    assert resolved.source is expected_source, why


@pytest.mark.unit
def test_every_registered_key_is_reported_with_a_source() -> None:
    """`doctor` promises provenance for *every* key, not for the ones that are set."""
    resolutions = resolve_all(flags={}, env={}, project={}, user={})

    assert {r.key for r in resolutions} == {k.name for k in REGISTRY}
    assert all(r.source is not None for r in resolutions)
    assert all(set(r.render()) == {"key", "value", "source"} for r in resolutions)


@pytest.mark.unit
def test_a_secret_is_reported_by_presence_and_source_never_by_value() -> None:
    secret_key = next(k.name for k in REGISTRY if k.is_secret)

    resolutions = resolve_all(env={secret_key: "PLANTED-NOT-A-REAL-CREDENTIAL"})
    rendered = next(r.render() for r in resolutions if r.key == secret_key)

    assert rendered["value"] == "<set>"
    assert rendered["source"] == str(Source.ENV)
    assert "PLANTED" not in repr(rendered)


@pytest.mark.unit
def test_every_feature_flag_defaults_off() -> None:
    """New behaviour is off until it has earned being on. No exceptions."""
    flags = [k for k in REGISTRY if k.name.startswith("ADOPT_FEATURE_")]

    assert flags, "the feature-flag registry is empty; the flags were lost"
    assert all(k.default == "0" for k in flags), [k.name for k in flags if k.default != "0"]


@pytest.mark.unit
def test_no_model_identifier_is_hard_coded_as_a_default() -> None:
    """A default model is how a tool aimed at every lab acquires a house lab."""
    model = next(k for k in REGISTRY if k.name == "ADOPT_MODEL")

    assert model.default is None


@pytest.mark.unit
def test_a_malformed_config_file_is_not_silently_ignored(tmp_path: Path) -> None:
    """Silently skipping it produces the exact 'works on my machine' failure
    that the source report exists to answer."""
    bad = tmp_path / "config.toml"
    bad.write_text("this is not = valid = toml", encoding="utf-8")

    with pytest.raises(Exception, match=r"(?i)toml|expected|invalid"):
        load_config_file(bad)


@pytest.mark.unit
def test_doctor_reports_a_finding_when_an_adapter_has_no_model() -> None:
    payload, findings = doctor_command.build_payload(env={"ADOPT_ADAPTER": "local_openai"})

    assert findings, "an adapter with no model will fail at construction; say so"
    assert payload["findings"] == findings
    assert any(f["key"] == "ADOPT_MODEL" for f in findings)


@pytest.mark.unit
def test_version_build_facts_are_immutable_artifact_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fabricating a build id would break the one thing the field is for:
    tying a binary in the field back to the artifact that was signed.

    Runtime environment variables were the original defect: the release job set
    them while compiling, but an installed wheel or later binary process did not
    inherit them and therefore reported null. Only bytes stamped into the
    artifact may supply these fields.
    """
    monkeypatch.setenv("ADOPT_BUILD_SBOM_SHA256", "runtime-must-not-win")
    monkeypatch.setenv("ADOPT_BUILD_ID", "runtime-must-not-win")

    payload = version_command.build_payload()

    assert payload["sbom_sha256"] is None
    assert payload["build_id"] is None
    assert payload["schema_version"] == 3
    assert payload["export_version"] == 3

    monkeypatch.setattr(version_command, "SBOM_SHA256", "a" * 64)
    build_id = f"github:owner/repo:1:1:{'b' * 40}"
    monkeypatch.setattr(version_command, "BUILD_ID", build_id)

    stamped = version_command.build_payload()

    assert stamped["sbom_sha256"] == "a" * 64
    assert stamped["build_id"] == build_id


# -- Build 1's scope ids (`02` §2 rule 1) ------------------------------------
#
# S1.1 implemented the CLI-override half of rule 1 and its own error hint
# promised the other half, so `adopt map` with no flags exited 2 while telling
# the operator to use a config file nothing read. `05` S1.2's fourth validation
# line -- `uv run adopt map && uv run adopt map --json` -- is what exposed it:
# the line is not executable against a flags-only command.


@pytest.mark.parametrize(
    "key",
    ["ADOPT_FIRM_ID", "ADOPT_ENGAGEMENT_ID", "ADOPT_SYSTEM_ID", "ADOPT_ENVIRONMENT_ID"],
)
def test_a_scope_id_resolves_from_the_project_file(key: str) -> None:
    """*Fails when* `adopt map`'s scope ids are flag-only again.

    *Matters because* `02` §2 rule 1 makes the config file the first layer, and
    every documented invocation of `adopt map` in the pack omits the flags. *No
    other instrument catches it because* the flag path works perfectly, so every
    test that passes flags -- which is all of them -- stays green.
    """
    resolved = {r.key: r for r in resolve_all(project={key: "value-from-file"})}
    assert resolved[key].value == "value-from-file"
    assert resolved[key].source is Source.PROJECT_FILE


@pytest.mark.parametrize(
    "key",
    ["ADOPT_FIRM_ID", "ADOPT_ENGAGEMENT_ID", "ADOPT_SYSTEM_ID", "ADOPT_ENVIRONMENT_ID"],
)
def test_no_scope_id_carries_a_default(key: str) -> None:
    """*Fails when* any scope id acquires a default.

    *Matters because* `02` §2 rule 3 and PRD F1.4 are that there is **no default
    environment** and no guessed scope: a run that cannot name its scope aborts
    with the command to run. A default here would be the "default to production"
    the mandatory environment segment exists to prevent, and it would resolve to
    a row belonging to somebody else. *No other instrument catches it because* a
    defaulted id makes the command succeed, which reads as working.
    """
    resolved = {r.key: r for r in resolve_all(env={})}
    assert resolved[key].value is None
    assert resolved[key].source is Source.DEFAULT


# --------------------------------------------------------------------------- #
# The four `.adopt/config.toml` sections -- `05` S1.1's checkbox, built in S1.4.
#
# S1.1 marked *"strict config parsing; unknown keys under
# `[map]`/`[extractors]`/`[emit]`/`[agent]` reject"* complete with nothing behind
# it: `load_config_file` drops every table (`if not isinstance(v, dict)`), so an
# unknown key under one of those sections was accepted by being ignored. These
# cases are the evidence the checkbox never had.
# --------------------------------------------------------------------------- #


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_absent_config_file_yields_the_defaults(tmp_path: Path) -> None:
    """A missing file is normal; only a malformed one is an error."""
    from adopt_cli.map_config import load_map_config

    configuration = load_map_config(tmp_path / "nothing.toml")
    assert configuration.agent.enabled is False
    assert configuration.extractors.web is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("[nonsense]\nx = 1\n", "an unknown section"),
        ("[extractors]\nwbe.enabled = true\n", "a typo'd pack name"),
        ("[extractors]\nweb.enable = true\n", "a typo'd key under a valid pack"),
        ("[agent]\nenabled = 'yes'\n", "a value of the wrong type"),
        ("[emit]\nformats = ['pdf']\n", "a format outside `02` §8's closed set"),
        ("[map]\nmoves.enabled = true\nmoves.extra = 1\n", "an extra key on a toggle"),
    ],
    ids=[
        "unknown-section",
        "typo-pack",
        "typo-key",
        "wrong-type",
        "bad-format",
        "extra-toggle-key",
    ],
)
def test_a_bad_setting_is_refused_by_name(tmp_path: Path, text: str, reason: str) -> None:
    """*Defect sentence.* Fails when the four sections stop being closed; matters
    because a setting nobody reads has no effect and *looks like it worked* --
    the "it works on my machine" report this module exists to answer, and the
    exact state `05` S1.1's checkbox claimed to prevent; no other instrument
    catches it because an ignored key raises nothing and changes nothing."""
    from adopt_cli.map_config import load_map_config

    with pytest.raises(AdoptError) as caught:
        load_map_config(_config(tmp_path, text))
    assert caught.value.code is ErrorCode.MAP_USAGE, reason


def test_a_pack_toggle_overrides_the_sprint_default_in_both_directions(tmp_path: Path) -> None:
    """`01` §9's flag table, as an operator reaches it.

    Both directions, because a configuration that can only turn packs *on* leaves
    an operator no way to disable a pack that is misbehaving on their tree --
    which is `03` §9's first rollback surface.
    """
    from adopt_map.plugins import DEFAULT_ENABLED_PACKS

    from adopt_cli.map_config import load_map_config

    configuration = load_map_config(
        _config(tmp_path, "[extractors]\nai.enabled = true\nweb.enabled = false\n")
    )
    enabled = configuration.extractors.enabled_packs(defaults=DEFAULT_ENABLED_PACKS)
    assert "ai" in enabled
    assert "web" not in enabled
    assert "common" in enabled, "a pack the file does not mention keeps its default"


def test_the_pack_names_are_read_from_the_manifest_not_restated() -> None:
    """A second copy of the six pack names is a second thing to keep in step."""
    from adopt_map.schemas import ExtractorManifest

    from adopt_cli.map_config import PACK_NAMES

    declared = get_args(ExtractorManifest.model_fields["pack"].annotation)
    assert set(PACK_NAMES) == set(declared)
