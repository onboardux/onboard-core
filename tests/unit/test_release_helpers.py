"""Release helpers hold the irreversible publication invariants cheaply."""

import hashlib
import json
from pathlib import Path

import pytest
from scripts import (
    assert_release_complete,
    embed_build_info,
    emit_sbom,
    release_context,
)
from scripts.licence_gate import licence_hash


def _audit_table(rows: list[tuple[str, str, str, str]]) -> str:
    header = (
        "| Dependency | Repository | Version | Licence hash | Security status | "
        "Usage mode | Owner | Re-verification date | Licence |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| `{name}` | {repository} | {version} | `{licence_hash(licence)[:16]}` | "
        f"clean-2026-08-11 | in-binary | owner | 2026-10-30 | {licence} |\n"
        for name, version, licence, repository in rows
    )
    return header + body


def _write_audited_closure(tmp_path: Path) -> tuple[Path, Path]:
    constraints = tmp_path / "runtime-constraints.txt"
    constraints.write_text(
        "annotated-doc==0.0.5\ncolorama==0.4.6 ; sys_platform == 'win32'\n",
        encoding="utf-8",
    )
    verifications = tmp_path / "licence-verifications.md"
    verifications.write_text(
        _audit_table(
            [
                (
                    "annotated-doc",
                    "0.0.5",
                    "MIT",
                    "https://github.com/fastapi/annotated-doc",
                ),
                (
                    "colorama",
                    "0.4.6",
                    "BSD-3-Clause",
                    "https://github.com/tartley/colorama",
                ),
            ]
        ),
        encoding="utf-8",
    )
    return constraints, verifications


def _write_payload_evidence(path: Path) -> None:
    if not path.exists():
        path.write_bytes(b"payload")
    path.with_name(f"{path.name}.sig").write_text("signature", encoding="utf-8")
    path.with_name(f"{path.name}.pem").write_text("certificate", encoding="utf-8")


def _complete_release(tmp_path: Path, *, version: str = "0.3.0") -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    sbom = {
        "bomFormat": emit_sbom.CYCLONEDX_FORMAT,
        "specVersion": emit_sbom.CYCLONEDX_SPEC_VERSION,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": emit_sbom.SBOM_ROOT_NAME,
                "version": version,
            }
        },
        "components": [{"name": "colorama", "version": "0.4.6"}],
    }
    sbom_path = dist / assert_release_complete.SBOM_NAME
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")

    payloads = [sbom_path]
    for name in release_context.CANONICAL_DISTRIBUTIONS:
        archive_name = name.replace("-", "_")
        payloads.extend(
            [
                dist / f"{archive_name}-{version}-py3-none-any.whl",
                dist / f"{archive_name}-{version}.tar.gz",
            ]
        )
    payloads.extend(dist / name for name in assert_release_complete.EXPECTED_BINARIES)
    for payload in payloads:
        _write_payload_evidence(payload)
    (dist / "provenance.intoto.jsonl").write_text(
        '{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}\n',
        encoding="utf-8",
    )
    return dist


@pytest.mark.unit
def test_sbom_uses_target_union_and_retains_windows_marker(tmp_path: Path) -> None:
    """A Linux-generated SBOM must still describe Windows-only runtime packages."""
    constraints, verifications = _write_audited_closure(tmp_path)
    output = tmp_path / "sbom.cdx.json"

    assert (
        emit_sbom.main(
            [
                "--out",
                str(output),
                "--name",
                "adopt-core",
                "--version",
                "0.3.0",
                "--constraints",
                str(constraints),
                "--verifications",
                str(verifications),
            ]
        )
        == 0
    )
    assert b"\r\n" not in output.read_bytes()
    sbom = json.loads(output.read_text(encoding="utf-8"))

    colorama = next(
        component for component in sbom["components"] if component["name"] == "colorama"
    )
    assert colorama["version"] == "0.4.6"
    assert colorama["licenses"] == [{"expression": "BSD-3-Clause"}]
    assert colorama["properties"] == [
        {"name": emit_sbom.MARKER_PROPERTY, "value": "sys_platform == 'win32'"}
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_constraints",
    [
        "colorama>=0.4.6\n",
        "colorama==0.4.6\ncolorama==0.4.6 ; sys_platform == 'win32'\n",
        "adopt-cli==0.3.0\n",
    ],
)
def test_sbom_rejects_non_exact_or_non_runtime_constraint_entries(
    tmp_path: Path, bad_constraints: str
) -> None:
    constraints, verifications = _write_audited_closure(tmp_path)
    constraints.write_text(bad_constraints, encoding="utf-8")

    with pytest.raises(ValueError):
        emit_sbom.load_audited_runtime_dependencies(constraints, verifications)


