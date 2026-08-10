"""An artefact that cannot find `schema/` says so, instead of finding nothing.

*Fails when* a build ships without the schema assets and the code carries on as
though there were simply no work to do. *Matters because* that is precisely what
shipped: `adopt_store.api`, `adopt_schema.manifest` and `adopt_store.annex` each
walked `parents[N]` from `__file__` to a checkout, which from an installed wheel
lands on the environment root; the migrations glob then found nothing, reported
nothing pending, and `open_store` created an empty database whose first query
failed with `no such table: firm` -- a symptom four layers below its cause, and
phrased as if the store were at fault (CR-53). *No other instrument catches it
because* every test in this suite runs against an editable install, where the
walk still lands in the checkout and every one of these paths is correct.

The wheel-level twin of these tests is `scripts/packaged_artifact.py`, which
installs a built artefact into a clean environment and runs the journey. Both are
needed and neither substitutes: this file pins the *behaviour* when assets are
absent, that script proves they are *present* in what we ship.
"""

from pathlib import Path

import pytest

from adopt_obs import AdoptError, ErrorCode
from adopt_schema.assets import ASSETS_ROOT_ENV, assets_root, schema_dir
from adopt_schema.emitters import sqlite as sqlite_emitter
from adopt_schema.manifest import load_manifest
from adopt_schema.migrate import new_migration, pending


def _assets_tree(root: Path) -> Path:
    """A directory shaped like the repository root: it *contains* `schema/`."""
    directory = root / "schema" / "migrations" / "sqlite"
    directory.mkdir(parents=True)
    (directory / "0001__init_v3.sql").write_text(
        sqlite_emitter.emit(load_manifest()), encoding="utf-8", newline="\n"
    )
    return root


@pytest.mark.unit
def test_neither_bundled_nor_checkout_raises_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch that used to be a wrong answer instead of an error.

    Both candidates are pointed at directories holding no `schema/`, which is
    the shape of an installed wheel built without its package data.
    """
    monkeypatch.delenv(ASSETS_ROOT_ENV, raising=False)
    monkeypatch.setattr("adopt_schema.assets._BUNDLED", tmp_path / "absent")
    monkeypatch.setattr("adopt_schema.assets._CHECKOUT", tmp_path / "also-absent")

    with pytest.raises(AdoptError) as raised:
        assets_root()
    assert raised.value.code is ErrorCode.SCHEMA_ASSETS_MISSING
    # The message must name both places looked, because "not found" without
    # "where" is what sends someone reading `parents[4]` by hand.
    assert "absent" in str(raised.value)


@pytest.mark.unit
def test_the_bundled_copy_wins_over_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence, and it is not arbitrary.

    A wheel's `parents[4]` walk leaves `site-packages` and can strike any
    directory at all -- in the run that found this defect it struck
    `AppData/Local/Temp`. If such a directory happened to hold a `schema/`, a
    checkout-first order would read a stranger's manifest in preference to the
    one built into the artefact.
    """
    monkeypatch.delenv(ASSETS_ROOT_ENV, raising=False)
    bundled = _assets_tree(tmp_path / "bundled")
    stranger = _assets_tree(tmp_path / "stranger")
    monkeypatch.setattr("adopt_schema.assets._BUNDLED", bundled)
    monkeypatch.setattr("adopt_schema.assets._CHECKOUT", stranger)

    assert assets_root() == bundled


@pytest.mark.unit
def test_the_checkout_is_used_when_there_is_no_bundled_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source checkout keeps working -- the case every developer runs."""
    monkeypatch.delenv(ASSETS_ROOT_ENV, raising=False)
    checkout = _assets_tree(tmp_path / "checkout")
    monkeypatch.setattr("adopt_schema.assets._BUNDLED", tmp_path / "absent")
    monkeypatch.setattr("adopt_schema.assets._CHECKOUT", checkout)

    assert assets_root() == checkout
    assert schema_dir() == checkout / "schema"


@pytest.mark.unit
def test_an_override_naming_the_wrong_level_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ADOPT_SCHEMA_ASSETS_ROOT` takes the *parent* of `schema/`.

    The likely mistake is pointing it at `schema/` itself, which would otherwise
    resolve to `schema/schema/` and fail later as a missing migration directory.
    """
    root = _assets_tree(tmp_path)
    monkeypatch.setenv(ASSETS_ROOT_ENV, str(root / "schema"))

    with pytest.raises(AdoptError) as raised:
        assets_root()
    assert raised.value.code is ErrorCode.SCHEMA_ASSETS_MISSING
    assert "contains" in str(raised.value.hint)


@pytest.mark.unit
def test_an_override_wins_over_both_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = _assets_tree(tmp_path / "override")
    monkeypatch.setattr("adopt_schema.assets._BUNDLED", _assets_tree(tmp_path / "bundled"))
    monkeypatch.setenv(ASSETS_ROOT_ENV, str(override))

    assert assets_root() == override


@pytest.mark.unit
def test_a_missing_migrations_directory_is_a_failure_not_an_empty_list(
    tmp_path: Path,
) -> None:
    """The silent glob, pinned.

    `Path.glob` on a directory that does not exist yields nothing, so this
    reported zero pending migrations for an artefact carrying none at all.
    """
    with pytest.raises(AdoptError) as raised:
        pending(tmp_path, "sqlite", 0)
    assert raised.value.code is ErrorCode.SCHEMA_ASSETS_MISSING


@pytest.mark.unit
def test_an_empty_migrations_directory_is_also_a_failure(tmp_path: Path) -> None:
    """One packaging accident further along than the missing directory.

    Every dialect ships at least the initial migration, so on the read path an
    empty directory can only mean the assets are incomplete.
    """
    (tmp_path / "schema" / "migrations" / "sqlite").mkdir(parents=True)

    with pytest.raises(AdoptError) as raised:
        pending(tmp_path, "sqlite", 0)
    assert raised.value.code is ErrorCode.SCHEMA_ASSETS_MISSING


@pytest.mark.unit
def test_scaffolding_the_first_migration_still_works_on_an_empty_directory(
    tmp_path: Path,
) -> None:
    """The exemption the two tests above carve out, held open deliberately.

    *Fails when* the emptiness check is moved from the read path into
    `_ordered_files`, where it would also reject the directory `new_migration`
    has just created. *Matters because* that would make the first migration of
    any new dialect unwritable -- a gate blocking the work it exists to protect.
    """
    created = new_migration(tmp_path, "sqlite", "add_probe_retry_budget")
    assert created.name == "0001__add_probe_retry_budget.sql"
    assert created.is_file()
