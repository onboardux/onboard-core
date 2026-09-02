# The Handover Event — a playbook

**What the commands cannot do for you.** `adopt handover` records six steps and
enforces their order; this document says who is in the room, what they do
between the verbs, and why each step is shaped the way it is. It is the human
half of v6.1 §6 Build 9, and it is deliberately short.

---

## Sell it at the start, run it at the end

**The handover event cannot be sold into an engagement that is already ending.**
Its value is proportional to the state the store accumulated on the way: if the
map, the ingested docs, the harvested decisions and the probe baselines were
built over months, the event assembles a real handover in an afternoon. If the
store is empty, the packs are an inventory plus a long gap list, and the event
becomes the broad knowledge-transfer interview it exists to replace.

`adopt handover start` says so when it finds no confirmed knowledge. That note
is not a blocker and is not meant as one — it is there because a product that
let you run this on an empty store without comment would be pretending.

Plan it at kickoff. Run it at closure.

---

## Who is in the room

| Role | Who | What they do |
|---|---|---|
| **Delivering FDE** | The engineer closing out | Runs every verb. Owns the checklist's tasks. |
| **Receiving owner** | The named group taking the system | Accepts at close. Must exist before the event starts. |
| **Receiving engineers** | Two or three of them | Perform the verification tasks. Not observers. |
| **SMEs** | Whoever the gaps point at | Answer the elicitation agenda. Only the gaps. |

The receiving owner is a **group** by default (`--individual` for a person),
because a person goes on holiday and a system's owner should not.

---

## The six steps

### 1. Freeze — `adopt handover start`

```sh
adopt handover start --system orders-api --receiving-owner client-platform --actor alice
```

Records the opening position: identities, how many are covered, confirmed and
unverified knowledge counts, open gaps, open conflicts, open questions, and who
owns the system today. That snapshot is what lets the closure say what it added.

The scope is frozen here. `--system` takes the whole system; `--scope
firm/engagement/system/environment` narrows to one environment when only that
one is being handed over.

**Before you run it**, make sure the state is worth handing over: `adopt map` is
current, `adopt refresh` has been run, and the review queue is not full of
unconfirmed candidates.

### 2. Elicit — `adopt handover elicit`

```sh
adopt handover elicit --out ./handover
```

Writes `handover/elicitation.md`: every open gap as a concrete question, grouped
by the owner who can answer it, with an `Unassigned` block at the end for the
ones nobody has claimed. Open conflicts — where a probe contradicted confirmed
knowledge — get their own section, because those are questions for the same
people and the answer is a deliverable either way.

**Book the sessions from the groups.** Each named group is one sitting. The
`Unassigned` block is triage: your first job there is finding the person, not
the answer.

**Ask only what is on the agenda.** Anything already covered by confirmed
knowledge is deliberately absent. An event that runs a general interview anyway
has not used the product, and the hour you spend re-asking answered questions is
the hour the gaps needed.

Capture what you learn the way you always do — `adopt answer <id> --text …` for
open questions, `adopt ingest` for documents somebody hands you, `adopt bind`
for links no heuristic found. Then re-run `elicit`; a second pass is expected
and the record keeps both.

### 3. Pack — `adopt handover pack`

```sh
adopt pack --audience client_ops --draft-missing    # optional: draft first
adopt review                                        # confirm or correct the drafts
adopt handover pack --out ./handover
```

Emits one pack per audience (`technical`, `client_ops`, `end_user`, `admin`),
each section stamped `fresh`, `stale` or `unverified` and dated. The digest of
every Markdown file is recorded, so the acceptance record can say exactly which
document went out.

**Drafting happens before this step, not during it.** `adopt pack
--draft-missing` writes drafts as unverified knowledge and `adopt review` is
where a human confirms or corrects them. What the handover emits is what
somebody has already confirmed, or what is explicitly stamped UNVERIFIED — and
unverified content renders under a banner a client cannot miss. That is
deliberate: a client reading a draft as verified truth is this build's worst
failure.

