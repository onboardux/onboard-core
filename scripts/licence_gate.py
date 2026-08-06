"""`licence-gate`: permissive-only in-binary, copyleft subprocess-only, some
dependencies denied by name.

Three rules, in the order they are checked.

1. **Denied by name.** Two dependencies are disqualified outright and the
   failure output quotes the reason, because "why not just use CodeQL?" is
   asked roughly once per quarter and the answer needs to arrive with the
   failure rather than after a meeting.
2. **Permissive-only in the binary.** A copyleft dependency may be *invoked*
   as a subprocess but never linked. Subprocess use must be declared in
   ``subprocess-deps.toml`` together with its invocation site, so the claim
   "we only shell out to it" is a checked fact rather than an intention.
3. **Every dependency carries a verification record.** ``licence-verifications.md``
   must give repository, version, licence hash, security status, usage mode,
   owner and re-verification date for each row. **A row missing any one of the
   seven blocks**, because a partial record is what lets an unverified
   dependency look verified.

The gate re-runs weekly on a schedule and not only on change: dependencies
relicense between our commits, and a gate that only fires on a diff will not
notice.

``--strict-verify`` (used at the release gate) additionally requires that every
installed distribution has a row, that no row is still ``pending-audit``, and
that no re-verification date has passed.
"""

import argparse
import hashlib
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
VERIFICATIONS_PATH: Final[Path] = REPO_ROOT / "licence-verifications.md"
SUBPROCESS_DEPS_PATH: Final[Path] = REPO_ROOT / "subprocess-deps.toml"

#: SPDX identifiers permitted for a dependency linked into the distribution.
#: MPL, EPL and CDDL are deliberately absent: they are file-level copyleft, and
#: "only the files we touched" is not a boundary anyone can audit at release
#: time.
PERMISSIVE_LICENCES: Final[frozenset[str]] = frozenset(
    {
        "MIT",
        "MIT-0",
        "BSD-2-CLAUSE",
        "BSD-3-CLAUSE",
        "0BSD",
        "APACHE-2.0",
        "ISC",
        "PSF-2.0",
        "PYTHON-2.0",
        "PYTHON-2.0.1",
        "UNLICENSE",
        "CC0-1.0",
        "ZLIB",
    }
)

#: Any of these substrings in a licence string means copyleft: permitted only
#: as a declared subprocess, never linked.
COPYLEFT_MARKERS: Final[tuple[str, ...]] = (
    "GPL",
    "AGPL",
    "LGPL",
    "MPL",
    "MOZILLA",
    "EPL",
    "ECLIPSE",
    "CDDL",
    "SSPL",
    "BUSL",
    "BUSINESS SOURCE",
)

#: Disqualified outright. The reason travels with the failure.
DENIED_BY_NAME: Final[dict[str, str]] = {
    "codeql": (
        "The CodeQL CLI engine's terms forbid analysis of non-open-source code, "
        "which is this product's entire purpose. There is no usage mode that makes "
        "it compliant, so it is denied by name rather than by licence class."
    ),
    "codeql-cli": (
        "The CodeQL CLI engine's terms forbid analysis of non-open-source code, "
        "which is this product's entire purpose."
    ),
    "semgrep-rules": (
        "Semgrep's maintained rule sets are internal-use only and non-redistributable. "
        "Semgrep CE itself remains permitted as a declared subprocess; it is the "
        "maintained rules that cannot ship."
    ),
    "semgrep-pro": (
        "Semgrep's maintained/Pro rule sets are internal-use only and non-redistributable."
    ),
}

REQUIRED_VERIFICATION_FIELDS: Final[tuple[str, ...]] = (
    "repository",
    "version",
    "licence_hash",
    "security_status",
    "usage_mode",
    "owner",
    "reverification_date",
)

