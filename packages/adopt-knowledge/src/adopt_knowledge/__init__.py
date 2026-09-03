"""`adopt ingest` / `harvest` / `bind` / `review` / `gaps` -- Build 2.

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
* **Offline.** Harvest mines what is locally present (F7) through the system
  `git` binary, confined to `gitlog`. Forge enrichment is a `--allow-network`
  option that is **declared and refused**, so an operator who read the
  architecture gets a sentence naming the deferral rather than an
  unknown-option error.
* **Mined and authored never merge.** `artifact_observed` is a claim about
  where something was read from, and nothing a human or a model writes can
  acquire it after the fact.
"""

from adopt_knowledge.changes import (
    ACTION_CONFIRM_CURRENT,
    ACTION_REBIND,
    ACTION_RETIRE,
    ACTIONS,
    SOURCE_RULED_CLASSES,
    ChangedBinding,
    ChangeOutcome,
    confirm_current_item,
    rebind_item,
    retire_item,
    still_stale_after_confirm,
)
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
from adopt_knowledge.drafting import (
    DRAFT_ACTOR,
    DRAFT_AUTHORITY,
    DRAFT_KIND,
    DRAFT_PROMPT_REF,
    DRAFT_PROVENANCE_PREFIX,
    DRAFT_SOURCE,
    DRAFT_VERIFICATION,
    Draft,
    DraftOutcome,
    DraftReport,
    DraftTarget,
    Fact,
    draft_one,
    ground,
    render_body,
    run_drafting,
    title_for,
)
from adopt_knowledge.gaps import (
    GAP_KEY_SEPARATOR,
    OPEN_DISPOSITION,
    ConflictRow,
    CoverageEntry,
    Gap,
    OpenConflict,
    gap_key_for,
    rank_conflicts,
    rank_gaps,
)
from adopt_knowledge.gitlog import Commit, head_sha, read_commits
from adopt_knowledge.harvest import (
    HARVEST_EXTRACTOR,
    HARVEST_EXTRACTOR_VERSION,
    Candidate,
    HarvestReport,
    Signal,
    batch_key,
    decision_record_titles,
    mine,
    run_harvest,
)
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
    path_matches,
    structural_matches,
)
from adopt_knowledge.ports import (
    BindingFreshener,
    BindingSuperseder,
    BindingWriter,
    DraftStore,
    ItemRetirer,
    KnowledgeWriter,
    ReviewWriter,
)
from adopt_knowledge.review import (
    CHANGE_POPULATIONS,
    SOURCE_DRAFT,
    SOURCE_HARVEST,
    SOURCE_INGEST,
    SOURCE_REFRESH,
    SOURCE_SENSE,
    ChangeCause,
    ChangedItem,
    Outcome,
    PendingItem,
    coalesce_changes,
    confirm,
    derive_suggestions,
    edit,
    reject,
    source_of,
)

__all__ = [
    "ACTIONS",
    "ACTION_CONFIRM_CURRENT",
    "ACTION_REBIND",
    "ACTION_RETIRE",
    "AUDIENCES",
    "CHANGE_POPULATIONS",
    "CREATED",
    "DEFAULT_AUDIENCE",
    "DEFAULT_KIND",
    "DRAFT_ACTOR",
    "DRAFT_AUTHORITY",
    "DRAFT_KIND",
    "DRAFT_PROMPT_REF",
    "DRAFT_PROVENANCE_PREFIX",
    "DRAFT_SOURCE",
    "DRAFT_VERIFICATION",
    "GAP_KEY_SEPARATOR",
    "HARVEST_EXTRACTOR",
    "HARVEST_EXTRACTOR_VERSION",
    "INGEST_EXTRACTOR_VERSION",
    "NAME_TIER",
    "OPEN_DISPOSITION",
    "SOURCE_DRAFT",
    "SOURCE_HARVEST",
    "SOURCE_INGEST",
    "SOURCE_REFRESH",
    "SOURCE_RULED_CLASSES",
    "SOURCE_SENSE",
    "STRUCTURAL_TIERS",
    "UNCHANGED",
    "UPDATED",
    "BindingFreshener",
    "BindingSuperseder",
    "BindingWriter",
    "Candidate",
    "ChangeCause",
    "ChangeOutcome",
    "ChangedBinding",
    "ChangedItem",
    "Commit",
    "ConflictRow",
    "CoverageEntry",
    "Document",
    "DocumentOutcome",
    "Draft",
    "DraftOutcome",
    "DraftReport",
    "DraftStore",
    "DraftTarget",
    "Fact",
    "Gap",
    "HarvestReport",
    "IdentityView",
    "IngestReport",
    "ItemRetirer",
    "KnowledgeWriter",
    "Match",
    "MatchOutcome",
    "OpenConflict",
    "Outcome",
    "PendingItem",
    "ReviewWriter",
    "Signal",
    "StoredDocument",
    "batch_key",
    "body_digest",
    "coalesce_changes",
    "confirm",
    "confirm_current_item",
    "decision_record_titles",
    "derive_suggestions",
    "discover",
    "draft_one",
    "edit",
    "gap_key_for",
    "ground",
    "head_sha",
    "match_document",
    "mine",
    "name_matches",
    "path_matches",
    "rank_conflicts",
    "rank_gaps",
    "read_commits",
    "read_document",
    "rebind_item",
    "reject",
    "render_body",
    "retire_item",
    "run_drafting",
    "run_harvest",
    "run_ingest",
    "source_of",
    "split_frontmatter",
    "still_stale_after_confirm",
    "structural_matches",
    "title_for",
]
