"""`ChangeFacade` -- Build 6's writes, and the one thing it refuses to do.

`Store.changes()`, the accessor `adopt_store.api` reserved for "the sprint that
writes the tables it fronts" (contracts §10.3). This is that sprint.

**The facade records a conclusion; it never reaches one.** The five-class
cascade is `adopt_map.diff.compute`, a pure function over (stored state,
observed state, moves, file delta, extractor outcomes), and everything here
takes a class as an argument. That split is the whole reason Build 8 can operate
the same cascade server-side: the judgement is portable because it touches no
store, and the recording is portable because it touches no dialect.

**One event per source per run, many classifications under it.** A refresh
produces one `change_event` for the artifact half (and, from S6.2, one for the
probe half), sharing a `batch_key` of `refresh:<run>`; per-identity meaning
lives on `classification`, whose `(change_event_id, identity_id)` index is
UNIQUE precisely because one change means exactly one thing for one identity.

**Propagation is a write with a blast radius, so it is narrow by construction.**
`stale_load_bearing_bindings` stales only bindings whose `is_load_bearing` is
true, and only for the identities the caller names -- the semantics-changed set.
Deaths and moves need no write at all: `resolve_freshness` already reads
identity head status and stales through its source rules (F3), so writing a
binding row for them would be a second mechanism saying the same thing, and the
two would disagree the first time one was fixed.
"""

import datetime as _dt
from collections.abc import Mapping, Sequence
from typing import Final, Protocol, runtime_checkable

from adopt_model import Binding, ChangeEvent, Classification
from adopt_model._enums import ChangeSource, FreshnessState
from adopt_obs import Clock, SystemClock, get_logger, new_id, truncate_to_millisecond
from adopt_store.facades.records import ChangeRecords

__all__ = ["CLASSIFIER_LABEL", "CLASSIFIER_TRAINING_CATEGORIES", "ChangeFacade", "ClassifiedChange"]

_log = get_logger("adopt_store")

#: The deterministic cascade's version label (v6.1 §6 Build 6: "Classification
#: rows record `classifier_version = deterministic-v1`"). Build 8's ML
#: classifier lands as a *second* label, which is what makes it a version
#: rather than a rewrite -- and is why a build with no model records one at all.
CLASSIFIER_LABEL: Final[str] = "deterministic-v1"

#: `classifier_version.training_data_categories` is NOT NULL and means
#: "de-identified feature categories only". A deterministic cascade trained on
#: nothing says so, rather than leaving a reader to infer that an empty string
#: meant "unknown".
CLASSIFIER_TRAINING_CATEGORIES: Final[str] = "none: deterministic cascade, no training data"

_STALE: Final[FreshnessState] = "stale"
_FRESH: Final[FreshnessState] = "fresh"


@runtime_checkable
class ClassifiedChange(Protocol):
    """What the cascade concluded about one identity, as this facade reads it.

    **A protocol, not a class to construct.** `adopt_map.diff.ChangeEntry`
    already carries exactly these four fields, and `no-raw-sqlite` forbids
    `adopt_map` and the CLI from importing this package -- so a concrete type
    here would have to be transcribed into a second value on the far side of
    that boundary, which is one more place for a class or a cascade step to be
    copied wrong. The caller passes what the cascade produced; this facade reads
    the four fields it needs and mints everything else.

    The id, the event id, the classifier version and the timestamp are
    deliberately **not** here: they are the facade's to assign, on the same
    reasoning that keeps ids off every `RevisionDraft` -- a value that could
    carry them is a value through which a caller could forge a cascade decision.
    """

    @property
    def identity_id(self) -> str | None: ...

    @property
    def impact_class(self) -> str: ...

    @property
    def decided_by(self) -> str: ...

    @property
    def evidence(self) -> str: ...


