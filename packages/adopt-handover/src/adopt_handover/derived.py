"""Derived formats: DOCX through pandoc, PDF through typst. Both subprocesses.

v6.1 §6 Build 4: derived formats are **content-equivalent, not byte-stable**.
The Markdown is the canon; these are conversions of it, and nothing downstream
of a conversion is ever read back into the store.

**Subprocess, and that is a licence boundary rather than a style choice.**
Pandoc is GPL-2.0-or-later, and `03` §7.3 permits no copyleft `in-binary`. A
subprocess never links, so the obligation never attaches to anything we ship --
and the rule is only *true* if the invocation site is declared, which is what
`subprocess-deps.toml` is for. Typst is Apache-2.0 and would be importable if it
were a Python library; it is a Rust binary, so it arrives the same way and gets
the same row. One seam, two tools, no import of either.

**Nothing here reads the store, and nothing here writes one.** A converter takes
a Markdown file and produces another file beside it. That is the whole surface,
and it is why this module can hold a `subprocess` import in a package whose other
five modules hold none.

**A missing tool is refused, never skipped.** The canonical Markdown and its
sidecar are written *before* any conversion is attempted, so a silent skip would
leave an operator who asked for a DOCX with a directory that looks finished and
no DOCX in it. `PACK_RENDERER_MISSING` names the tool and the install command.

**The version is printed, never enforced.** Pandoc's Markdown reader has moved
between releases and a pack converted on two machines can differ; that is what
"content-equivalent, not byte-stable" already concedes. Recording which binary
did it makes a surprising DOCX explicable, where pinning an exact version would
make the command fail on every machine that has a different one -- refusing to
convert a document because the converter is one patch release ahead is a worse
outcome than converting it.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from adopt_const import PACK_CONVERT_TIMEOUT_SECONDS
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = [
    "FORMATS",
    "MARKDOWN",
    "Converter",
    "convert",
    "converter_for",
    "tool_version",
]

_log = get_logger("adopt_handover")

#: The canonical format. Named so `--format md` is a value rather than a
#: special case the caller has to remember not to pass here.
MARKDOWN: Final[str] = "md"


@dataclass(frozen=True, slots=True)
class Converter:
    """One external tool, and how to drive it.

    Data rather than a subclass per tool: the two differ in an executable name,
    an argument list and an install hint, and a class hierarchy over three
    strings is three files where a table would do.
    """

    format: str
    executable: str
    licence: str
    install_hint: str
    #: Rendered with `{input}`, `{output}` and `{root}`. Every argument is ours;
    #: nothing from the store and no operator text reaches this list, which is
    #: what keeps the invocation free of anything a shell would have to be
    #: trusted with -- and there is no shell at all, because `shell=False` is the
    #: default and is never overridden here.
    arguments: tuple[str, ...]

    def command(self, source: Path, target: Path) -> list[str]:
        return [self.executable] + [
            argument.format(input=str(source), output=str(target), root=str(target.parent))
            for argument in self.arguments
        ]


#: The two derived formats v6.1 §6 Build 4 names, and nothing else. A third
#: would need its own licence row and its own subprocess declaration, which is
#: the friction that keeps this list short.
FORMATS: Final[tuple[Converter, ...]] = (
    Converter(
        format="docx",
        executable="pandoc",
        licence="GPL-2.0-or-later",
        install_hint="Install pandoc (https://pandoc.org/installing.html), or drop --format "
        "to keep the Markdown pack, which is the canonical output either way.",
        # `--from=gfm` rather than pandoc's default `markdown`: the pack is
        # written as GitHub-flavoured Markdown (pipe tables, fenced blocks), and
        # pandoc's own dialect reads a pipe table as a paragraph -- which turns
        # the gap appendix, the one table a client actually reads, into a wall
        # of pipes. `--standalone` because a DOCX fragment is not a document.
        arguments=("--from=gfm", "--to=docx", "--standalone", "--output={output}", "{input}"),
    ),
    Converter(
        format="pdf",
        executable="typst",
        licence="Apache-2.0",
        install_hint="Install typst (https://github.com/typst/typst#installation), or use "
        "--format docx, or keep the Markdown pack.",
        # Typst reads its own markup rather than Markdown, so the Markdown is
        # handed to it as the body of a minimal document written by `convert`.
        # `--root` is pinned to the output directory so the compile cannot read
        # a file outside the pack it was asked to render.
        arguments=("compile", "--root={root}", "{input}", "{output}"),
    ),
)

_BY_FORMAT: Final[dict[str, Converter]] = {converter.format: converter for converter in FORMATS}


def converter_for(format_name: str) -> Converter | None:
    """The converter for a format name, or `None` for the canonical Markdown.

    Raises:
        AdoptError: ``PACK_RENDERER_MISSING`` when the name is not a format this
            build knows. The same code as an absent tool, because the operator's
            situation is the same one -- they asked for an output this machine
            cannot produce -- and the hint distinguishes the two cases.
    """
    if format_name == MARKDOWN:
        return None
    converter = _BY_FORMAT.get(format_name)
    if converter is None:
        known = ", ".join([MARKDOWN, *sorted(_BY_FORMAT)])
        raise AdoptError(
            ErrorCode.PACK_RENDERER_MISSING,
            message=f"no renderer for format {format_name!r}",
            hint=f"Known formats: {known}.",
        )
    return converter


def tool_version(converter: Converter) -> str | None:
    """The installed tool's first version line, or `None` when it is absent.

    Recorded on every conversion so a pack that came out looking wrong can be
    traced to the binary that produced it. Never compared against anything: see
    the module docstring on why pinning would refuse more work than it saves.
    """
    if shutil.which(converter.executable) is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed executable, no shell, no operator input.
            [converter.executable, "--version"],
            capture_output=True,
            text=True,
            timeout=PACK_CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (completed.stdout or completed.stderr or "").strip().splitlines()
    return first[0].strip() if first else None


def convert(source: Path, target: Path, converter: Converter) -> str | None:
    """Convert `source` to `target`. Returns the tool version that did it.

    Args:
        source: The canonical Markdown, already written. Read by the tool, never
            by this module -- the bytes handed to the converter are provably the
            bytes on disk.
        target: Where the derived file goes.
        converter: Which tool, from `FORMATS`.

    Raises:
        AdoptError: ``PACK_RENDERER_MISSING`` when the tool is absent or when it
            exits non-zero. Both are the same sentence to an operator -- *the
            derived format was not produced* -- and the message carries the
            tool's own stderr, which says more about a failed conversion than
            anything this module could infer.
    """
    version = tool_version(converter)
    if version is None:
        raise AdoptError(
            ErrorCode.PACK_RENDERER_MISSING,
            message=f"{converter.executable!r} is not on PATH, so no {converter.format} "
            "pack was written",
            hint=converter.install_hint,
        )

    if converter.format == "pdf":
        source = _typst_document(source, target)

    try:
        completed = subprocess.run(  # noqa: S603 -- fixed executable, no shell, no operator input.
            converter.command(source, target),
            capture_output=True,
            text=True,
            timeout=PACK_CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as failure:
        raise AdoptError(
            ErrorCode.PACK_RENDERER_MISSING,
            message=f"{converter.executable} could not be run: {failure.__class__.__name__}",
            hint=converter.install_hint,
        ) from failure

    if completed.returncode != 0:
        raise AdoptError(
            ErrorCode.PACK_RENDERER_MISSING,
            message=f"{converter.executable} exited {completed.returncode} and wrote no "
            f"{converter.format} pack",
            # The tool's own first stderr line: it names the actual cause, and a
            # converter's diagnostics are more useful to whoever has to fix this
            # than a sentence we invented about them.
            hint=_first_line(completed.stderr) or converter.install_hint,
        )

    _log.info(
        "pack.converted",
        format=converter.format,
        tool=converter.executable,
        licence=converter.licence,
    )
    return version


def _first_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def _typst_document(source: Path, target: Path) -> Path:
    """Wrap the Markdown as a typst document beside the output, and return it.

    Typst reads its own markup, not Markdown, so the pack's text goes into a
    `raw` block: the PDF then carries the pack **verbatim**, which is what
    content-equivalence requires and what a lossy Markdown-to-typst translation
    would not give. A prettier PDF would mean owning a second Markdown parser,
    and this module owns no format semantics on purpose.

    The wrapper is written beside the target rather than into a scratch
    directory because typst's `read()` resolves against `--root`, which is the
    output directory -- so the pack and its wrapper have to be siblings. It is
    left in place afterwards for the reason the sidecar is: a build artefact
    that explains how the artefact beside it was made.
    """
    wrapper = target.with_suffix(".typ")
    wrapper.write_text(
        "#set page(margin: 2cm)\n"
        "#set text(size: 9pt)\n"
        f'#raw(read("{source.name}"), lang: "markdown", block: true)\n',
        encoding="utf-8",
        newline="\n",
    )
    return wrapper
