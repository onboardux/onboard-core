---
name: adopt-watch
description: Keep an adopt store current after the client's system changes — run `adopt refresh` (re-map, re-probe, classify every change deterministically, stale the bound knowledge, open one review session), read what each change means, and resolve change items with retire / rebind / confirm-current on a person's decision. Use whenever code was merged, a release shipped, someone renamed or removed things, `adopt ask` started answering STALE, or someone asks "what changed", "is our documentation still right" or "triage the drift". Load adopt-cli first.
---

# Watching for change

**Why:** knowledge rots silently when the system moves under it. `refresh`
compares the repository with the store, classifies every change
deterministically, and stales exactly the knowledge whose **load-bearing**
binding changed. So `ask` answers STALE, naming the cause, rather than serving
something that stopped being true. Your job is to run it, explain each finding
plainly, and record the person's decision about each one.

Read `adopt-cli` first.

## 1. Refresh

```shell
adopt refresh --packs generic,web,ai --json > ../orders-api-adopt/refresh.json
```

**Pass the packs the store was mapped with** (the workspace's `packs.txt`; omit
`--packs` only if onboarding did). Without them, `refresh` runs the archetype's
packs alone and lists every other identity under `exempt`: not dead, and **not
checked either**. Measured: a reworded prompt on a web system mapped with the
`ai` pack went unnoticed, and the README bound to it stayed fresh. Read `exempt`
on every run, and say what it contains.

| Exit | Means | Do |
|---|---|---|
| `0` | Clean, **or** the only findings were cosmetic (`BINDING_INTACT_RENDER_ONLY`) | Report it; nothing needs a decision. |
| `4` | Actionable findings | Open the review (section 2). |
| `1` | An extractor failed | **Stop.** Every absence in this run is unreliable, so an identity that looks "dead" may simply not have been looked for. Resolve nothing from it. |

Stored probes are re-run too, which sends requests to the client's environment.
When probe runs are not approved for this session, run the artifact half only
(it opens no socket):

```shell
adopt refresh --no-probes --json
```

Probe steps of `kind: prompt` also need `--allow-network`, which needs a yes. On
a store pulled from a control plane, `refresh` refuses (`REFRESH_TARGET_IS_REPLICA`):
the plane is the only writer there, and sensing runs through `adopt ci-sense` in
the client's CI (see `adopt-connect`).

## 2. What each change means

```shell
adopt review --json > ../orders-api-adopt/review.json
```

A refresh opens **one** batch, coalesced per knowledge item and ordered by blast
radius, so a big rebase is one sitting and not two hundred entries. Each entry
carries its causes:

| Class | What happened | Usually |
|---|---|---|
| `BINDING_DEAD` | The identity the note is bound to is gone. | `retire` if the thing was removed on purpose; otherwise look for its successor among this run's `UNBOUND_NEW`. |
| `BINDING_MOVED` | The same referent at a new address, with **byte-identical** extracted attributes (a file or class relocated); the alias was recorded. | `rebind` (the target defaults to the alias). |
| `BINDING_INTACT_SEMANTICS_CHANGED` | Its extracted attributes changed: an endpoint's parameters, a settings key's type or whether it is required, a schedule, a prompt's wording. | Read the note against the change: still true, so `confirm-current`; no longer true, so a person corrects it. |
| `UNBOUND_NEW` | A new identity no knowledge covers yet. | A new gap for `adopt-capture` — unless it is a dead referent's successor. |
| `BINDING_INTACT_RENDER_ONLY` | Mapped territory changed, but no extracted attribute did: comments, formatting. | Informational. It never exits `4`. |

**A rename is DEAD plus NEW, not MOVED.** An endpoint's path and a config key's
name are part of what is extracted, so a renamed route or environment variable
changes its attributes as well as its address and cannot pair. Measured: `POST
/shipments/{id}/cancel` renamed to `/cancellation`, and `CARRIER_API_KEY` to
`CARRIER_API_TOKEN`, each arrived as one `BINDING_DEAD` and one `UNBOUND_NEW` in
the same run. Pair them for the person, and propose `rebind --to` the new URI.

