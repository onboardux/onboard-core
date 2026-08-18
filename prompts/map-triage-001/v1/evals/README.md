# `evals/` — map-triage-001@1

`04` §7: *"every prompt version directory carries `evals/` and cannot register
without them"*. This directory is that carrier, and `scripts/prompt_evals.py`
is what makes the sentence a gate rather than a convention — Build 0's
`adopt_agent.skills.load_skill` is a **protected** module and does not check
(B1-CR-81).

`manifest.json` names the `04` §8 suites that grade this prompt and the golden
set they read. The cases themselves live under `fixtures/golden/`, not here: a
golden set is corpus, it is shared between adapters, and copying it beside each
prompt would put the same ground truth in two places one edit apart.

**The digest covers this directory** (`prompts/README.md`), so changing what
grades a prompt changes `skill_sha256` — which is the property that makes
"a prompt merged without eval evidence is a merge-blocker" checkable after the
fact rather than only at review.
