"""The file-state snapshot: scoped, wholesale, and honest about having none.

The weakest signal in Build 6, feeding exactly one informational class. These
tests pin the three ways it could quietly mislead: reporting a measurement it
never took, leaking one scope's tree into another's, and letting deleted files
haunt a snapshot forever.
"""

import datetime as _dt
from pathlib import Path

import pytest
from adopt_map.filestate import FileState, changed_paths, hash_file

from adopt_store.annex.filestate import open_file_state

pytestmark = pytest.mark.unit

_NOW = _dt.datetime(2026, 8, 26, 12, 0, tzinfo=_dt.UTC)
_SCOPE = "northwind/acme-erp/orders-api/prod"


def test_no_snapshot_reads_as_none_not_as_empty(tmp_path: Path) -> None:
    """*Fails when* an absent snapshot returns `{}`. *Matters because* `{}` and
    `None` take different branches downstream: empty means "nothing changed" and
    `None` means "we cannot tell". Printing the first for the second is a
    measurement nobody took, and the first refresh of every store is exactly
    that case. *No other instrument catches it* because both are falsy."""
    with open_file_state(tmp_path / "store.db") as records:
        assert records.load(_SCOPE) is None


def test_a_snapshot_round_trips(tmp_path: Path) -> None:
    with open_file_state(tmp_path / "store.db") as records:
        written = records.replace(
            _SCOPE, [FileState(path="src/api.py", sha256="aaa")], observed_at=_NOW
        )
        assert written == 1
        assert records.load(_SCOPE) == {"src/api.py": "aaa"}


def test_replacing_a_snapshot_drops_files_that_are_gone(tmp_path: Path) -> None:
    """*Fails when* replacement degrades to a merge. *Matters because* a deleted
    file would stay in the snapshot forever, comparing equal on every future run
    -- so if the path were ever recreated with different content, the change
    would be measured against a tree that no longer exists."""
    with open_file_state(tmp_path / "store.db") as records:
        records.replace(
            _SCOPE,
            [
                FileState(path="src/api.py", sha256="aaa"),
                FileState(path="src/old.py", sha256="bbb"),
            ],
            observed_at=_NOW,
        )
        records.replace(_SCOPE, [FileState(path="src/api.py", sha256="aaa")], observed_at=_NOW)

        assert records.load(_SCOPE) == {"src/api.py": "aaa"}


def test_one_scopes_snapshot_does_not_reach_another(tmp_path: Path) -> None:
    """*Fails when* the scope key is dropped. *Matters because* one store can
    hold several environments: staging and prod are different working trees, and
    a shared key would make each refresh report the other's tree as wholly
    rewritten."""
    with open_file_state(tmp_path / "store.db") as records:
        records.replace(_SCOPE, [FileState(path="src/api.py", sha256="aaa")], observed_at=_NOW)
        records.replace(
            "northwind/acme-erp/orders-api/staging",
            [FileState(path="src/api.py", sha256="zzz")],
            observed_at=_NOW,
        )

        assert records.load(_SCOPE) == {"src/api.py": "aaa"}


# -- the comparison ---------------------------------------------------------


def test_no_previous_snapshot_yields_no_changed_paths() -> None:
    assert changed_paths(None, {"src/api.py": "aaa"}) == frozenset()


def test_only_files_present_in_both_and_differing_are_changed() -> None:
    """*Fails when* added or deleted files are reported as content changes.
    *Matters because* both already have an actionable class -- a new file's
    identities are UNBOUND_NEW and a deleted file's are BINDING_DEAD -- and
    reporting the file too would put a second, unactionable entry beside every
    real one."""
    previous = {"kept.py": "aaa", "edited.py": "bbb", "deleted.py": "ccc"}
    current = {"kept.py": "aaa", "edited.py": "changed", "added.py": "ddd"}

    assert changed_paths(previous, current) == frozenset({"edited.py"})


def test_hashing_is_over_bytes_and_survives_an_unreadable_file(tmp_path: Path) -> None:
    """*Fails when* an unreadable file raises out of the walk. *Matters because*
    a file vanishing between the walk and the hash is a race, and a refresh that
    died of it would lose the real changes it had already found."""
    real = tmp_path / "a.txt"
    real.write_bytes(b"hello")

    assert hash_file(real) == hash_file(real)
    assert hash_file(tmp_path / "missing.txt") is None
