"""Export → import → export is byte-identical, whatever the rows contain.

*Fails when* a value's rendering depends on anything but the value: a `json`
column whose keys re-serialize in insertion order, a timestamp whose sub-second
digits are dropped when they are zero, a non-ASCII character escaped one way on
the way out and another on the way back, a newline inside Markdown that splits
one NDJSON line into two. *Matters because* G0 has no soft-fail mode and this is
the property it gates on, so a defect here is one that blocks every release until
someone finds it -- and finding it from a diff of two 36-file bundles is far
harder than finding it from a shrunk counterexample. *No other instrument catches
it because* the golden fixture holds one hand-written row per table, chosen to be
readable, and readable values are exactly the ones that do not exercise escaping.

**The generated text is deliberately hostile** on the four axes that actually
break a line-delimited format: newlines, quotes and backslashes, non-ASCII, and
the empty string. Random alphanumerics would round-trip under a writer with none
of the escaping this asserts.

**Uniqueness comes from a counter, never from generated or time-derived data.**
`test_facade_roundtrip.py` records why: ULIDs share a time prefix, so two
examples in the same millisecond collide on a slug derived from one and the store
correctly refuses the second -- a failure that has nothing to do with this
property and reproduces only under load.
"""

import datetime as _dt
import itertools
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from adopt_export import apply_bundle, verify_roundtrip, write_bundle
from adopt_model import AudienceTag, KnowledgeItem, KnowledgeRevision, ObservabilityBoundary
from adopt_obs import ManualClock, new_id
from adopt_store import open_store
from adopt_store.api import SqliteStoreHandle, writer_identity

pytestmark = pytest.mark.property

_START = _dt.datetime(2026, 8, 5, 11, 0, 0, tzinfo=_dt.UTC)

#: How many items one example may carry. Enough for ordering to be exercised,
#: few enough that the suite stays inside its runtime ratchet.
# const-sync: ok -- a generation bound for this property, not a product value.
_MAX_ITEMS = 6

#: Every example creates two stores and therefore migrates schema version 3
#: twice, which is by far the dominant cost -- thirty-seven tables, not the six
#: rows the example is about. Twelve examples is what keeps this property inside
#: the runtime the ratchet allows while still exercising the whole hostile set,
#: which is sampled rather than searched.
# const-sync: ok -- a hypothesis example count for this property, not a tunable.
MAX_EXAMPLES = 12

_SYSTEMS = itertools.count()

#: The four things that break a line-delimited JSON format, and nothing else.
#:
#: **Surrogates are excluded, and that is a statement about the product rather
#: than a convenience.** A lone `\ud800` is not text: it has no UTF-8 encoding at
#: all, so contracts §1.3 -- "UTF-8, NFC normalization on every segment" -- does
#: not describe a value that could hold one. Generating them tests whether SQLite
#: rejects an unencodable string, which it does, and says nothing about whether a
#: bundle round-trips.
_HOSTILE = st.one_of(
    st.text(alphabet=st.characters(min_codepoint=1, exclude_categories=["Cs"]), max_size=40),
    st.sampled_from(
        [
            "",
            "line one\nline two",
            'he said "yes"',
            "back\\slash",
            "tab\tand\rcarriage",
            "naïve café — ünïcödé",
            "日本語のテキスト",
            "emoji 🧭 in a title",
            "trailing space ",
            '{"looks": "like json"}',
        ]
    ),
)


@pytest.fixture(scope="module")
def clock() -> ManualClock:
    return ManualClock(_START)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("export-roundtrip")


def _fresh_store(workspace: Path, clock: ManualClock, name: str) -> SqliteStoreHandle:
    return open_store(workspace / f"{name}.db", migrate=True, clock=clock)


def _seed(handle: SqliteStoreHandle, clock: ManualClock, titles: list[str], bodies: list[str]):
    """A four-level scope plus one item per generated title, written as models."""
    facade = handle.scope()
    suffix = f"{next(_SYSTEMS):06x}"
    firm = facade.create_firm(slug=f"firm-{suffix}", name="Firm")
    engagement = facade.create_engagement(firm_id=firm.id, slug=f"eng-{suffix}", name="Engagement")
    system = facade.create_system(engagement_id=engagement.id, slug=f"sys-{suffix}", name="System")
    environment = facade.create_environment(system_id=system.id, slug="prod", name="Production")

    now = clock.now()
    records = handle.import_records()
    with handle.backend.transaction():
        # The one `json` column in schema version 3. Its value is generated too,
        # because "the keys come back in the same order" is a claim about a dict
        # that was never in sorted order to begin with.
        records.insert_rows(
            "observability_boundary",
            [
                ObservabilityBoundary(
                    id=new_id("ob"),
                    system_id=system.id,
                    environment_id=environment.id,
                    tier="T2",
                    knowledge_plane_location="customer",
                    control_plane_location="customer",
                    permitted_outbound_categories={"z": 1, "a": [2, {"m": None, "b": True}]},
                    declared_at=now,
                    contractual=False,
                )
            ],
        )
        items: list[KnowledgeItem] = []
        revisions: list[KnowledgeRevision] = []
        tags: list[AudienceTag] = []
        for index, (title, body) in enumerate(zip(titles, bodies, strict=True)):
            item_id = new_id("ki")
            revision_id = new_id("krev")
            items.append(
                KnowledgeItem(
                    id=item_id,
                    firm_id=firm.id,
                    engagement_id=engagement.id,
                    system_id=system.id,
                    environment_id=environment.id,
                    kind="answer",
                    title=title,
                    current_revision_id=revision_id,
                    freshness_state="unverified",
                    created_at=now,
                    updated_at=now,
                )
            )
            revisions.append(
                KnowledgeRevision(
                    id=revision_id,
                    item_id=item_id,
                    body_md=body,
                    authority_class="artifact_observed",
                    created_at=now,
                )
            )
            tags.append(AudienceTag(item_id=item_id, audience=f"audience-{index}"))
        records.insert_rows("knowledge_item", items)
        records.insert_rows("knowledge_revision", revisions)
        records.insert_rows("audience_tag", tags)


@settings(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payloads=st.lists(st.tuples(_HOSTILE, _HOSTILE), min_size=1, max_size=_MAX_ITEMS))
def test_roundtrip_is_byte_identical(
    workspace: Path, clock: ManualClock, payloads: list[tuple[str, str]]
) -> None:
    run = f"{next(_SYSTEMS):06x}"
    titles = [title for title, _ in payloads]
    bodies = [body for _, body in payloads]

    with _fresh_store(workspace, clock, f"source-{run}") as source:
        _seed(source, clock, titles, bodies)
        first = workspace / f"first-{run}"
        write_bundle(source.export_records(), first, written_by=writer_identity(), clock=clock)

    with _fresh_store(workspace, clock, f"restored-{run}") as restored:
        apply_bundle(restored.import_records(), first)
        second = workspace / f"second-{run}"
        write_bundle(restored.export_records(), second, written_by=writer_identity(), clock=clock)

    verify_roundtrip(first, second)
