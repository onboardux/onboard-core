"""Concrete values satisfying `ports`' protocols.

The protocols say what assembly needs; these say it in one shipped shape, so the
CLI and every test build the same thing rather than each inventing a row object.
Plain frozen dataclasses: they carry no behaviour, because every rule that could
be got wrong lives in `sections` and `assemble` where it is tested once.

They are **not** generated models. A pack needs a revision's body alongside its
item's title, audiences and bound URIs, which is a join across four tables and
therefore no single canonical row -- and inventing a manifest table to hold the
join would be a second answer to what the store already knows.
"""

import datetime as _dt
from dataclasses import dataclass, field

__all__ = ["PackBoundary", "PackConflict", "PackGap", "PackIdentity", "PackKnowledge"]


@dataclass(frozen=True, slots=True)
class PackKnowledge:
    """One knowledge revision, joined to what a section needs to render it."""

    item_id: str
    revision_id: str
    title: str
    kind: str
    body_md: str
    verification: str | None
    created_at: _dt.datetime
    identity_uris: tuple[str, ...] = ()
    audiences: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackIdentity:
    """One identity, as the inventory lists it."""

    uri: str
    kind: str
    covered: bool


@dataclass(frozen=True, slots=True)
class PackGap:
    """One derived gap joined to its disposition, if a human recorded one."""

    uri: str
    kind: str
    gap_key: str
    reasons: tuple[str, ...] = ()
    status: str | None = None
    owner_actor_id: str | None = None
    note: str | None = None
    waived_until: _dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class PackConflict:
    """One open conflict, as the gap appendix lists it."""

    uri: str
    kind: str
    intent_revision_id: str | None
    detected_at: _dt.datetime


@dataclass(frozen=True, slots=True)
class PackBoundary:
    """The declared observability boundary, flattened for rendering."""

    tier: str
    declared_at: _dt.datetime
    contractual: bool
    covered: str | None = None
    not_covered: str | None = None
    permitted_outbound_categories: tuple[str, ...] = field(default_factory=tuple)
