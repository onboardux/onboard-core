"""Assemble the third-party notices the packed `adopt` binary must carry.

The binary is a redistribution. It contains CPython, the native libraries the
interpreter links (OpenSSL, libffi, libbzip2 and others) and every runtime
dependency of `adopt-cli` (pydantic, typer, click, rich, ...). Their licences --
PSF, Apache-2.0, MIT, BSD -- each make the same condition of redistribution:
the copyright and permission notice travels with the copy.

Until 2026-10-01 nothing did. The release attached a CycloneDX SBOM, which names
licence *identifiers*, and the binary's onefile payload carried no licence text
at all: forty files, none of them a notice (measured by unpacking the published
`v0.4.1` Windows binary). This module builds the text that closes that gap, and
`release.yml` embeds it in every binary.

**Fail closed.** A runtime dependency that is installed but ships no licence
file, or one the constraints require on every platform that is not installed,
stops the build naming it. A notices file that silently omitted one dependency
would read exactly like a complete one, which is the failure mode every gate in
this repository exists to refuse. A marker-gated requirement that does not apply
to this platform (`colorama ; sys_platform == 'win32'` on Linux) is skipped.

Run it with the interpreter the binary is packed from, after the runtime wheels
are installed into it -- that interpreter's metadata is the closure the binary
contains, and its `sys.base_prefix` holds the CPython licence that is embedded.
"""

import argparse
import importlib.metadata as metadata
import platform
import re
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
NATIVE_NOTICES_DIR: Final[Path] = REPO_ROOT / "third_party" / "notices"
OUTPUT_NAME: Final[str] = "THIRD_PARTY_NOTICES.txt"

#: File-name prefixes that mark a file a distribution installs as a notice.
#: Matched anywhere in the distribution, not only in its `.dist-info`, because
#: some packages vendor others: typer carries click's licence under `_click/`.
_NOTICE_PREFIXES: Final[tuple[str, ...]] = ("licen", "copying", "notice", "authors")
_RULE: Final[str] = "========================================================================"
_REQUIREMENT: Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[^\s;]+)(?:\s*;\s*(?P<marker>.+))?$"
)


class NoticesIncomplete(Exception):
    """A dependency the binary carries has no notice this module could find."""


@dataclass(frozen=True)
class Requirement:
    name: str
    version: str
    marker: str | None


def parse_requirements(text: str) -> list[Requirement]:
    """`name==version [; marker]` lines from an exported runtime closure."""
    found: list[Requirement] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match is None:
            raise NoticesIncomplete(f"unparseable runtime requirement: {line!r}")
        found.append(Requirement(match["name"], match["version"], match["marker"]))
    return found


def _section(title: str, body: str) -> str:
    return f"{_RULE}\n{title}\n{_RULE}\n\n{body.strip()}\n"


