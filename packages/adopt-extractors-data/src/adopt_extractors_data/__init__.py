"""`adopt-extractors-data` -- the data & analytics pack (`03` §5.10, `05` S1.6).

Two readers over one archetype: a dbt project's models and sources, and the
semantic models and metrics built on them. `01` F8.5 files all four as
`metadata_component` **plus lineage relations**, which makes this the only pack
in the build that emits `derives_from` -- and the only archetype where the
upstream direction is written down by the client rather than inferred.

**The fifth archetype, and the same thirteen kinds.** A dbt model and a
Salesforce field are both `metadata_component`; the namespace is what tells them
apart. That is `03` §5.10's whole claim, and S1.6 is where it is finally tested
across five archetypes rather than argued.

**No dbt import and no warehouse connection.** A dbt project is YAML and SQL
text; `manifest.json` is a *compiled* artefact that requires running dbt against
a warehouse profile, which `02` §7 obligation 1 forbids and `01` §10 rules out.
So this pack reads the source of truth a client's repository actually holds.
"""

from adopt_map.schemas import Extractor

from adopt_extractors_data.dbt import DbtExtractor
from adopt_extractors_data.semantic_model import SemanticModelExtractor

__all__ = ["DbtExtractor", "SemanticModelExtractor", "pack"]


def pack() -> tuple[Extractor, ...]:
    """Every `data` extractor a real run may use, in manifest-id order."""
    return (DbtExtractor(), SemanticModelExtractor())
