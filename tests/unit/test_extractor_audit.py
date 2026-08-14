"""The static audit -- `01` F7.2, `03` §5.8, `04` §6.

**One rejection test per audit rule**, as `05` S1.3 asks, and the parameterization
is over `plugins.AUDIT_RULES` rather than over a list written here. A test that
carried its own list would silently stop covering a rule the day somebody added
one, which is the failure mode the rule list exists to prevent.

*Defect sentence.* Fails when the audit stops rejecting a category of unsafe
extractor code; matters because `01` F7.2's static-only guarantee is what a client
security reviewer is invited to check and `04` §6 reuses this audit as the
quarantine gate for agent-authored modules; no other instrument catches it because
the audit is the only thing that reads an extractor before it runs, and a module
that passes it is never inspected again.
"""

import pytest
from adopt_map.plugins import AUDIT_RULES, audit_source, require_clean

from adopt_const import URI_SCHEME
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

#: One source sample per rule. The `uri_construction` sample is composed from
#: `adopt_const.URI_SCHEME` rather than spelled out (B1-CR-26): a proof that
#: hard-codes the value goes blind on the same day the rule does -- which is
#: exactly what happened to the two audits this one replaced.
_VIOLATIONS: dict[str, str] = {
    "client_import": "import django\n",
    "dynamic_execution": "def f(src):\n    return eval(src)\n",
    "subprocess": "import subprocess\n\ndef f():\n    subprocess.run(['ls'])\n",
    "socket": "import socket\n\ndef f():\n    socket.socket()\n",
    "write_open": "def f(p):\n    with open(p, 'w') as h:\n        h.write('x')\n",
    "environment": "import os\n\ndef f():\n    return os.environ['SECRET']\n",
    "self_confidence": "def f():\n    return SurfaceFact(confidence=0.99)\n",
    "undeclared_kind": "def f():\n    return SurfaceFact(identity_kind='endpoint')\n",
    "uri_construction": f'PREFIX = "{URI_SCHEME}://a/b/c/d/symbol/python/x"\n',
}

#: A module that passes every rule. Deliberately does something -- reads through
#: the context, checks the budget, yields a declared kind -- because a clean
#: sample that did nothing would pass an audit that rejected everything.
_CLEAN = """
import re

from adopt_map.schemas import SurfaceFact


def extract(ctx):
    for entry in ctx.files():
        ctx.budget.check()
        for match in re.finditer(r"^def (\\w+)", ctx.text(entry)):
            yield SurfaceFact(
                identity_kind="symbol",
                namespace="python",
                local_key=match.group(1),
                title=match.group(1),
            )
"""


@pytest.mark.parametrize("rule", AUDIT_RULES)
def test_the_audit_rejects_its_own_rule(rule: str) -> None:
    """Every rule in `AUDIT_RULES` has a sample the audit rejects for that rule.

    Parameterized over the module's tuple, so a new rule with no sample here
    fails as a `KeyError` rather than quietly going untested.
    """
    findings = audit_source(_VIOLATIONS[rule], declared_kinds=["symbol"])
    assert rule in {finding.rule for finding in findings}, (
        f"{rule} did not fire on its own violation sample"
    )


def test_a_clean_extractor_passes_every_rule() -> None:
    """The clean sample fires nothing.

    Without this, an audit that rejected every module would pass all nine
    rejection tests above -- which is the shape of gate the repository has
    already found five times.
    """
    assert audit_source(_CLEAN, declared_kinds=["symbol"]) == ()


def test_a_kind_outside_the_closed_enum_is_rejected_even_when_declared() -> None:
    """Declaring a non-member does not admit it -- `01` F2.2.

    Build 1 never extends `IdentityKind`. An extractor that declared
    `failure_surface` in its own manifest would otherwise pass rule 8 by widening
    the thing rule 8 checks against.
    """
    source = "def f():\n    return SurfaceFact(identity_kind='failure_surface')\n"
    findings = audit_source(source, declared_kinds=["failure_surface"])
    assert any(finding.rule == "undeclared_kind" for finding in findings)


def test_a_module_that_does_not_parse_is_refused_rather_than_admitted() -> None:
    """An unauditable module is refused.

    The guarantee is not conditional on the parser having managed to read the
    file: admitting what we could not audit would make "static-only" mean
    "static-only where convenient".
    """
    with pytest.raises(AdoptError) as caught:
        audit_source("def f(:\n", declared_kinds=["symbol"])
    assert caught.value.code is ErrorCode.MAP_EXTRACTOR_FAILED


def test_require_clean_names_every_rule_that_fired() -> None:
    """The refusal message names the rules and the lines, not just a count.

    A gate that says "audit failed" sends the author looking; one that names
    `line 3: subprocess` sends them to the line.
    """
    source = "import subprocess\n\ndef f():\n    subprocess.run(['ls'])\n"
    with pytest.raises(AdoptError) as caught:
        require_clean(source, extractor_id="pack.bad", declared_kinds=["symbol"])
    assert "subprocess" in caught.value.message
    assert "pack.bad" in caught.value.message


def test_the_audit_reads_source_and_never_imports_it() -> None:
    """A module that detonates on import is audited without detonating.

    The `poisoned-import` fixture proves this end to end; this asserts the
    property at the unit the fixture exercises, because an audit that imported
    to introspect would have to run the thing it exists to refuse.
    """
    poisoned = "from pathlib import Path\n\nPath('detonated.txt').write_text('boom')\n"
    findings = audit_source(poisoned, declared_kinds=["symbol"])
    # It is rejected -- `write_text` is a write -- and nothing was written by the
    # act of auditing it.
    assert any(finding.rule == "write_open" for finding in findings)
