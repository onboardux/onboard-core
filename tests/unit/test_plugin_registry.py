"""Registration, pack gating, tier gating and the run plan -- `03` §5.8.

*Defect sentence.* Fails when the registry admits an extractor it should not, or
excludes one without recording why; matters because the plan decides what a
client's map contains and `01` F13.5 makes a silent omission indistinguishable
from a family that was covered and found empty; no other instrument catches it
because the run report only shows what *did* run.
"""

import pytest
from adopt_map.context import Budget, ExtractorContext
from adopt_map.fileindex import FileIndex
from adopt_map.plugins import DEFAULT_ENABLED_PACKS, ExtractorRegistry
from adopt_map.schemas import ExtractorManifest, SurfaceFact
from pydantic import ValidationError

from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit


class _Fake:
    """A minimal extractor. Structural, like every real one."""

    def __init__(
        self,
        identifier: str,
        *,
        kinds: list[str] | None = None,
        pack: str = "common",
        archetypes: list[str] | None = None,
        capability: list[str] | None = None,
        applies: bool = True,
    ) -> None:
        self._manifest = ExtractorManifest(
            id=identifier,
            version="1.0.0",
            pack=pack,  # type: ignore[arg-type]
            archetypes=archetypes or [],  # type: ignore[arg-type]
            kinds=kinds or ["symbol"],  # type: ignore[arg-type]
            method="regex",
            requires_capability=capability or [],
        )
        self._applies = applies

    def manifest(self) -> ExtractorManifest:
        return self._manifest

    def applies_to(self, root: str) -> bool:
        del root
        return self._applies

    def extract(self, ctx: ExtractorContext) -> "list[SurfaceFact]":  # pragma: no cover
        del ctx
        return []


def _registry(
    *extractors: _Fake, packs: frozenset[str] = DEFAULT_ENABLED_PACKS
) -> ExtractorRegistry:
    registry = ExtractorRegistry(enabled_packs=packs)
    registry.register_all(extractors)
    return registry


def test_a_duplicate_extractor_id_is_refused() -> None:
    """Ids are the attribution key on every revision this build writes.

    Two extractors sharing one id make a bad pack unattributable, which is the
    rollback surface `03` §9 depends on.
    """
    registry = _registry(_Fake("common.a"))
    with pytest.raises(AdoptError) as caught:
        registry.register(_Fake("common.a"))
    assert caught.value.code is ErrorCode.MAP_EXTRACTOR_FAILED


def test_a_manifest_naming_a_kind_outside_the_closed_enum_cannot_be_built() -> None:
    """`01` F2.2: Build 1 never extends `IdentityKind`.

    The **first** refusal is the model's: `ExtractorManifest.kinds` is typed
    `list[IdentityKind]` against Build 0's closed `Literal`, so a manifest naming
    `failure_surface` does not construct. That is the strongest place for the
    rule to live -- an invalid manifest never exists to be passed anywhere.
    """
    with pytest.raises(ValidationError):
        _Fake("common.bad", kinds=["failure_surface"])


def test_the_registry_backstops_a_manifest_that_bypassed_validation() -> None:
    """And the registry checks anyway, for the manifest that skipped the model.

    `model_construct` builds a Pydantic model **without validation** -- it is how
    a deserializer, a fixture or a future agent-authored manifest could arrive
    holding a kind the enum does not carry. The registry's own check is the
    backstop for exactly that path, and it is worth having precisely because the
    model catches the ordinary case: a rule enforced in only one place is a rule
    with one way around it.
    """

    class _Bypassed(_Fake):
        def manifest(self) -> ExtractorManifest:
            return ExtractorManifest.model_construct(
                id="common.bypassed",
                version="1.0.0",
                pack="common",
                archetypes=[],
                kinds=["failure_surface"],
                method="regex",
                heavy=False,
                requires_capability=[],
                fallback=None,
                determinism="strict",
            )

    registry = ExtractorRegistry()
    with pytest.raises(AdoptError) as caught:
        registry.register(_Bypassed("common.bypassed"))
    assert caught.value.code is ErrorCode.MAP_EXTRACTOR_FAILED
    assert "failure_surface" in caught.value.message


def test_all_returns_manifest_id_order_regardless_of_registration_order() -> None:
    """`02` §7 obligation 3, kept by the framework rather than asked of extractors.

    A plan ordered by import order would make the fact sequence -- and therefore
    every artifact byte -- depend on which module loaded first.
    """
    registry = _registry(_Fake("common.z"), _Fake("common.a"), _Fake("common.m"))
    assert [e.manifest().id for e in registry.all()] == ["common.a", "common.m", "common.z"]


