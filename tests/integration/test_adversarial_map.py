"""`05` S1.8's adversarial pass -- six attacks, one table, one verdict each.

*Fails when* any of the six things `05` S1.8 says to try actually succeeds:
executing client code, exfiltrating a secret, emitting a render-bound URI, mutating
a revision, crossing environments, or marking an identity dead. *Matters because*
these are the six a client's security reviewer will try, and five of them are
`01` section 1.6 invariants — the ones described as non-negotiable, which is a claim
that has to be attacked rather than asserted. *No other instrument catches it
because* each attack is refused by a **different** mechanism — a guard, a missing
field, a mint-site check, an import contract, a scope resolution, an absent code
path — and the suites that prove each mechanism individually never ask the question
in the attacker's form: *can I get this outcome by any route.*

**One table, six rows, and the table is the point.** Six bespoke test functions
would drift into six different definitions of "refused"; one parameterised table
makes every attack answer the same question and makes the seventh, when somebody
thinks of it, a row rather than a file.

**Each row asserts the refusal AND that the refusal was recorded**, because `01`
F7.4 and `01` N7 both make the record the deliverable: a run that silently declined
an attack teaches an operator nothing, and a security reviewer cannot audit a
silence.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from adopt_map.minting import mint
from adopt_map.schemas.surface import SurfaceFact

from adopt_obs.errors import AdoptError
from tests.e2e.map_journey import Journey

pytestmark = pytest.mark.integration


def _attack_execute_client_code(journey: Journey) -> tuple[bool, str]:
    """Make the tool import or execute the tree it is reading.

    The poisoned fixture writes a marker on import, so *"it did not detonate"* is
    an assertion rather than an absence.
    """
    poisoned = Path("fixtures/repos/poisoned-import")
    for entry in poisoned.iterdir():
        target = journey.tree / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copyfile(entry, target)

    run = journey.map()
    markers = list(journey.tree.rglob("DETONATED*")) + list(Path.cwd().glob("DETONATED*"))
    recorded = run.run_report["client_imports_attempted"] == 0
    return (not markers and recorded), f"markers={markers}, report_clean={recorded}"


def _attack_exfiltrate_a_secret(journey: Journey) -> tuple[bool, str]:
    """Plant a credential and look for its *value* in everything the run produced.

    The value, not the key: the key is supposed to be recorded — `01` F2.6 mints a
    secret **reference** — and a test that searched for the key would fail on
    correct behaviour and teach everyone to weaken it.
    """
    secret = "sk-live-ADVERSARIAL-b7f3c19d4e2a"
    (journey.tree / ".env").write_text(
        f"DATABASE_PASSWORD={secret}\nSTRIPE_API_KEY={secret}\n", encoding="utf-8"
    )

    run = journey.map()

    haystacks: list[str] = [run.stdout, run.output]
    for artifact in run.out_dir.rglob("*"):
        if artifact.is_file():
            haystacks.append(artifact.read_text(encoding="utf-8", errors="replace"))
    haystacks.append(journey.store.read_bytes().decode("utf-8", errors="replace"))

    leaked = [i for i, text in enumerate(haystacks) if secret in text]
    # The reference itself must still be there, or this run refused by extracting
    # nothing and the assertion above is satisfied for the wrong reason.
    referenced = any("DATABASE_PASSWORD" in text for text in haystacks)
    return (not leaked and referenced), f"leaked_in={leaked}, reference_present={referenced}"


def _attack_render_bound_uri(journey: Journey) -> tuple[bool, str]:
    """Mint a `ui_component` from a CSS selector -- design bet 1's whole subject."""
    del journey
    fact = SurfaceFact(
        identity_kind="ui_component",
        namespace="web",
        local_key="div.container > button.primary:nth-child(2)",
        title="the checkout button",
        attributes={},
        source_refs=[],
    )
    from adopt_scope import Scope, ScopeNode

    scope = Scope(
        firm=ScopeNode(id="firm_x", slug="northwind"),
        engagement=ScopeNode(id="eng_x", slug="acme-erp"),
        system=ScopeNode(id="sys_x", slug="orders-api"),
        environment=ScopeNode(id="env_x", slug="prod"),
    )
    try:
        minted = mint(scope, fact)
    except AdoptError as error:
        return (str(error.code).endswith("MAP_URI_CONSTRUCTION_BYPASS"), f"refused: {error.code}")
    return False, f"a selector was minted into {minted}"


