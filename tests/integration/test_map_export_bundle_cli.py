"""`adopt map --archetype platform` without a usable bundle -- `02` §8, `05` S1.6.

`05` S1.6's Final Output Validation line 2: *"A platform run without
`--export-bundle` exits 4 with the export command in the message."* Asserted
through the real CLI, because the promise is about what an operator sees: an exit
code **and** a remediation they can copy.

**The refusals are asserted on the JSON envelope, not on the rendered text**, and
that is a deliberate choice rather than a convenience. `02` §8 makes the envelope
the contract an integrator scripts against; the human rendering goes through a
rich `Console` bound to `sys.stderr` at import time, which no test harness can
reliably recapture afterwards. Asserting the envelope tests the promise; asserting
the pretty printing would test the terminal.

`02` §8's exit-4 row also promises *"zero writes"*, so both cases assert the store
is byte-identical afterwards — the same standard `05` S1.1's validation line set
for the other declined paths.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adopt_cli.main import app
from adopt_scope import Scope, ScopeNode
from adopt_store.api import open_store

pytestmark = pytest.mark.integration

_BUNDLE = Path("fixtures/repos/sf-metadata-bundle")


@pytest.fixture
def cli(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> Callable[..., tuple[int, str, str]]:
    """A scoped store and a callable that runs `adopt map` against it."""
    store = tmp_path / "store.db"
    handle = open_store(store, migrate=True)
    facade = handle.scope()
    firm = facade.create_firm(slug="northwind", name="Northwind LLP")
    engagement = facade.create_engagement(firm_id=firm.id, slug="acme-erp", name="ACME ERP")
    system = facade.create_system(engagement_id=engagement.id, slug="sfdc", name="Salesforce org")
    facade.create_environment(system_id=system.id, slug="prod", name="Production")
    handle.boundary().declare(
        scope=Scope(
            firm=ScopeNode(id=firm.id, slug=firm.slug),
            system=ScopeNode(id=system.id, slug=system.slug),
        ),
        tier="T2",
        knowledge_plane_location="customer",
        control_plane_location="customer",
        permitted_outbound_categories=["metadata_only"],
    )
    handle.close()

    def digest() -> str:
        return hashlib.sha256(store.read_bytes()).hexdigest()

    def run(*extra: str) -> tuple[int, str, str]:
        before = digest()
        result = CliRunner().invoke(
            app,
            [
                "map",
                ".",
                "--archetype",
                "platform",
                "--firm",
                firm.id,
                "--engagement",
                engagement.id,
                "--system",
                system.id,
                "--store",
                str(store),
                "--out",
                str(tmp_path / "out"),
                *extra,
            ],
        )
        captured = capfd.readouterr()
        printed = result.output + captured.out + captured.err + (result.stderr or "")
        return result.exit_code, printed, "changed" if digest() != before else before

    run.before = digest()  # type: ignore[attr-defined]
    return run


def _envelope(printed: str) -> dict[str, str]:
    """The error envelope out of a stream that also carries structured logs.

    `03` §6 sends every log line to stderr as JSON too, so the captured text is an
    envelope *and* a log stream. Scanning for the first object that **has an
    `error` key** rather than the first object at all: a run that logged before it
    declined would otherwise hand back a log line and the assertion would read as
    a missing envelope.
    """
    decoder = json.JSONDecoder()
    for index, character in enumerate(printed):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(printed[index:])
        except json.JSONDecodeError:
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return error
    raise AssertionError("no error envelope in the captured output:\n" + printed)


def test_a_platform_run_without_a_bundle_exits_4_with_the_export_command(
    cli: Callable[..., tuple[int, str, str]],
) -> None:
    """*Defect sentence.* Fails when the refusal stops naming the command a client
    must run, or stops being exit 4; matters because the operator on the other
    end of this message has no source tree and no idea what we want from them --
    `01` F8.3's whole point is that the metadata *is* the export, so the message
    has to say how to produce one; no other instrument catches the remediation
    half, because an exit code alone is a correct-looking refusal that leaves
    somebody stuck.
    """
    exit_code, output, after = cli("--json")
    assert exit_code == 4, output
    envelope = _envelope(output)
    assert envelope["code"] == "MAP_EXPORT_BUNDLE_MISSING"
    assert "--export-bundle" in envelope["message"]
    assert "force:source:retrieve" in envelope["hint"], "no export command in the remediation"
    assert after != "changed", "a declined run touched the store"


def test_a_bundle_path_that_does_not_exist_is_the_same_refusal(
    cli: Callable[..., tuple[int, str, str]], tmp_path: Path
) -> None:
    """A flag pointing at nothing is the same fact as no flag.

    *Defect sentence.* Fails when a mistyped `--export-bundle` produces a
    successful run over an empty index instead of a refusal; matters because that
    run would report a packaged platform with **no components in it** and exit 0
    -- a confident, wrong, empty map, which `01` §1.6 exists to forbid; no other
    instrument catches it, because every count is internally consistent and the
    artefacts are all written.
    """
    exit_code, output, after = cli("--json", "--export-bundle", str(tmp_path / "not-there"))
    assert exit_code == 4, output
    envelope = _envelope(output)
    assert envelope["code"] == "MAP_EXPORT_BUNDLE_MISSING"
    assert "does not exist" in envelope["message"]
    assert after != "changed"


def test_a_platform_run_with_a_bundle_maps_it(
    cli: Callable[..., tuple[int, str, str]], tmp_path: Path
) -> None:
    """The positive control, without which both refusals could be unconditional.

    A pair of tests that only ever assert exit 4 would pass just as well against
    a command that refused every platform run -- the shape of vacuous gate this
    repository has now found six times.
    """
    exit_code, output, _ = cli("--export-bundle", str(_BUNDLE), "--json")
    assert exit_code == 0, output
    payload = json.loads(output)
    assert payload["counts_by_kind"].get("metadata_component", 0) > 0