#: How a dependency reaches a user, which is what decides whether copyleft is
#: acceptable. The policy constrains what is **linked into the distribution**;
#: a test-only dependency is never in the wheel or the binary, and a subprocess
#: dependency is invoked across a process boundary.
USAGE_IN_BINARY: Final[str] = "in-binary"
USAGE_SUBPROCESS: Final[str] = "subprocess"
USAGE_DEV_ONLY: Final[str] = "dev-only"
#: A runtime dependency of a service that is **never distributed** -- the closed
#: control plane. Copyleft obligations attach to conveying a work; a hosted
#: service conveys nothing, so `in-binary`'s permissive-only rule does not
#: describe it and `dev-only` is a false claim about a production dependency.
#: Recorded as its own mode so the distinction is a checked fact rather than a
#: judgement someone makes again at each review *(CR-29)*.
USAGE_SERVICE_SIDE: Final[str] = "service-side"
VALID_USAGE_MODES: Final[frozenset[str]] = frozenset(
    {USAGE_IN_BINARY, USAGE_SUBPROCESS, USAGE_DEV_ONLY, USAGE_SERVICE_SIDE}
)

PENDING_STATUS: Final[str] = "pending-audit"

#: Some projects paste an entire licence text into the `License` metadata
#: field. Only the first line is meaningful, and it is truncated so a pasted
#: licence cannot turn a report into a wall of text.
# const-sync: ok -- a display width, not AGENT_DEFAULT_MAX_WALL_SECONDS.
LICENCE_FIELD_MAX_CHARS: Final[int] = 120

_CLASSIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^License :: (?:OSI Approved :: )?(.+)$", re.MULTILINE
)

