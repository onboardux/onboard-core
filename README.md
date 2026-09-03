# `adopt-core`

The Apache-2.0, local-first substrate for the Adoption-Phase Platform. `adopt`
maps and initializes an engagement, maintains a portable SQLite knowledge store,
and exports that knowledge without requiring an operated service.

The CLI is offline by default and has no telemetry switch. Network access is an
explicit per-invocation choice, and client content is structurally excluded from
logs.

> **Status: `0.3.1` is released** — fifteen distributions on PyPI, plus three
> signed single-file binaries, a CycloneDX SBOM, SLSA provenance and the v3
> reference bundle on the
> [GitHub Release](https://github.com/onboardux/onboard-core/releases/tag/v0.3.1).
> A workflow artifact is still not a release; only the tagged, signed bundle is.
>
> **Upgrading from `0.3.0` is a straight replacement.** No schema change, so a
> `0.3.0` store opens unchanged. `0.3.1` fixes two defects: the single-file
> binary stamped `adopt-core/0.0.0+unknown` as provenance into every bundle and
> store it wrote, and two distributions were missing a dependency declaration so
> installing them alone raised `ModuleNotFoundError`. Neither affected a
> `pip install adopt-cli` journey. See [CHANGELOG.md](CHANGELOG.md).

## What is included

The uv workspace contains 20 publishable packages:

| Area | Packages |
|---|---|
| CLI and workflow | `adopt-cli`, `adopt-workflow` |
| Store and schema | `adopt-store`, `adopt-schema`, `adopt-model`, `adopt-identity`, `adopt-scope` |
| Analysis | `adopt-detect`, `adopt-map`, `adopt-coverage`, `adopt-freshness`, `adopt-policy`, `adopt-agent` |
| Knowledge and delivery | `adopt-knowledge`, `adopt-ask`, `adopt-handover`, `adopt-probe` |
| Portability and operations | `adopt-export`, `adopt-obs`, `adopt-const` |

The command surface includes `init`, `detect`, `map`, `boundary`, store
migration and inspection, identity parsing, coverage and freshness evaluation,
export/import, policy validation, adapter checks, `doctor`, release provenance
reporting, the knowledge verbs (`ingest`, `harvest`, `bind`, `review`, `gaps`),
`ask`/`answer`/`serve`, `pack`/`draft`, `probe`, and `refresh`.

## Install

One package pulls in everything the CLI needs:

```sh
pip install adopt-cli          # or: uv tool install adopt-cli
adopt version --json
```

That is nineteen of the twenty published distributions. `adopt-workflow` is a
library the CLI does not depend on, so it is published but not installed by the
line above. **`scripts/release_context.CANONICAL_DISTRIBUTIONS` is the
authority for the set and its count**, asserted on every pull request; this
paragraph describes it and three different numbers appeared here before that was
said out loud.

Or download a single-file binary — `adopt-linux-x86_64`, `adopt-macos-arm64` or
`adopt-windows-x86_64.exe` — from the GitHub Release. It needs no Python.
**Verify it before you run it**; [SECURITY.md](SECURITY.md) has the exact
`cosign` and attestation commands.

To run from a checkout instead — Python 3.12 and
[uv](https://docs.astral.sh/uv/):

```sh
uv sync --all-packages
uv run adopt --help
```

## First run

`adopt` reads a tree, classifies it, and records what it may observe. Nothing is
sent anywhere: the CLI is offline unless you pass `--allow-network`.

```sh
# 1. What kind of system is this? Detection refuses rather than guesses.
adopt detect ./myproject --json

# 2. Answer the three qualification questions.
cat > answers.json <<'JSON'
{"artifact_access": true, "deploy_signal": true, "safe_interaction": true}
JSON

# 3. Create the store, resolve the scope, declare the boundary.
adopt init ./myproject \
  --scope northwind/acme-erp/support-agent/prod \
  --answers answers.json --json

# 4. Inventory what the system actually contains. Deterministic, offline.
adopt map ./myproject --json

# 5. Look at what you have. Looking never repairs.
adopt map --report --json
adopt store info --json
adopt doctor --json
```

`--scope` is four immutable slugs — firm, engagement, system, environment — and
all four are required, because a boundary is declared for one environment of one
system. `init` reports the archetype, the negotiated tier and a `boundary_id`.

**If `detect` exits non-zero with `DETECT_AMBIGUOUS`, that is the design.** A
wrong archetype is a different set of extractors, not a slightly wrong answer, so
detection refuses and ranks instead of guessing. Narrow the path to one system,
or accept an archetype yourself with `adopt init --archetype <a>`. A proposal is
never a decision: nothing is written until a human names it.

### `adopt map`

`map` walks the repository once and records what it finds as identities, each
with a canonical URI and provenance — the file and line span, the extractor and
its version. Three archetype packs ship: **generic** (declared dependencies,
config keys, environment variables and settings classes, scheduled jobs, CI
workflows, files of interest), **web** (HTTP endpoints, middleware and auth
boundaries, schema fields) and **ai** (prompt files and named prompts,
tool/function schemas, pinned model identifiers, retrieval and data-source
config, agent graph nodes). The archetype `init` recorded chooses the packs;
`--packs generic,web` overrides that for a mixed system.

```sh
adopt map ./myproject                             # exit 0
adopt map ./myproject --report                    # counts by kind, listing with provenance
adopt map ./myproject --check-expected list.txt   # exit 4, naming every miss
```

**No model is called, and nothing in the tree is executed or written.** Parsing
is `ast` and manifest-first; extractors receive a read-only view of the tree and
have no capability to do either.

**Re-running writes nothing when nothing changed.** Observation is keyed on the
URI, so the revision chain records what changed rather than how often you
scanned. A file that moves is recorded as a *move* when the evidence is
unambiguous — the old URI stays resolvable forever — and reported without being
written when it is not.

`--check-expected` takes a curated file of identity URIs, one per line with `#`
comments, and exits `4` naming every one that is absent. It is a recall floor
rather than a coverage percentage on purpose: a percentage improves when its
denominator shrinks, and a named list cannot be gamed that way. See
[tests/reference/](tests/reference/) for the two real repositories this is
proven against.

### `adopt ask`

`ask` answers from the store and tells you how much to trust the answer. Three
outcomes, and never a fourth:

```sh
adopt ask "why does the approval step exist on refunds?"
adopt ask "how do I rotate the API key?" --json
adopt ask "..." --reindex          # rebuild the retrieval index first
adopt ask "..." --escalate         # record it as an open question, with its text
```

- **KNOWN** — the passages verbatim, each citing its `knowledge_revision` id,
  the identity URIs it is bound to, and the freshness rule that let it serve.
- **STALE** — the same answer, served *with* the cause: `STALE because
  load_bearing_identity_moved`. What was true before, and why it may not be now.
- **UNKNOWN** — a refusal. If matching text existed but was unconfirmed, it says
  so, because "nobody has written this" and "somebody wrote it and nobody
  confirmed it" send you to different places.

**All three exit `0`.** An honest refusal is a correct answer, not a failure;
scripts branch on the `branch` field of the `--json` payload. A boundary
refusal is different and exits `3` (`ASK_OUTSIDE_BOUNDARY`).

**No model is called.** The answer is quotation with attribution — the store
already holds prose a human wrote and confirmed. Retrieval is SQLite FTS5 in the
runtime annex, a derived index rebuilt whenever it disagrees with the store,
never exported and never canon.

**Only confirmed knowledge is ever cited**, and every answer passes a freshness
resolution before it is composed — that check is a type signature rather than a
step, so there is no code path around it.

**Questions are not recorded unless you say so.** Passive logging is off
(`ADOPT_ASK_LOG_QUESTIONS`, see `adopt doctor`), and when you switch it on the
rows land in the runtime annex — never in the store, never in an export.
`--escalate` is the other half: it records *one* question with its text, because
recording it is the point. On a terminal you are asked first, and the prompt
defaults to no.

**With an adapter configured**, one grounded synthesis pass may rewrite the
passages into prose. It must cite the same revisions; a synthesis that cites
nothing, or cites anything that was not retrieved, is discarded and the
extractive answer serves instead. Nothing it produces is ever stored.

### `adopt answer`

The capture ratchet: a human's answer becomes confirmed, bound, cited knowledge
in **one** command, so the next asker gets KNOWN.

```sh
adopt ask "how do I rotate the API key?" --escalate
# -> UNKNOWN ... Recorded as open question esc_01J…

adopt answer esc_01J… --text "Rotate it in the vault, then restart the service."
# -> Captured krev_01J… as confirmed knowledge.

adopt ask "how do I rotate the API key?"
# -> KNOWN, citing krev_01J…
```

One transaction writes the item, its `verified` revision, its `human`
provenance, its bindings and the escalation stamp — or none of them. The
revision is always `human_confirmed`: nothing a person typed can ever claim to
have been observed in an artifact.

`--uri` binds the answer to an identity explicitly, and repeats. A canonical URI
written into the answer text binds too. A *name* appearing in prose never binds
— `config` and `user` are identity keys and ordinary English both. Zero bindings
is fine; the answer still serves.

### `adopt serve`

The same answers over loopback HTTP, for editor plugins and local tooling.

```sh
adopt serve                        # 127.0.0.1:8787
curl -s localhost:8787/healthz
curl -s localhost:8787/ask -d '{"question": "why do refunds need approval?"}'
```

`POST /ask` returns the CLI's own `--json` payload plus `"contract":
"unstable"` — the shape is a local convenience, not a wire contract, until the
control plane's API lands.

**There is no authentication, no TLS and no rate limit.** Loopback is the
default for that reason, and any other `--host` prints a warning naming exactly
what it exposes. Escalation over HTTP is the request body's `escalate` field and
nothing else: there is no human on a socket to ask, so nobody is assumed to have
agreed.

Development checkouts intentionally report `null` for `sbom_sha256` and
`build_id`. A signed release wheel or binary embeds those immutable build facts.

### `adopt pack`

Audience-scoped handover materials, assembled from the store.

```sh
adopt pack --audience client_ops --out ./handover
# -> handover/client_ops.md           the pack
#    handover/client_ops.lineage.json section -> the revisions it came from

adopt pack --audience client_ops --out ./handover --draft-missing
# -> uncovered sections drafted through the configured model adapter,
#    landed as UNVERIFIED knowledge and queued in `adopt review`

adopt pack --audience client_ops --out ./handover --format docx
# -> handover/client_ops.docx via pandoc; content-equivalent, never canon
```

Sections select **confirmed** knowledge by audience tag and kind; the map
contributes the inventory; the coverage join contributes the gap appendix; the
observability boundary is embedded so the pack states its own limits.

Every section carries a stamp and a date. `fresh` means a human confirmed it and
nothing has changed under it; `stale` means something has changed under it since;
`unverified` means **no human has confirmed it** — a draft, or a candidate mined
from history. Unverified and stale sections carry a banner above the body, not a
footnote after it.

**The Markdown is byte-stable given the same revisions.** No clock reaches it:
every date comes from a revision's own timestamp, so regenerating a pack over an
unchanged store produces identical bytes and a diff shows only what actually
changed. Write it outside the repository, or gitignore it — `adopt map` walks
the tree, and a pack left inside becomes source on the next run.

**Assembly needs no model, and that stays true with `--draft-missing`.** Without
a configured adapter the flag drafts nothing, says so, and writes the same
complete pack — sections with no confirmed knowledge state that rather than
disappearing. A model is an improvement to this command, never a dependency of
it.

### `adopt draft`

One section, on request, from what the store already knows.

```sh
adopt draft 'onboard-v1://acme/erp/orders/prod/endpoint/-/POST %2Fv1%2Forders'
```

The same grounded pass `--draft-missing` runs in bulk, aimed at one identity.
Both need a configured adapter (`ADOPT_ADAPTER`, `ADOPT_MODEL`) and both are
bounded by `AGENT_DRAFT_MAX_USD` and `AGENT_DRAFT_MAX_WALL_SECONDS`.

**A draft is grounded or it does not exist.** The model is sent only facts the
store already holds — the identity's attributes, where each was observed, and
any knowledge already bound to it — and it must cite the ones it used. A draft
citing nothing, or citing a fact that was not sent, is **discarded whole**:
nothing is written, and the identity stays in the gap appendix where it was.
No file in your repository is opened at draft time.

**What lands is unverified.** A surviving draft is a knowledge revision marked
`unverified`, bound to its identity, and queued in `adopt review` beside
harvest candidates. It renders into the pack under an UNVERIFIED banner, it
counts toward no coverage, and `adopt ask` never serves it as a known answer.

```sh
adopt review                       # drafts appear here, source `draft`
adopt review --confirm <item-id>   # appends a verified revision
```

Confirming is the only thing that promotes a draft, and it is always a person.
The next pack renders that section without the banner.

### `adopt gaps`

Identities minus covered knowledge, ranked — and what a human decided about each.

```sh
adopt gaps                                   # the elicitation queue
adopt gaps --ack   <gap-key> --owner alice --note "SME session booked"
adopt gaps --resolve <gap-key>
adopt gaps --waive <gap-key> --until 2026-12-31 --note "accepted risk"
```

**Existence stays derived.** `recompute_coverage()` alone decides whether an
identity is uncovered; a disposition records only what someone decided to do
about it, and the report is the join. A gap that later gets confirmed knowledge
disappears from the listing whatever its disposition says.

A waiver **must** carry `--until`. It is the one disposition that removes a gap
from everyone's attention, and without an expiry nothing ever brings it back.

### `adopt probe`

Safe, repeatable, recorded interactions — the mystery shopper for systems whose
behaviour changes without a commit. Builds 1–4 capture what the repository
*says*; this is what the system *does*.

```sh
adopt probe add probes/checkout-happy-path.yaml   # validated: safe path, hosts, budgets
adopt probe run --all                             # executes; records what it observed
adopt probe baseline --set                        # "this is how it behaves today"
# …the provider updates a model, a prompt lands, an index is rebuilt…
adopt probe run --all && adopt probe diff         # names what changed, and exits 4
```

A probe is **data**, not code. One YAML file declares its safe path
(`mock|sandbox|shadow`), the hosts it may reach, its wall-clock/request/cost
budgets, its secrets *by environment-variable name*, the steps to run (`http`
and `prompt`), and the invariants each response must hold:

```yaml
probe_id: checkout-happy-path
safe_path: sandbox
network: { deny_by_default: true, allow: ["api.sandbox.example:443"] }
side_effect_policy: prohibited
secret_refs: [env:PROBE_API_TOKEN]
runtime: { max_seconds: 30, max_memory_mb: 256, max_requests: 10 }
cost: { max_model_calls: 2, max_tokens: 8000 }
output: { retain_raw: false, redaction_policy: pii-default }
cleanup: { required: true }
exercises: ["onboard-v1://acme/erp/orders-api/prod/endpoint/-/POST%20%2Fv1%2Fcheckout"]
diff_method: exact
steps:
  - kind: http
    method: POST
    url: "https://api.sandbox.example/v1/checkout"
    headers: { Authorization: "Bearer {{secret.PROBE_API_TOKEN}}" }
    body: { sku: "demo-1", qty: 1 }
    expect: { status: 200, json_fields: ["order_id", "total"], latency_under_ms: 2000 }
  - kind: prompt
    input: "Summarize the checkout policy for a customer."
    expect: { min_similarity: 0.92 }
```

**Only the runner opens a socket, and only to a declared host.** That is a
CI-enforced import contract proven by a planted violation, and it is the whole
safety argument: a probe carries no executable content, so the allow-list *is*
the boundary. A step aimed anywhere else is refused with
`PROBE_HOST_UNDECLARED` and exit `3` — at connection time, not merely at `add`.

**Secrets resolve at send and are redacted at record.** `{{secret.NAME}}` goes
out in the request; `[redacted:NAME]` is what any observation, log line or error
message carries. `probe_observation.output` is an exportable column, so a leaked
token would travel in the client's bundle forever.

**`diff` never confuses two different sentences.** If the latest run and the
baseline are of different probe revisions it reports `probe_changed`, with both
revision ids and no similarity score — *you* edited the question, and that is
not the client's system changing. Only a same-revision comparison can drift, and
only drift exits `4`.

**A drifted probe that `exercises` an identity contradicts what the store says
about it.** Where confirmed knowledge is bound to that identity, an open
`conflict` row is written once — deduplicated, never resolved away by any code
here — and it appears in `adopt gaps` and in the pack's gap appendix. The
disagreement between intent and behaviour is a deliverable, not a shrug.

Not in this version: generated-code probes, browser/UI probes, scheduled
execution, cleanup verification (`probe_run.cleanup_verified` is recorded
`false` and judged by nobody), and the `embedding_sim` / `llm_judge` /
`contract_delta` diff methods, which are refused at `add` with a named message.

### `adopt refresh`

Knowledge rots because systems move and documents do not. `refresh` is the verb
that notices: it re-maps the repository, re-runs the probes, compares both
against what the store already believed, and turns the difference into
classified change events, staled knowledge and one review session.

```sh
adopt refresh                     # re-map (+ re-probe if probes exist)
# -> counts by class, a batch id, and what was staled
adopt review                      # the session: DEAD, MOVED, SEMANTICS-CHANGED, NEW
adopt ask "how are orders created?"
# -> STALE, naming the change that made it stale
adopt review --resolve ri_01J… --action retire|rebind|confirm-current [--to <uri>]
```

**Exit `4` means the command worked and found something.** A run that reaches
the tree, holds every invariant and observes that the system moved has done its
job; `4` is degraded-with-findings, the same reading `adopt probe diff` and
`adopt map --check-expected` carry. Exit `1` is reserved for a run that could
not do its job — an extractor that raised, which makes every absence below it
unreliable evidence. A clean run exits `0` and writes nothing at all.

**A comment cannot stale anything, by construction.** Staleness is decided by
the per-kind **attribute digest** — method + path + parameter names for an
endpoint, key + type + default for a config, whitespace-normalized text where
the value *is* text — never a file hash. Reformat a file, add a docstring,
reorder imports: the digest is identical, the change is recorded as
`BINDING_INTACT_RENDER_ONLY`, listed as informational, and no exit code fires.
False staleness is the failure that makes people stop reading the queue, so it
is made unrepresentable rather than merely unlikely.

**An extractor upgrade re-baselines; it does not produce a change storm.**
Digests are compared only within the same extractor version. Change how we look
and the run reports *"instrument changed, system not re-judged"*, records the
new digest, and classifies nothing.

The five classes, and what each means:

| Class | What happened | Where it goes |
|---|---|---|
| `BINDING_DEAD` | the referent was looked for and not found | the review queue |
| `BINDING_MOVED` | it is at a new address; its history follows it | the review queue |
| `BINDING_INTACT_SEMANTICS_CHANGED` | same address, different meaning | the review queue |
| `UNBOUND_NEW` | a referent nobody has written about | `adopt gaps` |
| `BINDING_INTACT_RENDER_ONLY` | the file changed, the referent did not | informational |

**Nothing is silent.** Every class is recorded with the deterministic
classifier's version and rendered in `adopt review`; the informational ones are
shown without asking anything of a reviewer. Silence has to be earned with
measured evidence, and nothing in this version has earned it.

**Propagation is narrow on purpose.** Only **load-bearing** bindings stale their
item — the same rule `adopt ask` resolves freshness by, so the queue can never
show an item as needing review while `ask` still serves it as fresh. A change to
a referent nobody documented queues nothing and is reported so the counts add up.

**One run is one review session.** Every event of a run shares a batch key, and
the items are coalesced — one entry per knowledge item however many changed
referents it is bound to — ordered by blast radius. A big rebase is a session,
not two hundred entries.

Three actions resolve a change item, and each has a store consequence rather
than only a disposition:

- `--action retire` — the note is obsolete. A terminal revision is appended and
  the item resolves `retired` forever. Nothing is deleted.
- `--action rebind [--to <uri>]` — the note followed its referent. The old link
  appends a `moved` revision and a new binding is created to the successor;
  `--to` defaults to the alias a MOVED classification already recorded, and is
  required for any other class, because a deleted endpoint does not name what
  replaced it.
- `--action confirm-current` — still true as written. A `human_confirmed` /
  `verified` revision is appended and the links it re-affirmed go `fresh`. If a
  cause is a dead or moved referent, the command **says** the item stays STALE
  and points at the two actions that help, because no binding write can clear a
  rule that reads the referent's own state.

**The probe half runs only if there is something to run.** With probes defined
and a baseline set, `refresh` re-runs them and a drift becomes a `provider`
change event classifying every identity the probe's `exercises` declares — the
same propagation, the same batch, the same review. A probe compared against a
baseline of another revision reports `probe_changed` and is never drift: *you*
edited the question. A probe that could not reach its system is reported as a
failure and its sensor heartbeat is what makes the knowledge
`observation_stale` rather than `stale` — "we cannot currently tell" is a
different answer from "this is out of date". `--no-probes` skips the half
entirely, and nothing else in `refresh` opens a socket.

Not in this version: continuous or scheduled execution, filesystem watchers,
webhooks, ML classification and silent repair. `refresh` runs when you run it.

### `adopt handover`

The Verified Handover: a bounded engagement-closure event, recorded step by
step, resumable, and auditable a year later from either party's copy.

```sh
adopt handover start --system orders-api --receiving-owner client-platform
adopt handover elicit --out ./handover     # open gaps -> targeted SME questions, by owner
adopt handover pack   --out ./handover     # one pack per audience, every section stamped
adopt handover verify --checklist ./checklist.yaml    # exit 4 if a task failed
adopt handover snapshot --out ./handover/acceptance   # bundle + recorded digest
adopt handover close --accepted-by "Priya Raman"      # ownership transfer
adopt handover status                                 # every step, dated and attributed
```

Six steps in order, each re-runnable once the one before it is recorded. The
state is the store's own audit trail, so `status` answers from the same rows the
client's `acceptance.json` was rendered from — there is no second record to
disagree with the first, and no new table: the handover adds nothing to the
schema.

**Failures in step 4 become questions, not verdicts.** Each failed task opens an
escalation carrying its text, so `adopt answer` banks the answer in the room and
the next asker gets it. Anything still open at close transfers with a named
owner.

**Two rules refuse rather than warn.** The event cannot close leaving the system
unowned — the check runs inside the closing transaction and rolls the whole
close back — and unresolved gaps, questions and conflicts are never marked
resolved to make the record look finished. There is no code path that could.

**The client can verify what they were handed.** The acceptance digest is
computed over the bundle's per-table digests, so importing the bundle and
re-exporting it reproduces the same string offline, with no access to us:

```sh
adopt import ./acceptance/bundle --into ./ours.db && adopt export ./ours --store ./ours.db
```

Exit codes: `0` clean, `4` a verification round with failures, `2` a step out of
order or no open event, `3` an unowned event or a store that is a pulled replica.

The human half — who is in the room, what happens between the verbs, and why the
event is sold at engagement start rather than at its end — is
[`docs/handover-playbook.md`](docs/handover-playbook.md), with a checklist
template beside it.

## Validate a checkout

```sh
uv lock --check
uv sync --frozen --all-packages
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict packages/ scripts/ tools/ bench/
uv run lint-imports --config importlinter.ini
uv run pytest
uv run python scripts/licence_gate.py --check
uv run python scripts/packaged_artifact.py --check
```

Two maintainer-only consistency jobs compare this repository with private design
and control-plane repositories. They are skipped for forks and are not required
to build, test, or use `adopt-core`; all self-contained product gates continue to
run on public contributions.

## Design invariants

- Tunables live in `adopt_const`; application code does not duplicate them as
  literals.
- Store and wire evolution is schema-first and additive-only from `0.3.0`.
- Knowledge, bindings, identities, and probe definitions use append-only
  revisions.
- Offline mode opens no socket except a configured local model endpoint.
- Stable structured events replace free-text logging, and deny-listed content
  fields are dropped at the sink.
- Every distributed dependency is licence-verified. The in-binary policy is
  permissive-only.
- A valid release includes one wheel and one source distribution per canonical
  distribution, three onefile binaries, a CycloneDX SBOM, SLSA provenance, and
  keyless cosign evidence.
  `scripts/release_context.py` holds the canonical set and the gate asserts it
  on every pull request, so that module is the authority and this line is a
  description of it.

## Repository boundary

Everything committed here is offered under Apache-2.0. The operated control
plane is a separate private repository and is never vendored here. Contributions
must not contain control-plane implementation, client material, credentials, or
other non-redistributable content.

## Licence and security

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Security reporting and
release verification are documented in [SECURITY.md](SECURITY.md); dependency
evidence is in [licence-verifications.md](licence-verifications.md).