**What no attribute records, no refresh sees.** A settings default and a value
in `.env.example` are not attributes. Measured: a cancel-approval threshold
raised in both places exited `0` with no actionable change, while the runbook
still gave the old number; it appeared only under `render_only`, beside every
other key in the same file. When `render_only` names a configuration file, read
the file's diff yourself and tell the person what moved. A probe, or a person,
is the instrument for values.

Attribute digests are compared only within one extractor and one extractor
version. When the tool itself was upgraded, the report lists `rebaselined`
entries and says the instrument changed and the system was not re-judged; do not
read that as change in the client's system.

For each entry, show the person the cause, the evidence and the note's text, and
give your reading of which action fits. **The decision is theirs.**

## 3. Resolve — each action writes to the store

```shell
adopt review --resolve <review-item> --action retire --actor sam@client.com --json
adopt review --resolve <review-item> --action rebind --to '<successor URI>' --actor sam@client.com --json
adopt review --resolve <review-item> --action confirm-current --actor sam@client.com --json
```

| Action | Store consequence |
|---|---|
| `retire` | Appends the item's terminal revision. It stops serving. There is no un-retire, so be sure. |
| `rebind` | Appends a `moved` revision to each load-bearing link and binds the successor. `--to` defaults to the alias a `BINDING_MOVED` recorded; **for any other class it is required**, because a rebind without a recorded successor is a guess. |
| `confirm-current` | Appends a `human_confirmed`/`verified` revision and returns the re-affirmed links to fresh. |

On a `BINDING_DEAD` or `BINDING_MOVED` cause, `confirm-current` reports that the
item **stays STALE**. Confirming a note cannot revive the identity it is bound
to. Rebind it or retire it instead.

**An item with more than one cause: stop before `rebind`.** `rebind` re-points
*every* load-bearing link to a referent that changed in this run onto the one
`--to` target, including a link to one that is still alive (a
`SEMANTICS_CHANGED` cause). Measured: an answer bound to the dead cancel route
and to the live approval-threshold key came out bound to the new route alone,
and `adopt bind` cannot put the threshold link back (`REVISION_CHAIN_FORK`, see
`adopt-capture` section 6). So when an item's causes mix a dead or moved
referent with a live one, lay out both costs and let the person choose:

- `rebind --to <successor>`: the note follows the renamed referent and **loses
  its binding to the live one**, which becomes a gap again; or
- `confirm-current`: the live link returns to fresh, and the note **stays STALE**
  on the dead one.

**When the note is a client document that is now wrong,** the fix is theirs: they
edit the document, and you re-run the same `adopt ingest` from the root, which
records it as `updated`. Resolve the change item after that, not before.

## 4. Check the result

```shell
adopt ask "<the question the changed note answers>" --json
adopt freshness resolve --item <knowledge-item-id> --json
```

`freshness resolve` names the deciding rule behind an item's state. A sensor
outside `HEALTHY`, or one that has never reported, forces `observation_stale`:
silence is never read as stability.

## Report back

Give the exit code, counts by class, what was resolved and how (with the
person's name), what is still open, and any new gaps from `UNBOUND_NEW`. If the
run exited `1`, say that nothing was resolved and why.

## When something refuses

<!-- BEGIN GENERATED: errors REFRESH_,REVIEW_,FRESHNESS_,REVISION_ -->
| Code | Category | Exit |
|---|---|---|
| `FRESHNESS_SENSOR_DEGRADED` | policy | `3` |
| `REFRESH_TARGET_IS_REPLICA` | policy | `3` |
| `REVIEW_ITEM_NOT_FOUND` | usage | `2` |
| `REVIEW_ITEM_RESOLVED` | policy | `3` |
| `REVISION_CHAIN_FORK` | integrity | `1` |
| `REVISION_HEAD_DANGLING` | integrity | `1` |
| `REVISION_IMMUTABLE` | policy | `3` |
<!-- END GENERATED -->
