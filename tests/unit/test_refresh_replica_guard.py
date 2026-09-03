"""`adopt refresh` will not write canon into a store `adopt pull` maintains.

*Fails when* the guard stops firing, or fires against an ordinary field store.
*Matters because* every write `refresh` makes is canon — retirement revisions,
change events, staled bindings, a review batch — and a replica is replaced
wholesale by the next `adopt pull`. So a refresh against a replica does not
merely put the work in the wrong place: it puts it somewhere that gets deleted,
with nothing left to say it ever happened. R9 makes the plane the sole writer of
an operated system's canon, and `adopt ci-sense` is the verb for sensing one.
*No other instrument catches it because* the refresh **succeeds**: it writes a
correct-looking batch, exits 0 or 4 exactly as it should, and the loss happens
later and silently in a different command. Nothing downstream can distinguish a
replica whose review batch was pulled away from a replica that never had one.

The negative control is the point of the second test. A guard that refused every
store would also pass the first test, and would break the local loop entirely.
"""

import datetime as _dt
from pathlib import Path
from typing import Final

import pytest

from adopt_cli.commands._refresh_support import refuse_if_replica
from adopt_cli.replica import ReplicaMarker, write_marker
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit

_AT: Final[_dt.datetime] = _dt.datetime(2026, 8, 28, 11, 0, 0, tzinfo=_dt.UTC)
_SYSTEM: Final[str] = "sys_orders"
_PLANE: Final[str] = "https://plane.example"


def _marker() -> ReplicaMarker:
    return ReplicaMarker(
        plane_url=_PLANE,
        system_id=_SYSTEM,
        pulled_at=_AT,
        bundle_sha256="a" * 64,
    )


def test_a_replica_store_is_refused_and_the_refusal_names_ci_sense(tmp_path: Path) -> None:
    store = tmp_path / "store.db"
    store.write_bytes(b"")
    write_marker(store, _marker())

    with pytest.raises(AdoptError) as raised:
        refuse_if_replica(store)

    error = raised.value
    assert error.code is ErrorCode.REFRESH_TARGET_IS_REPLICA
    # The system and its plane, so an operator knows *which* replica this is.
    assert _SYSTEM in error.message
    assert _PLANE in error.message
    # The hint has to name the verb that does work here, or the refusal only
    # tells somebody to stop without telling them what to do instead.
    assert "ci-sense" in (error.hint or "")


def test_an_ordinary_field_store_is_not_refused(tmp_path: Path) -> None:
    """The negative control: the local loop still works.

    Without this, a guard that refused unconditionally would pass the test
    above while breaking `adopt refresh` for every unmanaged store — which is
    every store in the free product.
    """
    store = tmp_path / "store.db"
    store.write_bytes(b"")

    refuse_if_replica(store)  # must not raise
