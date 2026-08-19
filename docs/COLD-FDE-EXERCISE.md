# The cold-FDE exercise — the only honest test of "useful"

**Status: not performed. It requires a person, and no coding agent can stand in for
one.** `05` S1.8 asks for it in those terms: *"an unfamiliar engineer answers five
prepared structural questions from `surface.md` alone, timed."* This document is the
protocol, so that the exercise is a thing somebody can run rather than a line in a
sprint plan — and so that when it is run, the answer is comparable to the next one.

## Why it cannot be simulated

**G3** is the gate the whole free layer is justified by: *a cold engineer on a real
repo reaches something useful in ≤ 1 hour.* Every other instrument in this build
measures a property of the artefact — recall against a labelled set, a budget in
seconds, a URI set that does not change. None of them measures whether a human who
has never seen the system can answer a question from the document. An agent asked
to score that grades its own output against its own reading, which is not evidence
of anything.

Two constants are **blocked on this exercise and on nothing else**, and both say so
in `docs/pack/CONSTANT-RATIFICATION.md`:

- `MAP_STAGE1_REQUIRED_FAMILIES` — what "usable map" means for the north-star
  metric. It is currently `endpoint,db_field,job,config_key,prompt`, which is a
  reasonable guess about what people reach for first and is **only** a guess.
- `MAP_FIRST_SCREEN_LIST_MAX` — how many entries a list can hold before a reader
  stops reading it.

## Protocol

**Participant.** One engineer who has never worked on the target system and did not
write any part of this build. A person who has read this repository's documents is
not cold.

**Setup.** Run `adopt map` against one repository from `perf/soak/corpus.json`
(or, better, a real client system). Give the participant `surface.md` and nothing
else — no repository access, no search, no chat. The exercise measures the document.

**Timing.** Start the clock when they open the file. Record the time to each
answer, and stop at 60 minutes whether or not the questions are finished. The
number that matters is not "did they finish" but *how far a cold reader gets before
the hour is gone*.

**The five questions.** Structural, answerable from a map, and stated before the
run rather than chosen after it:

1. Which HTTP endpoint handles order creation, and which symbol implements it?
2. Which database fields does that path touch?
3. Which configuration keys change its behaviour, and which of them can change
   without a commit?
4. Which background jobs run against the same data?
5. Name one thing this map explicitly says it could **not** determine.

Question 5 is the one worth watching. A map that never admits a gap will get a
confident wrong answer, and `01`'s *"silence beats guessing"* is the invariant it
tests.

## What to record

| Field | Why |
|---|---|
| Time to each answer, and the total | The north-star metric is a duration |
| Correct / partial / wrong, judged by someone who knows the system | An answer given fast and wrongly is worse than no answer |
| Which section of `surface.md` they used for each | This is what ratifies `MAP_STAGE1_REQUIRED_FAMILIES` — the families they actually reached for |
| Where they scrolled past something and had to come back | This is what ratifies `MAP_FIRST_SCREEN_LIST_MAX` |
| Every question they wanted to ask and could not | The next sprint's backlog, in the participant's own words |

Archive the result beside the soak it used: `perf/soak/<date>/cold-fde.md`.

## The failure condition, stated in advance

If a cold engineer cannot answer three of the five inside the hour, **G3 has not
been met**, and `01` §1.4's rule applies: *anything threatening G3 is cut or
deferred, not negotiated.* Recording that outcome honestly is the entire value of
running the exercise; an exercise whose result can only be "we passed" is a
ceremony.
