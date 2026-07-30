"""`adopt doctor` -- contracts §14.

Output: ``{config[{key, value, source}], environment{}, findings[]}``.
Exit `0` when there are no findings, `4` when there are -- degraded success, not
failure. `doctor` reports; it never repairs. A tool that quietly fixes what it
finds destroys the evidence needed to work out why the problem occurred.
"""

import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adopt_cli.config import (
    Resolution,
    load_config_file,
    project_config_path,
    resolve_all,
    user_config_path,
)

__all__ = ["build_payload", "collect_findings"]


def collect_findings(resolutions: list[Resolution]) -> list[dict[str, str]]:
    """Report, never repair."""
    findings: list[dict[str, str]] = []
    by_key = {r.key: r for r in resolutions}

    offline = (by_key["ADOPT_OFFLINE"].value or "1").strip().lower()
    if offline in {"0", "false", "no"}:
        findings.append(
            {
                "severity": "warning",
                "key": "ADOPT_OFFLINE",
                "detail": (
                    "Offline mode is disabled. The default posture is offline; network "
                    "egress should be an explicit, per-invocation opt-in rather than a "
                    "standing configuration setting."
                ),
            }
        )

    adapter = by_key["ADOPT_ADAPTER"].value
    model = by_key["ADOPT_MODEL"].value
    if adapter and not model:
        findings.append(
            {
                "severity": "warning",
                "key": "ADOPT_MODEL",
                "detail": (
                    f"Adapter {adapter!r} is configured but ADOPT_MODEL is unset, and no "
                    "model identifier is hard-coded anywhere. The adapter will fail at "
                    "construction with AGENT_ADAPTER_UNKNOWN."
                ),
            }
        )

    if model and not adapter:
        findings.append(
            {
                "severity": "warning",
                "key": "ADOPT_ADAPTER",
                "detail": "ADOPT_MODEL is set but no adapter is configured; it will be ignored.",
            }
        )

    return findings


def build_payload(
    *,
    flags: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    project_path = project_config_path(cwd)
    user_path = user_config_path(home)
    resolutions = resolve_all(
        flags=flags,
        env=env,
        project=load_config_file(project_path),
        user=load_config_file(user_path),
    )
    findings = collect_findings(resolutions)
    payload: dict[str, Any] = {
        "config": [r.render() for r in resolutions],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
            "executable": sys.executable,
            "project_config": str(project_path) if project_path.exists() else None,
            "user_config": str(user_path) if user_path.exists() else None,
        },
        "findings": findings,
    }
    return payload, findings
