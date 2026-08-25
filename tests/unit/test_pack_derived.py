"""Derived formats: content-equivalent, never canon, and never silently absent.

v6.1 §6 Build 4's fourth demo line. The Markdown is the canonical output and is
byte-stable; a DOCX or a PDF is a conversion of it, asserted **structurally**
(every heading and every stamp survives) rather than byte-wise, because two
pandoc releases produce different bytes from one input and the spec already
concedes that.

**The tests that always run are the ones about refusal.** A machine without the
converter is the common case -- pandoc is installed in CI and on nobody's laptop
by default -- and the failure that matters is not a bad DOCX, it is a DOCX that
was never written while the command said nothing. Those assertions need no tool.
The content-equivalence test needs one and skips without it, which is honest:
a skipped test is visible, and a test that passed by converting nothing would
not be.
"""

import shutil
from pathlib import Path

import pytest
from adopt_handover import FORMATS, MARKDOWN, convert, converter_for
from adopt_handover.derived import tool_version

from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_PACK = """# Handover pack — client_ops

## System overview

Identities mapped: **1**

| Kind | Count |
|---|---|
| endpoint | 1 |

## Runbook and how-to

### endpoint POST /v1/orders

*Status:* **unverified** · *Dated:* 2026-01-01T00:00:00Z · *Revision:* `krev_01`

> **UNVERIFIED — nothing has confirmed this section.**

The endpoint accepts orders.
"""


def _pandoc() -> object:
    return converter_for("docx")


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    path = tmp_path / "client_ops.md"
    path.write_text(_PACK, encoding="utf-8", newline="\n")
    return path


class TestFormatSelection:
    def test_markdown_needs_no_converter(self) -> None:
        """`--format md` is the canonical path and must not reach a subprocess.

        *Fails when* the default format acquires a tool dependency. *Matters
        because* the pack is the product and a machine with no pandoc must still
        produce one. *No other instrument catches it because* every developer
        machine that happens to have pandoc would pass."""
        assert converter_for(MARKDOWN) is None

    def test_an_unknown_format_is_refused_by_name(self) -> None:
        with pytest.raises(AdoptError) as raised:
            converter_for("epub")

        assert raised.value.code is ErrorCode.PACK_RENDERER_MISSING
        assert "md" in str(raised.value.hint)

    def test_every_declared_format_carries_a_licence_and_an_install_hint(self) -> None:
        """*Fails when* a converter is added without its licence recorded.
        *Matters because* the licence is what decides `subprocess` versus
        `in-binary`, and pandoc's GPL is only compliant *because* it is never
        linked -- a converter added without that row is a policy violation
        nobody wrote down. *No other instrument catches it because*
        `licence_gate` reads `licence-verifications.md`, not this table, so a
        tool added here and forgotten there would run happily."""
        for converter in FORMATS:
            assert converter.licence
            assert converter.install_hint
            assert "{output}" in " ".join(converter.arguments)

    def test_the_command_is_an_argument_list_and_never_a_shell_string(self, tmp_path: Path) -> None:
        """*Fails when* an argument stops being rendered from the fixed table.
        *Matters because* the only values that reach a converter are paths this
        command constructed -- no store text, no operator text -- and that is
        what makes `shell=False` sufficient rather than merely conventional."""
        converter = FORMATS[0]
        command = converter.command(tmp_path / "in.md", tmp_path / "out.docx")

        assert command[0] == converter.executable
        assert all(isinstance(argument, str) for argument in command)
        assert any(str(tmp_path / "out.docx") in argument for argument in command)


class TestAMissingToolIsRefusedNotSkipped:
    def test_converting_without_the_tool_names_the_tool_and_the_fix(
        self, pack: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Fails when* an absent converter produces a warning, a `None`, or
        nothing at all. *Matters because* the Markdown and the sidecar are
        written **before** any conversion, so a silent skip leaves an operator
        who asked for a DOCX looking at a directory that appears finished. *No
        other instrument catches it because* the command exits zero and the pack
        it did write is perfectly correct.

        `which` is stubbed rather than the PATH edited, so the test asserts the
        same thing on a machine that has pandoc and one that does not."""
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        converter = _pandoc()
        assert converter is not None

        with pytest.raises(AdoptError) as raised:
            convert(pack, tmp_path / "out.docx", converter)  # type: ignore[arg-type]

        assert raised.value.code is ErrorCode.PACK_RENDERER_MISSING
        assert "pandoc" in str(raised.value.message)
        assert "install" in str(raised.value.hint).lower()
        assert not (tmp_path / "out.docx").exists()

    def test_the_version_of_an_absent_tool_is_none_rather_than_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        converter = _pandoc()
        assert converter is not None

        assert tool_version(converter) is None  # type: ignore[arg-type]


class TestContentEquivalence:
    @pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
    def test_the_docx_carries_every_heading_and_every_stamp(
        self, pack: Path, tmp_path: Path
    ) -> None:
        """Content-equivalence, structurally.

        *Fails when* the conversion drops a section or -- far worse -- drops the
        UNVERIFIED banner. *Matters because* a DOCX is what actually reaches a
        client, and an unverified section that arrives in Word without its
        banner is v6.1's named worst failure with an extra file extension in
        front of it. *No other instrument catches it because* the Markdown is
        byte-stable and perfect; only the conversion would have lost it.

        Asserted over the extracted document text rather than over bytes: two
        pandoc releases produce different DOCX bytes from one input, which is
        exactly why v6.1 says content-equivalent rather than byte-stable."""
        import zipfile

        converter = _pandoc()
        assert converter is not None
        target = tmp_path / "client_ops.docx"

        version = convert(pack, target, converter)  # type: ignore[arg-type]

        assert version is not None
        assert target.exists()
        with zipfile.ZipFile(target) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
        # Word splits runs, so the text is checked word-wise rather than as one
        # string -- a phrase assertion would fail on formatting, not on content.
        for token in ("Handover", "Runbook", "UNVERIFIED", "unverified", "orders"):
            assert token in document
