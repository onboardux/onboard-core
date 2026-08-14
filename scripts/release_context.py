"""Validate the lockstep workspace version, tag and publication route."""

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
#: Every distribution the workspace publishes, in lockstep at one version.
#:
#: Build 1 adds `adopt-map` and `adopt-extractors-common` here in S1.1 rather
#: than at release time, because everything downstream is *derived* from this
#: set -- `EXPECTED_DISTRIBUTION_COUNT`, the licence sweep's expected count, and
#: the release-completeness inventory. A package that builds a wheel but is
#: absent from this set is reported as an unexpected payload, so the set has to
#: move in the same change as the package.
#:
#: Consequence, recorded so it is not rediscovered at the tag: this is **two
#: more PyPI names to claim**. Build 0's DoD condition 6 is already blocked on
#: eleven outstanding names, and `05-sprint-plan.md` prerequisite 8 is explicit
#: that publication is *not* a Build 1 prerequisite -- so this changes the size
#: of that queue and nothing about whether S1.1 can close.
CANONICAL_DISTRIBUTIONS: Final[frozenset[str]] = frozenset(
    {
        "adopt-agent",
        "adopt-cli",
        "adopt-const",
        "adopt-coverage",
        "adopt-detect",
        "adopt-export",
        "adopt-extractors-common",
        "adopt-extractors-web",
        "adopt-freshness",
        "adopt-identity",
        "adopt-map",
        "adopt-model",
        "adopt-obs",
        "adopt-policy",
        "adopt-schema",
        "adopt-scope",
        "adopt-store",
        "adopt-workflow",
    }
)


@dataclass(frozen=True)
class Context:
    version: str
    tag: str
    distribution_count: int

    @property
    def distributions(self) -> tuple[str, ...]:
        """Canonical publish order for shell loops and release inventories."""
        return tuple(sorted(CANONICAL_DISTRIBUTIONS))


def resolve_context(root: Path = REPO_ROOT) -> Context:
    """Return the one version shared by every publishable workspace package."""
    versions: dict[str, str] = {}
    projects = sorted((root / "packages").glob("*/pyproject.toml"))
    if not projects:
        raise ValueError(f"no package pyprojects found under {root / 'packages'}")

    for project in projects:
        data = tomllib.loads(project.read_text(encoding="utf-8"))
        name = str(data["project"]["name"])
        if name in versions:
            raise ValueError(f"workspace distribution name is duplicated: {name}")
        versions[name] = str(data["project"]["version"])

    distinct = set(versions.values())
    if len(distinct) != 1:
        detail = ", ".join(f"{name}={version}" for name, version in sorted(versions.items()))
        raise ValueError(f"workspace package versions are not lockstep: {detail}")

    version = distinct.pop()
    if not version or any(char.isspace() or char in "/\\" for char in version):
        raise ValueError(f"workspace version is not safe for a release tag: {version!r}")

    actual = set(versions)
    missing = CANONICAL_DISTRIBUTIONS - actual
    unexpected = actual - CANONICAL_DISTRIBUTIONS
    if missing or unexpected:
        set_detail: list[str] = []
        if missing:
            set_detail.append("missing " + ", ".join(sorted(missing)))
        if unexpected:
            set_detail.append("unexpected " + ", ".join(sorted(unexpected)))
        raise ValueError("workspace distribution set is not canonical: " + "; ".join(set_detail))
    return Context(
        version=version,
        tag=f"v{version}",
        distribution_count=len(CANONICAL_DISTRIBUTIONS),
    )


def validate_route(context: Context, *, event_name: str, git_ref: str, publish: bool) -> list[str]:
    """Return every fail-closed routing violation for this invocation."""
    violations: list[str] = []
    if git_ref.startswith("refs/tags/") and git_ref != f"refs/tags/{context.tag}":
        violations.append(
            f"tag {git_ref.removeprefix('refs/tags/')} does not match workspace "
            f"version {context.version}; expected {context.tag}"
        )
    if publish and event_name != "workflow_dispatch":
        violations.append("publish=true is permitted only on a manual workflow dispatch")
    if publish and git_ref != f"refs/tags/{context.tag}":
        violations.append(
            f"publish=true requires ref refs/tags/{context.tag}; received {git_ref or '<empty>'}"
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--publish", choices=("true", "false"), default="false")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    try:
        context = resolve_context(args.root)
    except (KeyError, TypeError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"VIOLATION: {exc}")
        return 1

    violations = validate_route(
        context,
        event_name=args.event_name,
        git_ref=args.ref,
        publish=args.publish == "true",
    )
    for violation in violations:
        print(f"VIOLATION: {violation}")
    if violations:
        return 1

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"version={context.version}\n")
            output.write(f"tag={context.tag}\n")
            output.write(f"distribution_count={context.distribution_count}\n")
            output.write(f"distributions={' '.join(context.distributions)}\n")
    print(
        f"release context: {context.distribution_count} distributions at "
        f"version {context.version}; expected tag {context.tag}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
