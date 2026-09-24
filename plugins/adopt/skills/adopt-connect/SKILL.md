---
name: adopt-connect
description: Connect an adopt engagement to an Onboard control plane (the paid, operated layer) from the client side — configure remote mode, turn a laptop store into a verified read replica with `adopt pull`, route captures and review decisions to the plane, and add the `adopt ci-sense` step to the client's CI so changes are sensed continuously. Use whenever a system is being activated on or already operated by a control plane, someone mentions ADOPT_PLANE_URL, a plane token, `adopt pull`, a replica, `ci-sense`, continuous sensing, or errors like PLANE_REMOTE_NOT_CONFIGURED, PULL_TARGET_NOT_REPLICA or REFRESH_TARGET_IS_REPLICA. Load adopt-cli first.
---

# Connecting to a control plane

**What changes when a system is operated:** before activation, the store on the
FDE's machine is the canon. After activation, **the plane is the only writer**.
The laptop store becomes a read replica, and every capture-class write (answers,
review decisions) goes to the plane through the same commands. The CLI becomes
one more channel, like Slack. Sensing moves into the client's own CI, where the
repository access already is, so the plane never holds repository credentials.

Everything here is the client side, and all of it ships in the open-source CLI.
Activating a tenant and issuing tokens is done by whoever operates the plane; ask
them for the values below. Read `adopt-cli` first.

## 1. Remote mode: three facts, and the token by reference

```shell
export ADOPT_PLANE_URL=https://plane.example.com
export ADOPT_PLANE_SYSTEM=sys_01J...              # the system id this store replicates
export ADOPT_PLANE_TOKEN_ENV=ADOPT_PLANE_TOKEN    # the NAME of the variable holding the token
export ADOPT_PLANE_TOKEN=...                      # the token itself: from a secret store, never a file
```

**Configuring a plane is the consent.** There is no `--allow-network` on these
verbs: their whole purpose is to reach that one host. With nothing configured
they refuse with `PLANE_REMOTE_NOT_CONFIGURED` rather than writing locally.

Tokens are scoped, and each should carry only what its job needs:

| Job | Scope |
|---|---|
| `adopt pull` | `export` |
| remote `adopt answer`, and `adopt review` with `--confirm` or `--resolve` | `confirm` (and `ask` to read the queue) |
| `adopt ci-sense` in the client's CI | `sense` only |

Never write a token into a repository, a workflow file, `.adopt/config.toml` or a
chat. It lives in the environment, or in the CI's secret store.
`adopt doctor --json` shows which layer set each key without printing secret
values.

## 2. `adopt pull` — a verified read replica

```shell
adopt pull --json
```

It fetches the tenant's bundle, **verifies every digest**, imports into a fresh
file, and only then swaps it in atomically, writing a `<store>.replica.json`
marker beside it. The replica you are asking questions of is untouched until a
complete, verified copy is ready. Afterwards `ask`, `pack`, `gaps` and `review`
(listing) run locally against canon.

- `PULL_TARGET_NOT_REPLICA`, exit `3`: the local store is not a replica of this
  system. Perhaps it is the field store holding canon nobody exported yet.
  `--init-replica` **replaces** it. That is irreversible, so get a yes, and make
  sure its content already reached the plane (normally via activation from an
  `adopt export` bundle).
- Pull on a schedule that suits the engagement. The replica is only as fresh as
  its last pull.

## 3. Capture in remote mode

The commands do not change. `adopt answer <escalation> --text ... --actor ...`
posts to the plane's confirm endpoint and lands in the tenant's canon; `pull`
brings it back. Review resolutions behave the same way. Everything in
`adopt-capture` about whose words these are applies unchanged.

What refuses on a replica, by design: `adopt refresh` (`REFRESH_TARGET_IS_REPLICA`)
and local writes (`STORE_TARGET_IS_REPLICA`). The plane runs the same
deterministic classification server-side, fed by `ci-sense`.

## 4. `adopt ci-sense` in the client's CI

```shell
adopt ci-sense . --run-id "$GITHUB_RUN_ID" --json
```

It walks and extracts exactly as `map` does, then posts **attribute and file
digests only** (never a line of source) to the plane, which classifies them
against canon. It writes nothing locally.

- Adding it to the client's pipeline is a change to **their** repository and
  **their** CI: propose it, and let them merge it. Start from
  `assets/ci-sense.github-actions.yml`. It installs the published CLI in a
  throwaway environment, creates the store with the tenant's exact scope (the
  plane refuses a payload claiming another scope), and posts.
- The token is a `sense`-scoped secret in the CI's secret store.
- `--run-id` is the idempotency key. Reuse it across retries of one pipeline run
  so a retry records liveness without writing twice.
- Exit `0` on findings by default. `--strict` exits `4` when the plane records
  findings, and it is off deliberately: a sensing step that fails a deploy over
  stale documentation is a step somebody deletes.
- `--cadence-hours` states how often this pipeline reports. Silence beyond it
  degrades freshness for the scope, because silence is never read as stability.
- `--no-probes` posts the artifact observation only and opens no socket toward
  the client's system.
- **A first post opens no review batch, however many identities it sees.** Only
  changes to *load-bearing bindings* queue. That is correct, not a failure.

## 5. What the plane gives back

- A self-serve export at any time, which the open-source CLI imports and runs
  forever offline. If you leave, you keep everything.
- A continuity export, delivered on a schedule to a destination the client
  designates. The maximum loss window if the vendor fails is that cadence.
- One place where answers, escalations, freshness and ownership are visible
  across systems. That is the operator's console, not this CLI.

## When something refuses

<!-- BEGIN GENERATED: errors PLANE_,PULL_,REFRESH_,STORE_ -->
| Code | Category | Exit |
|---|---|---|
| `PLANE_ACTIVATION_UNOWNED` | policy | `3` |
| `PLANE_AUTH_INVALID` | policy | `3` |
| `PLANE_CONNECTOR_REVOKED` | policy | `3` |
| `PLANE_REMOTE_NOT_CONFIGURED` | usage | `2` |
| `PLANE_SENSE_PAYLOAD_INVALID` | usage | `2` |
| `PULL_TARGET_NOT_REPLICA` | policy | `3` |
| `REFRESH_TARGET_IS_REPLICA` | policy | `3` |
| `STORE_READ_ONLY` | policy | `3` |
| `STORE_TARGET_IS_REPLICA` | policy | `3` |
<!-- END GENERATED -->
