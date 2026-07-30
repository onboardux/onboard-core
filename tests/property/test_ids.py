"""Id generation: registered prefixes, and total ordering by creation.

Defect sentences:

* *Fails when* `new_id` mints an id whose prefix is not in the contracts §1.1
  registry, or whose ULID component cannot be parsed back. *Matters because* a
  typo'd prefix produces an id that looks valid, joins to nothing, and is
  indistinguishable from data loss months later. *No other instrument catches
  it because* the prefix is a runtime string, invisible to the type checker.
* *Fails when* two ids minted in the same millisecond do not sort in creation
  order. *Matters because* export row ordering is deterministic by primary key;
  if ids are not totally ordered, two exports of the same store differ and G0 --
  the whole portability promise -- fails intermittently rather than never.
* *No other instrument catches it because* the collision window is sub-
  millisecond and only a tight loop reaches it.
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_obs import PREFIX_REGISTRY, UnknownPrefixError, new_id, split_id

REGISTERED = sorted(PREFIX_REGISTRY)


@pytest.mark.property
@given(prefix=st.sampled_from(REGISTERED), suffixed=st.booleans())
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_prefixed_id_round_trips_through_split(prefix: str, suffixed: bool) -> None:
    minted = new_id(f"{prefix}_" if suffixed else prefix)
    parsed_prefix, ulid = split_id(minted)

    assert parsed_prefix == prefix
    assert minted == f"{prefix}_{ulid}"


@pytest.mark.property
@given(count=st.integers(min_value=2, max_value=500))
@settings(max_examples=25, deadline=None)
def test_ids_minted_in_one_burst_are_strictly_monotonic(count: int) -> None:
    """A burst is the case that matters: it lands inside one millisecond."""
    minted = [new_id("krev") for _ in range(count)]

    assert minted == sorted(minted), "ids are not lexicographically ordered by creation"
    assert len(set(minted)) == count, "a burst produced a duplicate id"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expectation"),
    [
        ("firm", "accepted -- registered, bare"),
        ("firm_", "accepted -- registered, trailing underscore"),
        ("ag", "accepted -- runtime annex prefixes are registered too"),
        ("run", "accepted -- the log and trace correlation prefix"),
        ("frim", "rejected -- transposed characters"),
        ("FIRM", "rejected -- registry is lowercase"),
        ("", "rejected -- empty"),
        ("_", "rejected -- separator only"),
        ("knowledge_item", "rejected -- table name, not the registered prefix"),
    ],
)
def test_prefix_registry_is_a_closed_vocabulary(value: str, expectation: str) -> None:
    if expectation.startswith("accepted"):
        assert split_id(new_id(value))[0] == value.rstrip("_")
        return
    with pytest.raises(UnknownPrefixError) as raised:
        new_id(value)
    # The message must name the offending prefix: a rejection that does not say
    # what was rejected sends the reader to the source.
    assert repr(value) in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "malformed",
    [
        "firm",
        "firm_",
        "firm_TOOSHORT",
        "firm_01KYSSD7R2AY2W9Y2X1PHKHG1DEXTRA",
        "firm_01KYSSD7R2AY2W9Y2X1PHKHG1I",
        "01KYSSD7R2AY2W9Y2X1PHKHG1D",
    ],
    ids=["no-separator", "empty-ulid", "too-short", "too-long", "illegal-char", "no-prefix"],
)
def test_split_id_rejects_malformed_ids(malformed: str) -> None:
    with pytest.raises((ValueError, UnknownPrefixError)):
        split_id(malformed)