def test_a_disabled_pack_is_excluded_and_the_reason_is_recorded() -> None:
    """`01` F7.6 and F13.5: a skip is a stated reason, never a silent omission.

    The example pack is `ai`, and it used to be `web`. S1.4 flipped
    `extractors.web.enabled` on (`01` §9), which turned this case into an
    assertion that an *enabled* pack is excluded -- so the test failed for the
    right reason and names a still-disabled pack now. `ai` flips at S1.5's exit
    gate, and when it does this case moves again rather than being deleted:
    something must always be off, or `pack_disabled` stops being reachable.
    """
    registry = _registry(_Fake("ai.prompts", pack="ai"))
    assert registry.plan(archetype="web", root=".", tier="T2") == ()
    assert ("ai.prompts", "pack_disabled") in registry.skipped(archetype="web", root=".", tier="T2")


def test_the_web_pack_is_enabled_by_default_from_s1_4() -> None:
    """`01` §9: *"`extractors.web.enabled` | on from S1.4"*.

    *Defect sentence.* Fails when the S1.4 flag flip is reverted or a later sprint
    edits `DEFAULT_ENABLED_PACKS`; matters because a `web` pack that is registered
    but not enabled produces a run with zero endpoints and a `pack_disabled`
    reason rather than an error, which reads as "this repository has no routes";
    no other instrument catches it because every extractor test constructs its own
    registry with an explicit enabled set.
    """
    assert "web" in DEFAULT_ENABLED_PACKS
    assert "common" in DEFAULT_ENABLED_PACKS
    assert not {"ai", "data", "lowcode", "platform"} & DEFAULT_ENABLED_PACKS


def test_an_archetype_mismatch_is_excluded_with_its_reason() -> None:
    registry = _registry(_Fake("common.ai_only", archetypes=["ai"]))
    assert registry.plan(archetype="web", root=".", tier="T2") == ()
    assert ("common.ai_only", "archetype_mismatch") in registry.skipped(
        archetype="web", root=".", tier="T2"
    )


def test_a_capability_above_the_tier_is_skipped_with_tier_blocked() -> None:
    """`01` F13.5. A capability the boundary does not permit is refused, and the
    reason names the tier rather than looking like an empty family."""
    registry = _registry(_Fake("common.db", capability=["db_connection"]))
    assert registry.plan(archetype="web", root=".", tier="T1") == ()
    assert ("common.db", "tier_blocked") in registry.skipped(archetype="web", root=".", tier="T1")
    assert registry.plan(archetype="web", root=".", tier="T3") != ()


def test_an_extractor_that_does_not_apply_is_recorded_rather_than_dropped() -> None:
    registry = _registry(_Fake("common.absent", applies=False))
    assert registry.plan(archetype="web", root=".", tier="T2") == ()
    assert ("common.absent", "not_applicable") in registry.skipped(
        archetype="web", root=".", tier="T2"
    )


def test_the_shipped_common_pack_registers_without_a_manifest_error() -> None:
    """The real pack, through the real registry.

    A registry proven only against fakes is one that can reject every shipped
    extractor and still pass its own suite.
    """
    from adopt_extractors_common import pack

    registry = ExtractorRegistry()
    registry.register_all(pack())
    assert {extractor.manifest().id for extractor in registry.all()} == {
        "common.config",
        "common.ctags",
        "common.failure",
        "common.regex",
        "common.secrets",
        "common.stub_tree",
    }


def test_common_stub_is_not_in_the_registered_pack() -> None:
    """B1-CR-61. `common.stub` reads nothing and emits four fixed facts.

    Registering it would write an endpoint, a config key and a symbol into every
    client's identity set regardless of whether they exist -- the invention
    `01` §1.6 forbids. It stays importable for the S1.1 suite and out of `pack()`.
    """
    from adopt_extractors_common import pack

    assert "common.stub" not in {extractor.manifest().id for extractor in pack()}


def test_a_context_carries_no_scope_store_or_uri_builder() -> None:
    """`02` §7 obligations 2, 5 and 6, asserted as **absent fields**.

    This is the mechanism behind environment isolation: a fuzzed extractor cannot
    emit a production URI from a staging run because it has no field through
    which to name an environment, no builder to mint one, and no store to write
    one to.
    """
    ctx = ExtractorContext(
        root=".",
        index=FileIndex(
            root=".",
            files=(),
            discovered=0,
            sampled=False,
            skipped_large=0,
            skipped_binary=0,
            vcs_revision=None,
        ),
        budget=Budget(stage1_deadline=0.0, total_deadline=0.0),
        archetype="web",
    )
    fields = set(ExtractorContext.__slots__)
    assert not fields & {"scope", "resolved", "store", "writer", "build_uri", "confidence"}
    assert not hasattr(ctx, "scope")
