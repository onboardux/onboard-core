"""The attribute digest -- v6.1 §6 Build 1, H5.

**Never a raw file hash.** The digest covers the *extracted attributes* of an
identity and nothing else, which is what makes a formatting change, a reordered
import, an added comment or a moved line incapable of manufacturing staleness.
That is not a nicety: Build 6 maps a digest change to `SEMANTICS-CHANGED`, and
false staleness is the failure that makes a reviewer stop trusting the queue.

The rendering rule is the exporter's, deliberately reused rather than reinvented:
sorted keys, no spaces, no ASCII escaping, UTF-8. A digest is a pure function of
(attributes, extractor version) on every machine.

**Comparisons are valid only within one extractor version.** The version is
mixed into the digest input rather than merely stored beside it, so two digests
from different extractor versions cannot collide into looking equal. Build 6
compares only within a matching version and re-baselines across one -- "we
changed how we look", not "the system changed".
"""

import hashlib
import json
from collections.abc import Mapping

__all__ = ["DIGEST_PREFIX", "attribute_digest", "canonical_attributes"]

#: Named so a stored value is self-describing: a reader seeing `sha256:` knows
#: what produced it without consulting a document, and a future algorithm change
#: is a new prefix rather than a silent reinterpretation of old rows.
DIGEST_PREFIX = "sha256"


def canonical_attributes(attributes: Mapping[str, object]) -> str:
    """The attribute map as one canonical string.

    Sorted keys and the tightest separators, at every nesting level -- the same
    rule `adopt_export` applies, so a tool that can read one can read the other.
    `default=str` is a deliberate backstop rather than a licence to pass exotic
    objects: an extractor should hand over primitives, and anything else is
    rendered rather than crashing a run late in a large repository.
    """
    return json.dumps(
        attributes,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def attribute_digest(attributes: Mapping[str, object], *, extractor_version: str) -> str:
    """`sha256:<hex>` over the canonical attributes and the extractor version.

    Args:
        attributes: The digest input. Whatever a pack puts here defines what a
            semantic change *is* for that kind, permanently -- a line number or
            a file path in this map would reintroduce exactly the false
            staleness H5 exists to remove.
        extractor_version: Mixed in, so digests from two versions are never
            comparable by accident.
    """
    payload = f"{extractor_version}\x00{canonical_attributes(attributes)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{DIGEST_PREFIX}:{digest}"
