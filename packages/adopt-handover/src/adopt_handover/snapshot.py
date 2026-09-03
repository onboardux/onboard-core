"""The acceptance digest: one string both parties can recompute from their own copy.

v6.1 §6 Build 9 step 5: *"acceptance snapshot = adopt export bundle + recorded
digest, both parties hold it."*

"Both parties hold it" is only a promise if the receiving party can **check**
theirs. So the digest is a function of the bundle's table digests and of nothing
else:

* **Not of `manifest.json`.** That file carries `written_at`, which differs on
  every export -- so a digest over the manifest would disagree with itself the
  first time the client re-exported their own imported copy, which is exactly
  when they would be checking.
* **Not of the bundle directory.** A stray file somebody left beside it would
  change the answer.
* **Of the table digests, sorted.** Those are per-table SHA-256s of byte-stable
  files (gate `golden-g0`, no soft-fail), so the client runs `adopt import` then
  `adopt export` and arrives at the same string from their own bytes.

The delivering store records the digest in its audit trail and the client holds
it in `acceptance.json`. Neither copy is authority over the other: they are two
renderings of the same fact, and a disagreement between them is the whole point
of having recorded it.
"""

import hashlib
import json
from collections.abc import Iterable
from typing import Final

__all__ = ["ACCEPTANCE_DIGEST_ALGORITHM", "acceptance_digest"]

#: Named in the record so a reader a year later knows what to recompute with,
#: rather than inferring it from a hex length.
ACCEPTANCE_DIGEST_ALGORITHM: Final[str] = "sha256"


def acceptance_digest(tables: Iterable[tuple[str, str]]) -> str:
    """The acceptance digest over `(table name, table sha256)` pairs.

    Order-independent by construction: the pairs are sorted before rendering, so
    two exports that emitted their tables in different orders -- or a manifest
    read back through a different realization -- still agree.

    The rendering is the export writer's one rule (sorted keys, no spaces, no
    ASCII escaping), spelled here as `sidecar.render_sidecar` spells it, so the
    digest does not change with the platform it was computed on.
    """
    payload = sorted([name, sha256] for name, sha256 in tables)
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