class ChangeFacade:
    """`Store.changes()` -- `change_event`, `classification`, and propagation."""

    def __init__(self, records: ChangeRecords, *, clock: Clock | None = None) -> None:
        self._records = records
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def record_run(
        self,
        *,
        system_id: str,
        environment_id: str,
        source: ChangeSource,
        batch_key: str,
        changes: Sequence[ClassifiedChange],
        referent: str | None = None,
        raw: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Record one source's findings for one refresh run.

        Args:
            source: `artifact` for the map diff, `provider` for the probe delta.
            batch_key: `refresh:<run>`, shared by every event of one run so the
                review surface can render the session as one thing.
            changes: What the cascade concluded, one entry per affected
                identity. **Order is preserved**: the caller sorts by blast
                radius and ULIDs are monotonic, so id order replays it.
            raw: A structured summary of the run, never client content -- the
                column is exported, and `raw` is a tempting place to put a diff.

        Returns:
            `(change_event_id, classification_ids)`.

        Raises:
            ValueError: When `changes` is empty. A run that concluded nothing
                writes no event: an event with no classification is a claim that
                something changed with no statement of what it meant, and every
                reader downstream would have to special-case it.
        """
        if not changes:
            raise ValueError(
                "a change event needs at least one classification. A refresh that found "
                "nothing reports that plainly rather than recording an empty change."
            )

        detected = self._now()
        event_id = new_id("ce")
        classification_ids: list[str] = []

        with self._records.transaction():
            classifier_version_id = self._records.ensure_classifier_version(
                version_label=CLASSIFIER_LABEL,
                training_data_categories=CLASSIFIER_TRAINING_CATEGORIES,
                released_at=detected,
            )
            self._records.insert_change_event(
                ChangeEvent(
                    id=event_id,
                    system_id=system_id,
                    environment_id=environment_id,
                    source=source,
                    detected_at=detected,
                    referent=referent,
                    batch_key=batch_key,
                    raw=raw,
                )
            )
            for change in changes:
                if change.identity_id is None:  # pragma: no cover -- caller resolves first
                    raise ValueError(
                        "a classification needs an identity: `classification.identity_id` is "
                        "NOT NULL, and a change whose referent the caller has not resolved "
                        "cannot be recorded against one."
                    )
                classification_id = new_id("cls")
                self._records.insert_classification(
                    # **Validated from a mapping, not constructed by keyword**:
                    # `class` is a Python keyword, so the generated model spells
                    # the field `class_` behind `alias="class"` and the column is
                    # unreachable as a keyword argument. A `**{"class": ...}`
                    # splat reaches it at runtime but is unverifiable under
                    # `--strict`, which would put the one field whose spelling is
                    # already unusual outside the type checker's view.
                    Classification.model_validate(
                        {
                            "id": classification_id,
                            "change_event_id": event_id,
                            "identity_id": change.identity_id,
                            "class": change.impact_class,
                            "decided_by": change.decided_by,
                            "classifier_version_id": classifier_version_id,
                            "evidence": change.evidence,
                            # Nothing is silent in v1 (v6.1 §6 Build 6). Silence
                            # is earned per slice in Build 8 with measured
                            # precision and a sampled audit; until then every
                            # class reaches a human and this column says so.
                            "acted_silently": False,
                            "created_at": detected,
                        }
                    )
                )
                classification_ids.append(classification_id)

        _log.info(
            "change.recorded",
            # `event` is a reserved log key -- the field is spelled
            # `change_event` so the id still travels.
            change_event=event_id,
            source=source,
            batch_key=batch_key,
            classifications=len(classification_ids),
        )
        return event_id, tuple(classification_ids)

    def stale_load_bearing_bindings(
        self, bindings: Sequence[Binding], *, identity_ids: Sequence[str]
    ) -> tuple[str, ...]:
        """Stale the load-bearing bindings of the named identities. Returns their ids.

        The non-load-bearing filter is applied **here rather than in a query**,
        for the reason `FreshnessRecords.bindings_for_item` returns both kinds:
        the rule is `is_load_bearing`, PRD F8.3, and a rule that lives in SQL is
        a rule the propagation test cannot see. Invariant #4's negative case
        asserts exactly this line.

        A binding already `stale` is written again rather than skipped -- the
        write is idempotent, and branching on the current value would mean the
        set of ids returned depended on history rather than on what changed.
        """
        wanted = set(identity_ids)
        updated_at = self._now()
        staled: list[str] = []

        with self._records.transaction():
            for binding in sorted(bindings, key=lambda row: row.id):
                if binding.identity_id not in wanted or not binding.is_load_bearing:
                    continue
                self._records.set_binding_freshness(binding.id, _STALE, updated_at=updated_at)
                staled.append(binding.id)

        if staled:
            _log.info("change.propagated", bindings=len(staled), identities=len(wanted))
        return tuple(staled)

    def freshen_bindings(self, binding_ids: Sequence[str]) -> tuple[str, ...]:
        """Return the named bindings to `fresh` -- `confirm-current`'s half.

        **The selection is the caller's here, and the asymmetry with
        `stale_load_bearing_bindings` is deliberate.** Propagation is handed
        everything bound to a changed identity and must apply the load-bearing
        rule itself, because nothing upstream of it did. A confirmation is the
        opposite case: a reviewer read one item's causes and said "still true",
        so the links being re-affirmed are already named -- and re-deciding
        which of them qualify would let this method disagree with the queue
        entry the reviewer actually answered.

        Idempotent, and ordered by id so two runs over one store agree.

        **This is not `resolve_freshness` being overridden.** The binding row is
        one input among several; an item whose identity is dead or moved still
        resolves STALE through the source rules after this write, which is why
        `confirm-current` on a DEAD or MOVED cause is honest about changing
        nothing rather than appearing to clear it.
        """
        updated_at = self._now()
        freshened = sorted(set(binding_ids))

        with self._records.transaction():
            for binding_id in freshened:
                self._records.set_binding_freshness(binding_id, _FRESH, updated_at=updated_at)

        if freshened:
            _log.info("change.reaffirmed", bindings=len(freshened))
        return tuple(freshened)

    def classifications_in(self, batch_key: str) -> tuple[Classification, ...]:
        """Every classification of one refresh run. The review surface's read."""
        return tuple(self._records.classifications_for_batch(batch_key))

    def events_in(self, batch_key: str) -> tuple[ChangeEvent, ...]:
        return tuple(self._records.change_events_for_batch(batch_key))

    def classified_identities(self, batch_key: str) -> Mapping[str, tuple[Classification, ...]]:
        """`identity_id -> its classifications` for one run, ordered within each key."""
        grouped: dict[str, list[Classification]] = {}
        for classification in self._records.classifications_for_batch(batch_key):
            grouped.setdefault(classification.identity_id, []).append(classification)
        return {identity_id: tuple(rows) for identity_id, rows in grouped.items()}
