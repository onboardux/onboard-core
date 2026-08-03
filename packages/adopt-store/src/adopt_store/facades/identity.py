"""`IdentityFacade` -- observe, move, and the URI that is never rewritten.

The rule this facade exists to hold is contracts §4 rule 9: **a URI is never
rewritten.** Everything else follows.

* **Observation is idempotent, keyed on the URI.** Seeing the same referent again
  advances `last_seen` and writes nothing else -- no revision, because nothing
  about the identity changed (CUJ-2 step 3). A revision per sighting would turn
  the chain into a log of scans rather than a record of changes, and the
  chain-integrity property would then be measuring extractor cadence.
* **A move is two rows, not an edit.** The old identity gets an
  `identity_revision` with `status='moved'` and `alias_of_identity_id` pointing
  at the new one; the new identity is created with its own URI. The old URI stays
  resolvable forever, which is what makes a bundle exported last year still
  answerable.
* **The URI is built here, from the scope's slugs.** A caller cannot hand one in.
  That is what keeps `identity.uri` and the four scope columns from ever
  disagreeing -- they are derived from one input in one place.
"""

import datetime as _dt

from adopt_identity import build_uri, parse_uri
from adopt_model import Identity
from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, Clock, ErrorCode, SystemClock, new_id, truncate_to_millisecond
from adopt_scope import Scope
from adopt_store.facades.records import IdentityRecords
from adopt_store.revisions import FAMILIES, IdentityRevisionDraft, RevisionWriter

__all__ = ["IdentityFacade"]


class IdentityFacade:
    """`Store.identities()` -- contracts §10.3."""

    def __init__(
        self,
        records: IdentityRecords,
        writer: RevisionWriter,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._records = records
        self._writer = writer
        self._clock: Clock = clock if clock is not None else SystemClock()

    def _now(self) -> _dt.datetime:
        return truncate_to_millisecond(self._clock.now())

    def observe(
        self,
        *,
        scope: Scope,
        kind: IdentityKind,
        namespace: str | None,
        key: str | tuple[str, ...],
        extractor: str | None = None,
        extractor_version: str | None = None,
        confidence: float | None = None,
        actor_id: str | None = None,
    ) -> Identity:
        """Record a sighting of a referent, creating it the first time.

        Args:
            scope: Resolved to all four levels; `environment` is mandatory in a
                URI, so it is mandatory here.
            kind: The `identity_kind`.
            namespace: The namespace, or `None`.
            key: The local key. A bare string is one segment.
            extractor: Recorded on the first revision as provenance.
            extractor_version: The same.
            confidence: The same.
            actor_id: Who caused it.

        Returns:
            The `identity` row, with `last_seen` at this observation.

        Raises:
            AdoptError: any ``URI_*`` code from the builder; ``SCOPE_VIOLATION``
                when the scope does not resolve to an environment.
        """
        uri = build_uri(scope, kind, namespace, key)
        observed = self._now()

        with self._records.transaction():
            existing = self._records.find_identity_by_uri(uri)
            if existing is not None:
                # The whole of CUJ-2's happy path: the same referent, seen again.
                # No revision is written, because nothing about the identity
                # changed -- only our knowledge of when we last looked.
                self._records.touch_identity_last_seen(existing.id, observed)
                return existing.model_copy(update={"last_seen": observed})

            parsed = parse_uri(uri)
            identity = Identity(
                id=new_id(FAMILIES["idn"].parent_prefix),
                uri=uri,
                firm_id=self._require(scope, "firm"),
                engagement_id=self._require(scope, "engagement"),
                system_id=self._require(scope, "system"),
                environment_id=self._require(scope, "environment"),
                identity_kind=kind,
                # Derived from the parsed URI rather than from the arguments, so
                # the row and its URI cannot disagree even by one normalization.
                namespace=parsed.namespace,
                local_key=parsed.key_path,
                first_seen=observed,
                last_seen=observed,
            )
            self._records.insert_identity(identity)
            self._writer.append_revision(
                parent_id=identity.id,
                draft=IdentityRevisionDraft(
                    status="active",
                    extractor=extractor,
                    extractor_version=extractor_version,
                    confidence=confidence,
                ),
                expected_head_id=None,
                actor_id=actor_id,
            )

        return identity

    def find_by_uri(self, uri: str) -> Identity | None:
        """The identity with this URI, or `None`. The only lookup key there is."""
        return self._records.find_identity_by_uri(uri)

    def get(self, identity_id: str) -> Identity | None:
        return self._records.get_identity(identity_id)

    def move(
        self,
        *,
        identity_id: str,
        scope: Scope,
        kind: IdentityKind,
        namespace: str | None,
        key: str | tuple[str, ...],
        actor_id: str | None = None,
    ) -> Identity:
        """Record that a referent moved to a new address.

        The old identity is *not* edited. It receives a `moved` revision naming
        the new identity, and keeps its URI: an exported bundle that referenced
        the old address still resolves, and the alias chain says where the
        referent went.

        Returns:
            The **new** identity.

        Raises:
            AdoptError: ``REVISION_HEAD_DANGLING`` when `identity_id` names no
                identity; ``URI_MALFORMED`` when the destination URI equals the
                source, which is not a move.
        """
        with self._records.transaction():
            source = self._records.get_identity(identity_id)
            if source is None:
                raise AdoptError(
                    ErrorCode.REVISION_HEAD_DANGLING,
                    message=f"no identity {identity_id!r} exists to move",
                    hint="Observe the referent first. A move is recorded against the "
                    "identity that held the old address.",
                )
            destination = self.observe(
                scope=scope, kind=kind, namespace=namespace, key=key, actor_id=actor_id
            )
            if destination.id == source.id:
                raise AdoptError(
                    ErrorCode.URI_MALFORMED,
                    message=f"the destination URI is {source.uri!r}, which is where the "
                    "referent already is",
                    hint="A move needs a different URI. Re-observing the same referent is "
                    "not a move and must not append a `moved` revision -- that would "
                    "make the identity its own alias.",
                )
            self._writer.append_revision(
                parent_id=source.id,
                draft=IdentityRevisionDraft(status="moved", alias_of_identity_id=destination.id),
                expected_head_id=self._writer.current_head(source.id),
                actor_id=actor_id,
            )
        return destination

    def retire(self, *, identity_id: str, reason: str, actor_id: str | None = None) -> str:
        """Append a `dead` revision. The row and its URI remain readable."""
        return self._writer.retire(parent_id=identity_id, reason=reason, actor_id=actor_id)

    @staticmethod
    def _require(scope: Scope, level: str) -> str:
        node = getattr(scope, level)
        if node is None:
            raise AdoptError(
                ErrorCode.SCOPE_VIOLATION,
                message=f"an identity needs a resolved {level}",
                hint="Every identity row carries all four scope columns (CR-09): they are "
                "what the plane's row-level-security policy is expressed from.",
            )
        return str(node.id)