def distribution_notices(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """Every notice file a distribution installs, as (path, text)."""
    notices: list[tuple[str, str]] = []
    for entry in dist.files or ():
        if not entry.name.lower().startswith(_NOTICE_PREFIXES):
            continue
        text = entry.read_text(encoding="utf-8")
        if text.strip():
            notices.append((str(entry), text))
    return notices


def interpreter_licence(base_prefix: Path, version: tuple[int, int]) -> str:
    """CPython's own LICENSE, from the interpreter the binary embeds."""
    major, minor = version
    for candidate in (
        base_prefix / "LICENSE.txt",
        base_prefix / "lib" / f"python{major}.{minor}" / "LICENSE.txt",
        base_prefix / "Lib" / "LICENSE.txt",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    raise NoticesIncomplete(
        f"no CPython LICENSE.txt under {base_prefix}; the binary embeds this interpreter "
        "and must carry its licence"
    )


def native_notices(native_dir: Path, version: tuple[int, int]) -> list[tuple[str, str]]:
    """The vendored texts no installed metadata supplies. See its README."""
    major, minor = version
    incorporated = native_dir / f"cpython-{major}.{minor}-license.rst"
    if not incorporated.is_file():
        raise NoticesIncomplete(
            f"no {incorporated.name} in {native_dir}: the software CPython incorporates "
            "differs between minor versions, so fetch the matching Doc/license.rst"
        )
    found = [("Software incorporated in CPython", incorporated.read_text(encoding="utf-8"))]
    for extra in sorted(native_dir.glob("*-LICENSE.txt")):
        name = extra.name.removesuffix("-LICENSE.txt")
        found.append((name, extra.read_text(encoding="utf-8")))
    return found


def build(
    requirements: Iterable[Requirement],
    *,
    lookup: Callable[[str], metadata.Distribution],
    cpython_licence: str,
    native: Sequence[tuple[str, str]],
    product_texts: Sequence[tuple[str, str]],
    python_version: str,
) -> str:
    """The notices document. Raises `NoticesIncomplete` naming every gap."""
    sections = [
        "Third-party notices for the adopt binary.\n\n"
        "This executable bundles the software below. Each component's copyright and "
        "licence notice is reproduced in full, as its licence requires.\n"
    ]
    for title, text in product_texts:
        sections.append(_section(title, text))
    sections.append(
        _section(f"CPython {python_version} (Python Software Foundation)", cpython_licence)
    )
    for title, text in native:
        sections.append(_section(title, text))

    gaps: list[str] = []
    for requirement in sorted(requirements, key=lambda r: r.name.lower()):
        try:
            dist = lookup(requirement.name)
        except metadata.PackageNotFoundError:
            if requirement.marker is None:
                gaps.append(f"{requirement.name}=={requirement.version} is not installed")
            continue
        notices = distribution_notices(dist)
        if not notices:
            gaps.append(f"{requirement.name}=={dist.version} ships no licence file")
            continue
        expression = dist.metadata.get("License-Expression") or ""
        title = f"{requirement.name} {dist.version}" + (f" ({expression})" if expression else "")
        sections.append(_section(title, "\n\n".join(text for _, text in notices)))
    if gaps:
        raise NoticesIncomplete("; ".join(gaps))
    return "\n".join(sections)


def _product_texts() -> list[tuple[str, str]]:
    return [
        (f"adopt-core: {name}", (REPO_ROOT / name).read_text(encoding="utf-8"))
        for name in ("NOTICE", "LICENSE")
        if (REPO_ROOT / name).is_file()
    ]


def _self_test() -> int:
    """Prove a dependency with no licence file stops the build, naming it."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        bare = root / "bare-1.0.dist-info"
        bare.mkdir()
        (bare / "METADATA").write_text("Metadata-Version: 2.1\nName: bare\nVersion: 1.0\n")
        (bare / "RECORD").write_text("bare-1.0.dist-info/METADATA,,\n")
        planted = metadata.PathDistribution(bare)

        def lookup(name: str) -> metadata.Distribution:
            if name == "bare":
                return planted
            raise metadata.PackageNotFoundError(name)

        requirements = parse_requirements(
            "bare==1.0\nmissing-everywhere==2.0\nwindows-only==3.0 ; sys_platform == 'win32'\n"
        )
        try:
            build(
                requirements,
                lookup=lookup,
                cpython_licence="planted",
                native=(),
                product_texts=(),
                python_version="planted",
            )
        except NoticesIncomplete as refused:
            message = str(refused)
            named = "bare==1.0 ships no licence file" in message
            unmarked = "missing-everywhere==2.0 is not installed" in message
            marker_skipped = "windows-only" not in message
            if named and unmarked and marker_skipped:
                print("self-test: OK -- a dependency with no notice is refused by name")
                return 0
            print(f"SELF-TEST FAILED: refused, but not for the planted reasons: {message}")
            return 1
    print("SELF-TEST FAILED: a dependency with no licence file produced a notices file.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--constraints", type=Path, help="Exported runtime closure.")
    parser.add_argument("--out", type=Path, default=Path(OUTPUT_NAME))
    parser.add_argument("--self-test", action="store_true", help="Prove a gap is refused.")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.constraints is None:
        parser.error("--constraints is required")
    version = (sys.version_info.major, sys.version_info.minor)
    try:
        document = build(
            parse_requirements(args.constraints.read_text(encoding="utf-8")),
            lookup=metadata.distribution,
            cpython_licence=interpreter_licence(Path(sys.base_prefix), version),
            native=native_notices(NATIVE_NOTICES_DIR, version),
            product_texts=_product_texts(),
            python_version=platform.python_version(),
        )
    except NoticesIncomplete as refused:
        print(f"VIOLATION: third-party notices incomplete: {refused}")
        return 1
    args.out.write_text(document, encoding="utf-8", newline="\n")
    print(f"third-party-notices: wrote {args.out} ({len(document.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
