# `stub-tree` — the smallest tree a move can be demonstrated on

`05` S1.2's Final Output Validation asks for a **rename** of a stub fixture file
followed by a re-run. `common.stub` reads nothing and emits four fixed facts, so
no rename could change its output (B1-CR-49); `common.stub_tree` reads this tree
and emits one `symbol` fact per top-level declaration.

That makes the six move cases reachable through the real CLI rather than only
through the writer's API:

| Do this | Expect |
|---|---|
| Run twice, unchanged | `revisions_written` all zero |
| `git mv orders/api.py orders/views.py` | one `moved` revision with `alias_of_identity_id` |
| Edit a signature **and** rename | no move, one `conflict` row |
| Copy a file so two carry identical declarations | no move, one `conflict` row |

Nothing here is imported or executed — the extractor recovers declarations with a
line-anchored regex over the file's text. The bodies exist only so the files read
like source rather than like a fixture.