@pytest.mark.unit
def test_sbom_rejects_a_pin_that_differs_from_the_audited_version(tmp_path: Path) -> None:
    constraints, verifications = _write_audited_closure(tmp_path)
    constraints.write_text("colorama==9.9.9\n", encoding="utf-8")

    with pytest.raises(ValueError, match="audited version"):
        emit_sbom.load_audited_runtime_dependencies(constraints, verifications)


@pytest.mark.unit
def test_release_context_requires_the_canonical_lockstep_workspace(tmp_path: Path) -> None:
    for name in release_context.CANONICAL_DISTRIBUTIONS:
        project = tmp_path / "packages" / name / "pyproject.toml"
        project.parent.mkdir(parents=True)
        project.write_text(f'[project]\nname = "{name}"\nversion = "0.3.0"\n', encoding="utf-8")

    context = release_context.resolve_context(tmp_path)
    assert context.distribution_count == 15
    assert context.distributions == tuple(sorted(release_context.CANONICAL_DISTRIBUTIONS))

    github_output = tmp_path / "github-output.txt"
    assert (
        release_context.main(
            [
                "--event-name",
                "workflow_dispatch",
                "--ref",
                "refs/heads/main",
                "--publish",
                "false",
                "--github-output",
                str(github_output),
                "--root",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (
        "distributions=" + " ".join(sorted(release_context.CANONICAL_DISTRIBUTIONS))
        in github_output.read_text(encoding="utf-8").splitlines()
    )

    missing = next(iter(release_context.CANONICAL_DISTRIBUTIONS))
    (tmp_path / "packages" / missing / "pyproject.toml").unlink()
    with pytest.raises(ValueError, match="not canonical"):
        release_context.resolve_context(tmp_path)


@pytest.mark.unit
def test_exact_manual_tag_route_is_the_positive_publication_case() -> None:
    context = release_context.Context(version="0.3.0", tag="v0.3.0", distribution_count=15)

    assert not release_context.validate_route(
        context,
        event_name="workflow_dispatch",
        git_ref="refs/tags/v0.3.0",
        publish=True,
    )


@pytest.mark.unit
def test_build_info_hashes_exact_bytes_and_validates_github_identity(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_bytes(b'{"components":[]}\r\n')
    build_id = f"github:owner/repository:12345:2:{'a' * 40}"

    rendered = embed_build_info.render(sbom, build_id)

    assert hashlib.sha256(sbom.read_bytes()).hexdigest() in rendered
    assert repr(build_id) in rendered
    with pytest.raises(ValueError, match="must match"):
        embed_build_info.render(sbom, "github:owner/repository:12345:2:not-a-sha")


@pytest.mark.unit
def test_complete_release_requires_exact_names_and_versions(tmp_path: Path) -> None:
    dist = _complete_release(tmp_path)
    valid = assert_release_complete.check(
        dist, expected_version="0.3.0", expected_python_distributions=15
    )
    assert valid.ok, valid.violations
    wrong_count = assert_release_complete.check(
        dist, expected_version="0.3.0", expected_python_distributions=14
    )
    assert any("count must be 15" in violation for violation in wrong_count.violations)

    wheel = dist / "adopt_cli-0.3.0-py3-none-any.whl"
    wheel.unlink()
    wheel.with_name(f"{wheel.name}.sig").unlink()
    wheel.with_name(f"{wheel.name}.pem").unlink()
    spoof = dist / "adopt_cli-9.9.9-0.3.0-py3-none-any.whl"
    _write_payload_evidence(spoof)

    report = assert_release_complete.check(
        dist, expected_version="0.3.0", expected_python_distributions=15
    )
    assert not report.ok
    assert any("carries version 9.9.9" in violation for violation in report.violations)


@pytest.mark.unit
def test_malformed_sbom_and_provenance_are_reported_without_crashing(tmp_path: Path) -> None:
    dist = _complete_release(tmp_path)
    (dist / assert_release_complete.SBOM_NAME).write_text("[]", encoding="utf-8")
    (dist / "provenance.intoto.jsonl").write_text("{not-json}\n", encoding="utf-8")

    report = assert_release_complete.check(
        dist, expected_version="0.3.0", expected_python_distributions=15
    )

    assert not report.ok
    assert any("root must be a JSON object" in violation for violation in report.violations)
    assert any("malformed JSON" in violation for violation in report.violations)


@pytest.mark.unit
def test_binary_ceiling_does_not_apply_to_python_distributions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = _complete_release(tmp_path)
    monkeypatch.setattr(assert_release_complete, "BINARY_MAX_MB", 1)
    (dist / "adopt_cli-0.3.0-py3-none-any.whl").write_bytes(b"x" * (2 * 1024 * 1024))

    report = assert_release_complete.check(
        dist, expected_version="0.3.0", expected_python_distributions=15
    )

    assert report.ok, report.violations
