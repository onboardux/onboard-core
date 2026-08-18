"""`quarantine-audit`: the `04` §6 step-1 audit still rejects, and still admits.

`04` §6's B1-CR-26 note is explicit about what proves this gate:

> The `--self-test` for this audit must plant a literal built from `URI_SCHEME`
> and require rejection.

and about why a weaker proof is worthless: Build 0 **CR-06** ratified the scheme
as `onboard-v1://` five days after this pack was written, so an audit looking for
the proposed `adopt://` scanned for a string that can never appear. Generated code
minting a forged URI would have passed every rule and reached quarantine as clean.
**A proof that hard-codes the value goes blind on the same day the rule does**, so
the literal below is composed from `adopt_const.URI_SCHEME` at plant time and
follows the constant the day `onboard-v2` is ratified.

**This is not `plant_violation.py`, and the difference is the subject.** That
script plants into the *source tree* to prove an import-linter contract over files
this repository ships. The subject here is text that has never been a file and
never will be one unless it passes -- so the plant is a synthetic `GlueOutput`
driven through the real `quarantine()`, and the assertion includes that **no file
was written**, which is the half of `04` §6 step 1 a findings-only check cannot see.

**Every rejection case carries a positive control.** A clean module is driven
through the same call and required to reach quarantine. Without it, an audit that
rejected *everything* -- a `return True` in the wrong place, a parse failure
mistaken for a finding -- would pass every rejection assertion perfectly. That is
this build's own recurring finding (`00` §6 B1-CR-69, and S1.6's FOV line 2),
applied to the instrument it keeps catching.

Usage:
    python scripts/quarantine_audit.py --self-test
"""

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
for _package in ("adopt-map", "adopt-const", "adopt-model", "adopt-obs"):
    sys.path.insert(0, str(REPO_ROOT / "packages" / _package / "src"))

from adopt_map.agent_gate import GateDecision  # noqa: E402
from adopt_map.quarantine import QuarantinePaths, quarantine  # noqa: E402
from adopt_map.schemas.agent import GlueOutput  # noqa: E402
from adopt_map.schemas.surface import ExtractorManifest  # noqa: E402

from adopt_const import URI_SCHEME  # noqa: E402

#: A module that does nothing an extractor may not do. The positive control.
_CLEAN_MODULE: Final[str] = '''
"""A minimal, audit-clean extractor."""


class Extractor:
    def manifest(self):
        return None

    def applies_to(self, root):
        return False

    def extract(self, ctx):
        return iter(())


EXTRACTOR = Extractor
'''


def _forged_uri_module() -> str:
    """The planted violation: a URI literal composed from the constant."""
    forged = f"{URI_SCHEME}://northwind/acme-erp/orders-api/prod/endpoint/http/x"
    return f'{_CLEAN_MODULE}\n_FORGED = "{forged}"\n'


def _manifest() -> ExtractorManifest:
    return ExtractorManifest(
        id="agent.forged.routes",
        version="0.1.0",
        pack="common",
        archetypes=["web"],
        kinds=["endpoint"],
        method="regex",
    )


def _drive(source: str, adopt_dir: Path) -> object:
    output = GlueOutput(
        outcome="authored",
        extractor_id="agent.forged.routes",
        module_source=source,
        test_source=None,
        manifest=_manifest(),
    )
    return quarantine(
        output,
        paths=QuarantinePaths(adopt_dir=adopt_dir),
        root=adopt_dir,
        samples=(),
        decision=GateDecision(allowed=True),
        prompt_ref="map-glue-001/v1",
        adapter=None,
        cost_usd=0.0,
    )


def self_test() -> int:
    """Plant, require rejection with no file written, then require the clean one through."""
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        adopt_dir = Path(tmp) / ".adopt"
        outcome = _drive(_forged_uri_module(), adopt_dir)
        module_path = QuarantinePaths(adopt_dir=adopt_dir).module("agent.forged.routes")

        if outcome.status != "rejected":  # type: ignore[attr-defined]
            failures.append(
                f"a module carrying a {URI_SCHEME} literal reached "
                f"{outcome.status!r} instead of 'rejected'"  # type: ignore[attr-defined]
            )
        if "uri_construction" not in outcome.audit_rules:  # type: ignore[attr-defined]
            failures.append(
                "the rejection did not name `uri_construction`; it fired on "
                f"{outcome.audit_rules!r}"  # type: ignore[attr-defined]
            )
        if module_path.exists():
            failures.append(
                f"{module_path} was written. `04` §6 step 1 is 'DISCARD ... No file "
                "written': a rejected module that exists on disk is one a reviewer "
                "can approve."
            )

    with tempfile.TemporaryDirectory() as tmp:
        adopt_dir = Path(tmp) / ".adopt"
        control = _drive(_CLEAN_MODULE, adopt_dir)
        if control.status != "quarantined":  # type: ignore[attr-defined]
            failures.append(
                "the positive control did not reach quarantine "
                f"({control.status!r}). Without it, an audit that rejected "  # type: ignore[attr-defined]
                "everything would pass every assertion above."
            )

    if failures:
        print("quarantine-audit self-test FAILED:\n")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"quarantine-audit: a planted {URI_SCHEME} literal is rejected as "
        "`uri_construction` with no file written, and a clean module still reaches "
        "quarantine"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Plant a URI literal built from URI_SCHEME and require rejection.",
    )
    args = parser.parse_args(argv)
    if not args.self_test:
        parser.error("this gate has one mode: --self-test")
    return self_test()


if __name__ == "__main__":
    sys.exit(main())
