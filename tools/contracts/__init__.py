"""Custom `import-linter` contract types for the five rules an import graph
cannot express.

Eight of the thirteen contracts in implementation spec §5.1 are import-graph
rules and are declared natively in `importlinter.ini`. The remaining five are
statements about *source text*, not about imports:

* `no-foreign-tables` -- no `CREATE TABLE` outside `schema/migrations/**`
* `no-revision-update` -- no `UPDATE` against any `*_revision` table
* `no-covered-cache-write` -- only `adopt_coverage` writes the coverage cache
* `workflow-body-purity` -- no clock, randomness, network, model call or I/O
  inside a `@workflow` body
* `uri-construction` -- a URI is assembled by `build_uri()` and nowhere else
  (Build 1, `02` §1.2; the fifth rule, added in S1.1)
* `no-covered-cache-read` -- only `adopt_map.coverage` names `covered_cache`
  inside `adopt_map` (Build 1, `05` S1.3; the sixth rule, added in S1.3)

They live here so that all fourteen are declared in one configuration file and
checked by one command. Splitting them across a second tool is how a rule ends
up enforced in a job nobody runs.

The first four are **live from S0**, before the code they constrain exists; the
fifth and sixth land with the first Build 1 code each constrains, in the same
change. A gate switched on after the code lands is a gate that starts life with
exemptions.
"""

from tools.contracts.purity import WorkflowBodyPurityContract
from tools.contracts.source_rules import (
    NoCoveredCacheReadContract,
    NoCoveredCacheWriteContract,
    NoForeignTablesContract,
    NoRevisionUpdateContract,
)
from tools.contracts.uri_construction import UriConstructionContract

__all__ = [
    "NoCoveredCacheReadContract",
    "NoCoveredCacheWriteContract",
    "NoForeignTablesContract",
    "NoRevisionUpdateContract",
    "UriConstructionContract",
    "WorkflowBodyPurityContract",
]
