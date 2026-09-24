---
name: adopt-onboard
description: First day on a client system with the adopt CLI — detect the archetype, agree the four scope slugs and the observability boundary with a person, init the store, map the repository, curate a recall list that proves nothing important was missed, ingest the client's existing docs, harvest decisions from git history, and produce the first gap list. Use whenever someone is setting adopt up on a repository for the first time, taking over a system somebody else built, starting an engagement, or asks to "map this repo", "get adopt running here" or "find out what we don't know about this system". Load adopt-cli first for the contract and the human gates.
---

# Onboarding a system

**The outcome:** in about an hour, a store that knows what the system contains
(the map), what is already written down (ingest), what was decided and why
(harvest), and what nobody knows yet (gaps), plus a curated recall list that
proves the map did not quietly miss what matters.

**Your part** is everything the CLI refuses to guess: the archetype when
detection is unsure, the four permanent scope slugs, the three boundary answers,
and the list of identities an engineer would expect to find. Each of those waits
for a person. Everything else is `adopt`'s, and you only run it and read it.

Read `adopt-cli` first. It covers preflight, the JSON and exit-code contract, the
engagement workspace, and which commands need a yes. This skill assumes all of it.

## 0. Set up (once)

1. Run preflight (`adopt-cli`, section 1). Stop on `ready: false`.
2. `cd` into the client's repository. **Run every command from its root.** Ingest
   records each document by its path relative to where you ran it, and a re-run
   from elsewhere would create a duplicate.
3. Create the engagement workspace beside it, and hide the store from git without
   editing the client's tracked files:

   ```shell
   mkdir -p ../orders-api-adopt
   echo ".adopt/" >> .git/info/exclude
   ```

## 1. Detect — read-only

```shell
adopt detect . --json
```

Report `archetype`, `confidence` and `rules_fired`. The rules are the evidence:
which file fired which rule and why. Detection reads files and never executes
them.

- **Exit `2`, `DETECT_AMBIGUOUS`:** working as designed. Show the ranked `scores`
  and the rules, and ask the person which archetype it is. Their choice goes to
  `init --archetype`. Never pick one yourself: a wrong archetype is a different
  set of extractors, not a slightly wrong answer.
- The archetypes are `web`, `platform`, `lowcode`, `data` and `ai`. Extractor
  packs exist for `generic`, `web` and `ai`, so a platform, low-code or data
  system maps thinly. Say so up front rather than letting a sparse map surprise
  anyone.

## 2. The four slugs — a person decides

`firm/engagement/system/environment`, for example
`northwind/acme-erp/orders-api/prod`:

| Slug | Names |
|---|---|
| firm | the delivery firm doing the work (reuse the firm's existing slug if it has one) |
| engagement | this client engagement |
| system | the delivered system this repository is |
| environment | the environment this store describes: `prod`, `staging`, and so on. One store is one environment. |

Each slug is 2–48 characters of lowercase letters, digits and hyphens, and starts
and ends with a letter or digit. **They are permanent.** They go into every
identity URI, a rename raises `SCOPE_SLUG_IMMUTABLE`, and a slug is never
reissued. Propose all four, explain that they cannot change, and wait for an
explicit yes.

## 3. The observability boundary — a person answers

Ask the three questions as written, and record the answers exactly. There are no
defaults, on purpose: a tier nobody negotiated is a tier a client signs against
by accident.

1. `artifact_access`: *Are prompts, model configuration and source readable by us
   (in version control, or through an API we can call)?*
2. `deploy_signal`: *Do we receive what fires on deploy (CI/CD events, or an
   audit trail)?*
3. `safe_interaction`: *Can a safe test interaction run (a mock, sandbox or shadow
   path exists)?*

Write them to the workspace. Keep this file out of the repository, because
`map` would turn its three keys into identities:

```json
{ "artifact_access": true, "deploy_signal": false, "safe_interaction": true }
```

Strict booleans only; `"yes"` is rejected with `TIER_ANSWERS_INVALID`. The
answers decide the tier:

| artifact | deploy | safe interaction | Tier | Means |
|---|---|---|---|---|
| no | – | – | `T0` | Recommend **declining** the engagement: there is nothing to observe. |
| yes | no | no | `T1` | Artifacts only. |
| yes | yes | no | `T2` | Artifacts and deploy signal. |
| yes | no | yes | `T3` | Artifacts and behavioural probes. |
| yes | yes | yes | `T4` | Full, including continuous behavioural baselines. |

An `ai` system below `T3` is recorded as a boundary violation, because a prompt
change with no way to run the system can be detected but never evaluated.

## 4. Init — after the yes

```shell
adopt init . --scope northwind/acme-erp/orders-api/prod \
             --answers ../orders-api-adopt/answers.json --json
```

Add `--archetype <a>` when step 1 needed a human decision. Report `tier`,
`archetype_source` (`detected` or `declared`), and `unavailable_capabilities`,
which names what this tier costs.

## 5. Map

```shell
adopt map . --json
adopt map --report --json > ../orders-api-adopt/map-report.json
```

Read `identities_seen`, `files_walked`, `files_unmapped` and each extractor's
`status`. **Any failed extractor means exit `1`**, and every absence below it is
unreliable evidence, so stop and report it. A high `files_unmapped` is not an
error: the map states its own coverage rather than hiding it. For a mixed system
whose archetype names only its dominant half, add packs:
`adopt map . --packs generic,web,ai --json`.

Mapping is deterministic and idempotent: a second run over an unchanged tree
writes nothing. It never writes to the repository and never executes it.
`--report` and `--check-expected` re-run the map before reporting, which is why
both stay at the repository root.

Expect noise, and name it rather than hiding it. Dependency manifests, frontend
tooling config, and agent tooling the client's repository itself carries
(`.claude/`, `.agents/`, editor settings) all become identities. Report the
largest groups of noise to the person as waiver candidates (`adopt-capture`,
gap dispositions). Never delete or ignore files in the client's tree to shrink
the map.

## 6. The recall list — you propose, the person curates

A coverage ratio improves when you map more junk. A named list cannot be gamed:
either `POST /login/access-token` is in the map or it is not. So build the list
an experienced engineer would name from memory after ten minutes in this code,
**including the things you expected and did not see in the map**. Those are the
reason the list exists.

1. Read the code yourself: route definitions, settings and environment variables,
   CI workflows and scheduled jobs, prompts, tool schemas, pinned models, data
   models. Aim for 20–40 referents a new owner would ask about first.
2. Build each URI with the CLI. **Never hand-encode one**: double encoding is
   refused by design, and a single slash inside a key is data rather than
   structure.

   ```shell
   adopt identity build --scope northwind/acme-erp/orders-api/prod \
         --kind endpoint --key 'POST /v1/orders' --json
   adopt identity build --scope northwind/acme-erp/orders-api/prod \
         --kind config_key --namespace env --key DATABASE_URL --json
   ```

   Match the shapes the map already uses: open `map-report.json` and copy the
   kind and namespace of a mapped neighbour. `assets/expected-identities.template.txt`
   shows one example per common kind.
3. Show the list to the person. They add, remove and correct it. It records
   *their* expectations, not yours.
4. Save it in the workspace, never in the repository, and check it:

   ```shell
   adopt map --check-expected ../orders-api-adopt/expected-identities.txt --json
   ```

5. **Exit `4` names each miss, and a miss is a finding.** Either the URI is
   wrong (compare it with the nearest mapped identity using
   `adopt identity parse <uri> --json`), or the extractors do not see that
   referent. Report the second kind as a gap in the tool. Do not delete the entry
   to make the check pass, and do not look for a way to create the identity by
   hand: there is none, deliberately.

Two limits are known and stated rather than hidden. Endpoint paths are relative
to their router's mount point, because a prefix held in a runtime variable is
never resolved. A data field is namespaced by the class that *declares* it.

## 7. Ingest what the client already wrote

```shell
adopt ingest README.md docs/ --json
```

Only prose the client's team wrote, such as READMEs, `docs/`, ADRs, runbooks and
wiki exports they hand you. It lands `verified`, because it is their own shipped
words. A document citing an identity URI, or a path that resolves to exactly one
identity, is bound at once. A document that merely *names* something produces a
suggestion for a person in `adopt review`. Report `created`,
`bindings_created`, `suggestions` and `review_batch`.

Read the bindings, not only the count. A path-tier binding is structural, but a
README that merely *links* to another file, or names `main.py` in passing, still
binds, and then counts that identity as covered. List bindings that look loose
for the person. They are the judge of whether the document really describes the
thing. Do not edit client documents to change what binds.

**Anything you wrote yourself** (a summary of the code, notes from reading it)
is not ingested this way. Follow `adopt-cli` section 6: `adopt ingest --unverified`
if preflight reports that feature, otherwise hand it to the person as proposed
text.

## 8. Harvest decisions from history

```shell
adopt harvest --since v1.4.0 --json
```

`--since` takes a tag, branch or sha: commits reachable from `HEAD` and not from
that ref. Start at the last release tag and widen if the candidates are thin;
`git rev-list --max-parents=0 HEAD` is the root, for the whole history.
`HARVEST_RANGE_UNKNOWN` means the ref is not present locally; `git fetch --tags`
first, if the person agrees. Every candidate is **unverified** and carries its
commit evidence. None of it is knowledge until a person confirms it in
`adopt review`.

A long range harvests bot commits too ("Update release notes", dependency
bumps): each becomes a candidate. Count them and present them to the person as
likely rejections, together with the handful that look like real decisions. The
rejecting is theirs.

## 9. The first gaps

```shell
adopt gaps --json   > ../orders-api-adopt/gaps.json
adopt review --json > ../orders-api-adopt/review.json
```

Summarise `identities`, `covered` and `uncovered`, the largest groups of uncovered
kinds, and what is waiting in review (harvest candidates, suggested bindings).
Then try one question the ingested docs should answer:

```shell
adopt ask "How is the service configured for production?" --json
```

KNOWN, STALE or UNKNOWN are all useful results. Hand the daily loop of asking,
escalating, banking answers and confirming candidates to `adopt-capture`.

## 10. Report back

```text
System        northwind/acme-erp/orders-api/prod   (archetype web, tier T3)
Map           147 identities from 212 files (61 unmapped); all extractors ok
Recall        34 expected, 33 found; 1 miss: <uri> — extractor does not see it
Knowledge     12 documents ingested, 9 bound; 4 suggestions and 27 harvest candidates await review
Gaps          96 of 147 uncovered; largest: endpoints (41), config keys (23)
Needs a person  confirm candidates · answer the top gaps · <anything refused>
Workspace     ../orders-api-adopt/
```

Quote the numbers from the envelopes. Do not round them into impressions.

## When a step refuses

<!-- BEGIN GENERATED: errors DETECT_,TIER_,SCOPE_,MAP_,HARVEST_,KNOWLEDGE_ -->
| Code | Category | Exit |
|---|---|---|
| `DETECT_AMBIGUOUS` | usage | `2` |
| `HARVEST_NOT_A_GIT_REPO` | usage | `2` |
| `HARVEST_RANGE_UNKNOWN` | usage | `2` |
| `KNOWLEDGE_SOURCE_UNREADABLE` | usage | `2` |
| `MAP_EXPECTED_IDENTITY_MISSING` | integrity | `1` |
| `MAP_EXPECTED_LIST_UNREADABLE` | usage | `2` |
| `MAP_NO_PACK_FOR_ARCHETYPE` | usage | `2` |
| `MAP_TREE_TOO_LARGE` | policy | `3` |
| `SCOPE_SLUG_IMMUTABLE` | policy | `3` |
| `SCOPE_SLUG_INVALID` | usage | `2` |
| `SCOPE_SLUG_REUSED` | policy | `3` |
| `SCOPE_VIOLATION` | policy | `3` |
| `TIER_ANSWERS_INVALID` | usage | `2` |
| `TIER_DECLINE_RECOMMENDED` | policy | `3` |
| `TIER_INSUFFICIENT` | policy | `3` |
<!-- END GENERATED -->

Every refusal carries a `hint`; read it first. `TIER_DECLINE_RECOMMENDED` and
`TIER_INSUFFICIENT` are findings for the person, not obstacles to route around.
