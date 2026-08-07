# `detect-001` golden set

AI spec §7.2. **Informational at Build 0 — this is not a gate.**

```sh
uv run python tests/golden_prompts/detect_001/report.py                          # NOT RUN, and says so
uv run python tests/golden_prompts/detect_001/report.py --adapter local_openai   # measures
```

## What it measures

| Metric | Definition |
|---|---|
| Top-1 accuracy | fraction of cases whose `primary` equals the hand label |
| Calibration | mean confidence on **wrong** answers below mean confidence on right ones |

Calibration is the metric that matters more here, and it is the one accuracy cannot
see. `04` §5.1 rule 2 tells the model that low confidence is a correct answer and
guessing is not; PRD §8 puts a human behind every write. A model that is
*confidently wrong* defeats both — the human reads a high confidence and accepts.
One that is *uncertainly wrong* is behaving exactly as instructed.

## Why it does not gate

`04` §7.2 states it, and the reason is worth keeping in front of whoever wants to
turn it on: **a 15-item set cannot support a blocking threshold.** Any number chosen
for it would be invented, and a gate whose threshold is invented is fake precision
that later gets quoted as a measurement. The gate arrives with the sample size, or
the flag stays off — and the flag is off (`ADOPT_FEATURE_AGENT_DISAMBIGUATION`
defaults to `0`), with a human accepting every write, which is what makes
"informational" a safe answer rather than a deferred obligation.

It follows that this is a **script and not a test.** Under `pytest` it would need
either a network or a skip, and a skipped eval that renders as a passing test is
precisely the failure this file is written against.

## The set

Fifteen cases, three per archetype, every one **ambiguous** — no archetype's score
clears `DETECT_CONFIDENCE_MIN`, which is the only situation in which `detect-001` is
ever called. A golden set of *unambiguous* systems would measure a prompt that never
runs on them.

`archetype` is the **hand label**: what a human who looked at the real system
concluded. It is deliberately not what the heuristics said — the disagreement is the
case.

## Two decisions about the data

**Each case is recorded as evidence, not as a tree.** The pass only ever sees
scores, rule names, paths and a bounded listing (`04` §4 step 4), so evidence is the
whole input. Recording trees instead would make the eval inputs a function of the
detector's current scoring, and a change to one rule weight would silently change
what this set measures — the eval would drift with the thing it is meant to hold
still.

**Listings are anonymized.** No real client path enters this repository. `adopt-core`
is Apache-2.0 and goes public at `0.3.0`, so a golden set carrying real structure
would publish a client's tree permanently — and no later commit withdraws it.

## Adding a case

1. It must be **ambiguous**: no score at or above `DETECT_CONFIDENCE_MIN` (`0.70`).
2. Give it a hand label and a one-line `notes` saying *why* that label is right. The
   note is the part a later reader needs; the label alone is unfalsifiable.
3. Anonymize every path.
4. Keep the per-archetype counts even. A set weighted toward one archetype measures
   a model's prior, not its reasoning.

## What a change to `detect-001` requires

`04` §5.2 rule 4: **a new prompt version requires its golden-set result table in the
PR.** A prompt merged without eval evidence is a merge-blocker. That rule is live
now even though the metric does not gate — the table is evidence a human read, not a
threshold a machine checked.