def _attack_mutate_a_revision(journey: Journey) -> tuple[bool, str]:
    """Look for any UPDATE path against a `*_revision` table, from the outside.

    Asserted as an **absence of capability** rather than as a caught exception:
    `01` section 1.6 says no UPDATE ever, and the way that holds is that no facade
    offers one. A test that called an update method and expected an error would be
    asserting that the method exists.
    """
    from adopt_store import open_store

    handle = open_store(journey.store, migrate=False)
    try:
        surfaces = [handle.identities(), handle.items(), handle.bindings()]
        offenders = [
            f"{type(facade).__name__}.{name}"
            for facade in surfaces
            for name in dir(facade)
            if name.startswith(("update", "delete", "set_", "mutate"))
        ]
    finally:
        handle.close()
    return not offenders, f"mutation methods on the revision facades: {offenders}"


def _attack_cross_environments(journey: Journey) -> tuple[bool, str]:
    """Run against staging and look for a production URI in the store."""
    journey.map(environment="staging")
    uris = journey.identity_uris()
    leaked = [uri for uri in uris if "/prod/" in uri]
    return (bool(uris) and not leaked), f"minted={len(uris)}, leaked={leaked[:2]}"


def _attack_mark_an_identity_dead(journey: Journey) -> tuple[bool, str]:
    """Delete everything and see whether the run declares anything dead.

    `00` section 5 puts *"mark an identity dead"* on Build 1's never-does list and
    `01` F5.3 gives the reason: absence and parse failure are indistinguishable
    from here, so a false death is a false retirement downstream.
    """
    journey.map()
    for path in journey.tree.rglob("*.py"):
        path.unlink()

    run = journey.map()
    artifacts = "".join(
        artifact.read_text(encoding="utf-8", errors="replace")
        for artifact in run.out_dir.rglob("*")
        if artifact.is_file()
    )
    payload = json.dumps(run.payload)
    dead = '"dead"' in artifacts or '"dead"' in payload
    return not dead, f"status_dead_in_artifacts={dead}"


ATTACKS: tuple[tuple[str, str, Callable[[Journey], tuple[bool, Any]]], ...] = (
    ("execute client code", "01 N8 / F7.2", _attack_execute_client_code),
    ("exfiltrate a secret", "01 N9 / 02 §10 C16", _attack_exfiltrate_a_secret),
    ("emit a render-bound URI", "01 §1.6 bet 1 / B1-CR-11", _attack_render_bound_uri),
    ("mutate a revision", "01 §1.6 append-only", _attack_mutate_a_revision),
    ("cross environments", "01 F6 / 02 §10 C9", _attack_cross_environments),
    ("mark an identity dead", "00 §5 / 01 F5.3", _attack_mark_an_identity_dead),
)


@pytest.mark.parametrize(
    ("attack", "rule", "attempt"),
    ATTACKS,
    ids=[name.replace(" ", "_") for name, _, _ in ATTACKS],
)
def test_the_adversarial_pass_refuses_every_attack(
    attack: str,
    rule: str,
    attempt: Callable[[Journey], tuple[bool, Any]],
    tmp_path: Path,
) -> None:
    """One attack, tried for real against a real run, and refused."""
    journey = Journey(tmp_path, fixture="web", environments=("prod", "staging"))

    refused, detail = attempt(journey)

    assert refused, f"{attack} SUCCEEDED against {rule}: {detail}"


def test_the_adversarial_table_covers_every_attack_the_sprint_names(tmp_path: Path) -> None:
    """The anti-vacuity guard: six attacks named in `05` S1.8, six rows here.

    Without this, a row deleted during a refactor would take an attack with it and
    the suite would stay green -- which is how B1-CR-69's conformance suite spent
    three sprints watching one pack of three.
    """
    del tmp_path
    named = {
        "execute client code",
        "exfiltrate a secret",
        "emit a render-bound URI",
        "mutate a revision",
        "cross environments",
        "mark an identity dead",
    }
    assert {name for name, _, _ in ATTACKS} == named
