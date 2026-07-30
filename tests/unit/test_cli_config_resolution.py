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

import pytest

from adopt_cli.commands import doctor as doctor_command
from adopt_cli.commands import version as version_command
from adopt_cli.config import REGISTRY, Source, load_config_file, resolve_all

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
def test_version_reports_null_build_provenance_outside_a_release_build() -> None:
    """Fabricating a build id would break the one thing the field is for:
    tying a binary in the field back to the artifact that was signed."""
    payload = version_command.build_payload(env={})

    assert payload["sbom_sha256"] is None
    assert payload["build_id"] is None
    assert payload["schema_version"] == payload["export_version"]
