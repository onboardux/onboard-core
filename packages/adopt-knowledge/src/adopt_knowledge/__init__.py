"""`adopt ingest` / `adopt bind` / `adopt review` / `adopt gaps` -- Build 2.

The store's first knowledge corpus, bound **honestly** to the identities Build 1
mapped, and the coverage gap made visible.

**The invariant this package exists to hold** (v6.1 §6 H2/D9, critical semantic
invariant #2): *no binding row exists that a structural match or a human did not
justify.* Everything else here is in service of it.

Why that one, out of everything Build 2 could have been careful about: a false
binding is the only failure in this build that is both **silent** and
**self-reinforcing**. `recompute_coverage` counts the falsely-bound identity as
covered, so `adopt gaps` stops asking for the knowledge that is actually
missing; and every later change to that identity stales a document that never
described it, until the reviewer learns the queue is noise. Neither symptom
points back at the matcher. The two-tier rule -- structural evidence binds,
names are proposed to a person -- is what makes the failure unrepresentable
rather than merely unlikely.

Three further postures, each inherited rather than invented:

* **No model call anywhere.** v6.1 §4 R3; the optional summarization pass is
  Build 4's generation module and is not built here.
* **Offline.** Harvest mines what is locally present (F7). Forge enrichment is
  a `--allow-network` option that does not exist yet.
* **Mined and authored never merge.** `artifact_observed` is a claim about
  where something was read from, and nothing a human or a model writes can
  acquire it after the fact.
"""

from adopt_knowledge.documents import (
    AUDIENCES,
    DEFAULT_AUDIENCE,
    DEFAULT_KIND,
    Document,
    body_digest,
    discover,
    read_document,
    split_frontmatter,
)
from adopt_knowledge.gaps import CoverageEntry, Gap, rank_gaps
from adopt_knowledge.ingest import (
    CREATED,
    INGEST_EXTRACTOR_VERSION,
    UNCHANGED,
    UPDATED,
    DocumentOutcome,
    IngestReport,
    StoredDocument,
    run_ingest,
)
from adopt_knowledge.matchers import (
    NAME_TIER,
    STRUCTURAL_TIERS,
    IdentityView,
    Match,
    MatchOutcome,
    match_document,
    name_matches,
    structural_matches,
)
from adopt_knowledge.ports import BindingWriter, KnowledgeWriter, ReviewWriter
from adopt_knowledge.review import PendingItem, confirm, derive_suggestions, reject

__all__ = [
    "AUDIENCES",
    "CREATED",
    "DEFAULT_AUDIENCE",
    "DEFAULT_KIND",
    "INGEST_EXTRACTOR_VERSION",
    "NAME_TIER",
    "STRUCTURAL_TIERS",
    "UNCHANGED",
    "UPDATED",
    "BindingWriter",
    "CoverageEntry",
    "Document",
    "DocumentOutcome",
    "Gap",
    "IdentityView",
    "IngestReport",
    "KnowledgeWriter",
    "Match",
    "MatchOutcome",
    "PendingItem",
    "ReviewWriter",
    "StoredDocument",
    "body_digest",
    "confirm",
    "derive_suggestions",
    "discover",
    "match_document",
    "name_matches",
    "rank_gaps",
    "read_document",
    "reject",
    "run_ingest",
    "split_frontmatter",
    "structural_matches",
]