_CLASSIFIER_TO_SPDX: Final[dict[str, str]] = {
    "MIT License": "MIT",
    "BSD License": "BSD-3-CLAUSE",
    # Added at S8 *(CR-41)*: `protobuf` and `email-validator` declare these and
    # neither was mapped, so both read as unknown licences under a policy that
    # already permits BSD-3-Clause and Unlicense. Extending the map that exists
    # rather than adding a second one -- two tables translating licence names is
    # one of them drifting, silently, in whichever direction nobody is watching.
    "3-Clause BSD License": "BSD-3-CLAUSE",
    "2-Clause BSD License": "BSD-2-CLAUSE",
    "The Unlicense (Unlicense)": "UNLICENSE",
    "Apache Software License": "APACHE-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "GNU General Public License (GPL)": "GPL",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU Affero General Public License v3": "AGPL-3.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


@dataclass(frozen=True)
class Dependency:
    name: str
    version: str
    licence: str
    repository: str = ""


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _licence_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return str(expression).strip()
    classifiers = "\n".join(meta.get_all("Classifier") or [])
    found = [
        _CLASSIFIER_TO_SPDX.get(match.strip(), match.strip())
        for match in _CLASSIFIER_RE.findall(classifiers)
        if match.strip() != "OSI Approved"
    ]
    if found:
        return " OR ".join(found)
    declared = meta.get("License")
    if declared:
        # Some projects paste the entire licence text into this field.
        first = str(declared).strip().splitlines()[0]
        return first[:LICENCE_FIELD_MAX_CHARS]
    return "UNKNOWN"


def _repository_of(dist: metadata.Distribution) -> str:
    """The project's own repository URL, taken from its metadata.

    The licence must be verified against the LICENSE file in this repository.
    A package-index summary is a rendering, not evidence.
    """
    meta = dist.metadata
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = str(entry).partition(",")
        # `code` and `source-code` are as common as `source` in the wild --
        # `urllib3` and `charset-normalizer` use the first, `grimp` and
        # `import-linter` the second. Missing them made the gate report "no
        # repository" for four packages that declare one, which reads as an
        # unverifiable dependency rather than as an incomplete reader *(CR-41)*.
        if label.strip().lower() in {
            "source",
            "source code",
            "source-code",
            "code",
            "repository",
            "homepage",
            "github",
        }:
            return url.strip()
    home = meta.get("Home-page")
    return str(home).strip() if home else ""


def installed_dependencies() -> list[Dependency]:
    seen: dict[str, Dependency] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        key = normalise(str(name))
        # Workspace members are first-party, not third-party dependencies.
        if key.startswith(("adopt-", "plane-")):
            continue
        seen[key] = Dependency(key, dist.version or "", _licence_of(dist), _repository_of(dist))
    return sorted(seen.values(), key=lambda d: d.name)


def licence_hash(licence: str) -> str:
    """A stable digest of the declared licence string.

    Recorded per row so that a silent relicense between two of our commits
    shows up as a hash change rather than as nothing at all.
    """
    return hashlib.sha256(licence.encode("utf-8")).hexdigest()


def _is_copyleft(licence: str) -> bool:
    upper = licence.upper()
    return any(marker in upper for marker in COPYLEFT_MARKERS)


def _is_network_copyleft(licence: str) -> bool:
    """Licences whose obligations are triggered by *use over a network*.

    This is the line `service-side` stops at. GPL and LGPL attach on conveying a
    work, and a hosted service conveys nothing; AGPL and SSPL were written
    precisely to close that gap, so "we never ship it" is not an answer to them.
    `LGPL` is excluded by the prefix check below because "AGPL" contains "GPL"
    and a substring match would otherwise catch every copyleft licence there is.
    """
    upper = licence.upper()
    return any(marker in upper for marker in ("AGPL", "AFFERO", "SSPL"))


def _is_permissive(licence: str) -> bool:
    upper = licence.upper()
    parts = [p.strip(" ()") for p in re.split(r"\bOR\b|\bAND\b|,|/", upper) if p.strip()]
    return bool(parts) and all(p in PERMISSIVE_LICENCES for p in parts)


def load_subprocess_deps(path: Path) -> dict[str, str]:
    """``{name: invocation_site}`` for every declared subprocess dependency."""
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    declared: dict[str, str] = {}
    for entry in data.get("subprocess", []):
        name = normalise(str(entry.get("name", "")))
        site = str(entry.get("invocation_site", "")).strip()
        if name and site:
            declared[name] = site
    return declared


def parse_verifications(text: str) -> dict[str, dict[str, str]]:
    """Parse the ``licence-verifications.md`` table into ``{name: fields}``.

    A cell that is empty, ``-``, ``TBD`` or ``?`` counts as **missing**. That is
    the whole point: a row that looks complete but says nothing is worse than a
    row that is absent, because it reads as verified in a review.
    """
    rows: dict[str, dict[str, str]] = {}
    header: list[str] | None = None

    for raw in text.splitlines():
        if not raw.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        normalised = [re.sub(r"[^a-z_]", "", c.lower().replace(" ", "_")) for c in cells]
        # The document contains several explanatory tables before the register.
        # Latch onto the header of whichever table declares a `Dependency`
        # column, and re-latch if another one appears -- locking onto the first
        # table in the file is how this silently parsed zero rows.
        if normalised and normalised[0] == "dependency":
            header = normalised
            continue
        if header is None or len(cells) != len(header):
            continue
        record = dict(zip(header, cells, strict=True))
        name = normalise(record.get("dependency", "").strip("`"))
        if not name:
            continue
        rows[name] = {
            key: "" if value.strip(" `") in {"", "-", "TBD", "?", "n/a"} else value.strip(" `")
            for key, value in record.items()
        }
    return rows


def check(
    dependencies: list[Dependency],
    verifications: dict[str, dict[str, str]],
    subprocess_deps: dict[str, str],
    *,
    strict_verify: bool,
    today: date | None = None,
) -> Report:
    report = Report()
    today = today or date.today()

    for dep in dependencies:
        if dep.name in DENIED_BY_NAME:
            report.violations.append(
                f"LICENCE_POLICY_VIOLATION: {dep.name} is denied by name. "
                f"{DENIED_BY_NAME[dep.name]}"
            )
            continue

        row = verifications.get(dep.name, {})
        # Fail closed: a dependency with no declared usage mode is assumed to be
        # linked into the distribution, which is the strictest rule. An
        # undeclared dependency must never be the lenient case.
        usage = row.get("usage_mode") or USAGE_IN_BINARY
        if usage not in VALID_USAGE_MODES:
            report.violations.append(
                f"licence-verifications.md: {dep.name} declares usage mode {usage!r}; "
                f"expected one of {sorted(VALID_USAGE_MODES)}."
            )
            usage = USAGE_IN_BINARY

        if usage == USAGE_SUBPROCESS:
            site = subprocess_deps.get(dep.name)
            if site is None:
                report.violations.append(
                    f"LICENCE_POLICY_VIOLATION: {dep.name} is recorded as subprocess-only "
                    "but is not declared in subprocess-deps.toml with an invocation site. "
                    "'We only shell out to it' has to be a checked fact."
                )
            else:
                report.notes.append(
                    f"{dep.name} ({dep.licence}) subprocess-only, invoked at {site}."
                )
            continue

        if usage == USAGE_DEV_ONLY:
            if _is_copyleft(dep.licence):
                report.notes.append(
                    f"{dep.name} ({dep.licence}) is copyleft but dev-only: it is not linked "
                    "into the wheel or the binary. Moving it to a runtime dependency would "
                    "make it a policy violation."
                )
            continue

        if usage == USAGE_SERVICE_SIDE:
            # Strong copyleft still bites a hosted service under AGPL's network
            # clause, so the exemption stops short of it. GPL and LGPL do not
            # reach a work that is never conveyed.
            if _is_network_copyleft(dep.licence):
                report.violations.append(
                    f"LICENCE_POLICY_VIOLATION: {dep.name} {dep.version} is "
                    f"{dep.licence}, whose obligations are triggered by network use, "
                    "so `service-side` does not exempt it. Deploy it as a separate "
                    "service or remove it."
                )
            elif _is_copyleft(dep.licence):
                report.notes.append(
                    f"{dep.name} ({dep.licence}) is copyleft and service-side: it runs in a "
                    "service that is never distributed, so no conveying occurs. Linking it "
                    "into a shipped wheel or binary would make it a policy violation."
                )
            continue

        if _is_copyleft(dep.licence):
            report.violations.append(
                f"LICENCE_POLICY_VIOLATION: {dep.name} {dep.version} is "
                f"{dep.licence}, which is copyleft, and it is linked into the "
                "distribution. Policy: permissive-only in-binary, copyleft "
                "subprocess-only. Declare it in subprocess-deps.toml with its "
                "invocation site, record it as dev-only if it never ships, or "
                "remove it."
            )
        elif not _is_permissive(dep.licence):
            report.violations.append(
                f"LICENCE_POLICY_VIOLATION: {dep.name} {dep.version} declares "
                f"{dep.licence!r}, which is not on the permissive allowlist. "
                "Verify the LICENSE file in the dependency's own repository -- a "
                "package-index summary is not evidence -- then either add the SPDX "
                "identifier to PERMISSIVE_LICENCES or remove the dependency."
            )

    for name, row in sorted(verifications.items()):
        missing = [f for f in REQUIRED_VERIFICATION_FIELDS if not row.get(f)]
        if missing:
            report.violations.append(
                f"licence-verifications.md: [VERIFY] row for {name} is missing "
                f"{', '.join(missing)}. A row missing any of the seven fields blocks: "
                "a partial record is what lets an unverified dependency look verified."
            )

    if strict_verify:
        installed = {d.name for d in dependencies}
        for name in sorted(installed - set(verifications)):
            report.violations.append(
                f"licence-verifications.md: {name} is installed but has no [VERIFY] row."
            )
        for name, row in sorted(verifications.items()):
            if row.get("security_status") == PENDING_STATUS:
                report.violations.append(
                    f"licence-verifications.md: {name} is still {PENDING_STATUS}. "
                    "The release gate does not ship an unaudited dependency."
                )
            raw_date = row.get("reverification_date", "")
            try:
                due = date.fromisoformat(raw_date)
            except ValueError:
                continue
            if due < today:
                report.violations.append(
                    f"licence-verifications.md: {name} was due for re-verification on "
                    f"{due.isoformat()}. Dependencies relicense between our commits."
                )

    report.notes.append(
        f"checked {len(dependencies)} third-party distributions against "
        f"{len(verifications)} verification rows."
    )
    return report


def self_test() -> int:
    """Prove the gate rejects the three failures it exists to catch.

    Exits 0 when every planted violation was detected -- that is the gate
    working. A gate whose negative tests are never run is a gate that is
    assumed to work.
    """
    failures: list[str] = []

    planted_agpl = [Dependency("planted-agpl-package", "1.0.0", "AGPL-3.0-or-later")]
    agpl_report = check(planted_agpl, {}, {}, strict_verify=False)
    if not any("copyleft" in v for v in agpl_report.violations):
        failures.append("planted AGPL dependency was NOT rejected")
    else:
        print("self-test: planted AGPL dependency rejected ->")
        for violation in agpl_report.violations:
            print(f"  {violation}")

    incomplete = {
        "planted-unverified": {
            "dependency": "planted-unverified",
            "repository": "https://example.invalid/planted",
            "version": "1.0.0",
            "licence_hash": "planted-digest",
            "security_status": "clear",
            "usage_mode": "in-binary",
            "owner": "eng-lead",
            "reverification_date": "",
        }
    }
    row_report = check([], incomplete, {}, strict_verify=False)
    if not any("reverification_date" in v for v in row_report.violations):
        failures.append("[VERIFY] row missing a re-verification date was NOT rejected")
    else:
        print("self-test: [VERIFY] row missing a re-verification date rejected ->")
        for violation in row_report.violations:
            print(f"  {violation}")

    denied = [Dependency("codeql", "2.0.0", "MIT")]
    denied_report = check(denied, {}, {}, strict_verify=False)
    if not any("denied by name" in v for v in denied_report.violations):
        failures.append("denied-by-name dependency was NOT rejected")
    else:
        print("self-test: denied-by-name dependency rejected with its reason ->")
        for violation in denied_report.violations:
            print(f"  {violation}")

    # The alias map may rename a licence, never widen the policy *(CR-41)*. If
    # a copyleft phrase ever reaches it -- as a key or as a target -- the map has
    # become the route around the rule it sits beside.
    # The classifier map translates a licence *name*; it must never translate a
    # copyleft name into a permissive identifier. That is the one way a table of
    # spellings could become the route around the rule it sits beside.
    laundered = [
        f"{key} -> {target}"
        for key, target in _CLASSIFIER_TO_SPDX.items()
        if _is_copyleft(key) and not _is_copyleft(target)
    ]
    if laundered:
        failures.append(
            f"_CLASSIFIER_TO_SPDX maps a copyleft name to a permissive one: {laundered}"
        )
    else:
        print("self-test: no classifier mapping turns a copyleft licence permissive ->")
        print(f"  {len(_CLASSIFIER_TO_SPDX)} mappings checked")

    if failures:
        for failure in failures:
            print(f"SELF-TEST FAILURE: {failure}")
        return 1
    print("licence-gate --self-test: OK (3/3 planted violations detected)")
    return 0


def emit_rows() -> int:
    """Print a verification table row per installed distribution.

    Used to seed `licence-verifications.md` with real licence hashes rather
    than invented ones. Owner, security status and the re-verification date are
    human facts and are left blank on purpose -- so the gate blocks until a
    person fills them in.
    """
    for dep in installed_dependencies():
        print(
            f"| `{dep.name}` | {dep.repository} | {dep.version} "
            f"| `{licence_hash(dep.licence)[:16]}` | | | | {dep.licence} |"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="check the installed tree")
    parser.add_argument("--strict-verify", action="store_true", help="release-gate strictness")
    parser.add_argument("--self-test", action="store_true", help="prove the gate rejects")
    parser.add_argument("--emit-rows", action="store_true", help="seed the verification table")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Repository whose licence-verifications.md and subprocess-deps.toml govern. "
            "Defaults to this script's own repository; the closed repository points here "
            "so one implementation of the policy serves both rather than a copy that drifts."
        ),
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.emit_rows:
        return emit_rows()

    root = args.root.resolve() if args.root is not None else REPO_ROOT
    verifications_path = root / VERIFICATIONS_PATH.name

    if not verifications_path.exists():
        print(f"VIOLATION: {verifications_path} is absent; every dependency is unverified.")
        return 1
    verifications = parse_verifications(verifications_path.read_text(encoding="utf-8"))

    report = check(
        installed_dependencies(),
        verifications,
        load_subprocess_deps(root / SUBPROCESS_DEPS_PATH.name),
        strict_verify=args.strict_verify,
    )
    for note in report.notes:
        print(f"note: {note}")
    for violation in report.violations:
        print(f"VIOLATION: {violation}")

    if report.ok:
        print("licence-gate: OK")
        return 0
    print(f"licence-gate: {len(report.violations)} violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
