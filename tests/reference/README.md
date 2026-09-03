# Reference repositories — the recall floor's subjects

Two **real** repositories, pinned by commit, each with a curated
`expected-identities.txt`. v6.1 §6's Build 1 demo requires them: *"on two real
repositories (one web service, one AI deployment — real code, not fixtures)"*.

| Directory | Repository | Archetype |
|---|---|---|
| `fullstack-fastapi/` | `fastapi/full-stack-fastapi-template` | web |
| `chat-langchain/` | `langchain-ai/chat-langchain` | ai |

**Nothing here is vendored.** `repo.json` records the URL and the commit; the
`map-journey` CI job clones each at its pin into a scratch directory and
`tests/e2e/test_map_journey.py` runs the demo against it. Copying third-party
source into an Apache-2.0 repository would be a licensing decision nobody made,
and it would freeze a tree whose whole value is that it is somebody else's.

**The pin is load-bearing, and the test asserts it.** A curated list of URIs is
only true of one tree. Checked out at a different commit the same list would
either fail for reasons that are not defects, or — worse — pass while measuring
a repository nobody curated. `test_map_journey` reads `commit` from `repo.json`
and refuses a clone that is not at it.

**Moving a pin is a curation task, not a bump.** Re-run the map at the new
commit, re-verify every entry against the store, and record what changed. The
list encodes a human's belief about a specific tree; reference repository #1's
first list carried two environment variables its author wrote from memory that
did not exist at that pin, which is the argument for checking rather than
trusting.