**Write the pack outside the repository**, or gitignore the directory. `adopt
map` walks the repo, so a pack written into it becomes source on the next run.

### 4. Verify — `adopt handover verify`

```sh
adopt handover verify --checklist ./checklist.yaml
```

The receiving engineers perform three to six realistic tasks using **only the
pack and `adopt ask`**. You record what happened; see
[`handover-checklist.example.yaml`](handover-checklist.example.yaml) for the
shape.

**Failures are findings about the pack, not marks against people.** Every failed
task becomes an open question carrying its text, and the honest thing to do is
answer it in the room:

```sh
adopt answer esc_01J… --text "Rotate it in the vault, then restart the service." \
    --uri 'onboard-v1://…/config_key/-/ORDERS_API_KEY'
```

That is the whole loop v5 §7.8 describes: *whatever they could not do becomes a
knowledge item, in the room*. Anything still open at close transfers with a
named owner.

The command exits `4` when any task failed — degraded with findings, not
failure. The command worked; it found something.

A second round is a second sitting, and both are kept. A record showing only the
last one could turn "they failed four tasks, then passed once we wrote the
runbook" into "they passed".

### 5. Snapshot — `adopt handover snapshot`

```sh
adopt handover snapshot --out ./handover/acceptance
```

Writes an export bundle and `acceptance.json` beside it, and records the
acceptance digest.

**Give the client both, and tell them how to check it.** The digest is computed
over the bundle's per-table digests, so they can verify their copy from their
own machine, offline, with the OSS CLI and nothing from us:

```sh
adopt import ./acceptance/bundle --into ./their-store.db
adopt export ./their-reexport --store ./their-store.db
# recompute over ./their-reexport/manifest.json's table digests -> same string
```

It reproduces because the table files are byte-stable and the digest ignores
everything that varies between exports. A client who can check what they were
handed is a client who does not have to trust us about it later.

### 6. Close — `adopt handover close`

```sh
adopt handover close --accepted-by "Priya Raman" --actor alice
```

Writes the ownership transfer, ends the previous system-scoped assignment, and
re-renders the acceptance record with the transfer filled in.

**Two rules bite here.**

*The event cannot close with the system unowned.* The command asks the same
ownership question the escalation router asks, inside the same transaction, and
rolls the whole close back if the receiving owner does not resolve. There is no
partial close.

*Unresolved items transfer with named owners rather than being closed to look
complete.* Every open gap nobody owns is assigned to the receiving owner; a gap
that already names an owner keeps them. Open questions belong to whoever owns
the system, which is now the receiving team. Open conflicts are listed. Nothing
is marked resolved — there is no code path in this build that could.

An event that closed its own open items to look finished would be worthless to
both parties six months later, which is precisely when the record gets read.

---

## Afterwards

`adopt handover status` describes the event forever — every step, when, by whom,
the digest, and what was still open at the time. It answers from the store, and
the client's `acceptance.json` answers the same thing from their copy.

**The Answer and Freshness services keep running.** A handover transfers
responsibility for a system; it does not stop the system changing. If the
engagement continues in an operated form, the pack sections stay current through
`adopt refresh` and the review queue.

---

## The operated version

What this playbook describes is the **self-serve** handover: free, offline, and
entirely within the OSS CLI. The paid event is the operated one — the plane
recording acceptance for both parties, with the receiving team holding their own
credential rather than a file you emailed them.

It is not built yet, and the trigger is the first paying handover engagement.
Nothing here forecloses it: the acceptance record is already the shape that
endpoint would receive, and the six steps are already rows in a table the plane
realizes.

**A handover cannot be run against a pulled replica.** After a system is
activated the plane owns its canon, and every step here writes canon — the
ownership transfer, the escalations, the gap dispositions, the acceptance trail.
`adopt pull` would replace all of it. The command refuses with
`HANDOVER_TARGET_IS_REPLICA` rather than letting an entire engagement closure
vanish at the next refresh.
