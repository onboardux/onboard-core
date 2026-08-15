"""Loading a dbt document, and which of this pack's readers owns it.

The same shape `adopt_extractors_ai._documents` has, for the same reason
(B1-CR-74): `common.config` reads every YAML file in a tree, so a dbt schema file
is claimed twice unless one rule says who owns it -- and that rule lives in
`adopt_map.documents`, because a pack may not import another pack.

**dbt's keys needed a co-key and S1.5's did not.** `retrieval:` names its own
subject; `models:` is one of the most common top-level keys in YAML anywhere, so
claiming it alone would make `common.config` skip an ordinary settings file in a
web repository and lose its keys silently. The rows in `adopt_map.documents`
therefore require `version:` beside the dbt resource keys and `profile:` beside a
project file's `name:`.
"""

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, Final

import yaml
from adopt_map.documents import OWNED_DOCUMENT_KEYS

__all__ = ["DBT_OWNER", "SEMANTIC_OWNER", "declaring_keys_for", "load_yaml"]

DBT_OWNER: Final[str] = "data.dbt"
SEMANTIC_OWNER: Final[str] = "data.semantic_model"

_YAML_SUFFIXES: Final[tuple[str, ...]] = (".yaml", ".yml")


def declaring_keys_for(owner: str) -> tuple[str, ...]:
    """The declaring keys `adopt_map.documents` gives `owner`, first key per row.

    Read from that table rather than restated here: `common.config` skips exactly
    the documents this pack claims, and two lists would be one edit away from
    disagreeing -- with a silently double-claimed file as the symptom rather than
    an error.
    """
    return tuple(keys[0] for keys, claimant in OWNED_DOCUMENT_KEYS if claimant == owner)


def load_yaml(path: str, text: str) -> Mapping[str, Any] | None:
    """A YAML document as a mapping, or `None`.

    **`yaml.safe_load` constructs no Python objects**, which is what keeps a
    client's YAML from executing anything on the way in (B1-CR-67's repair, and
    `02` §7 obligation 1). A malformed document yields `None` and no exception:
    obligation 8 makes failure local, and `01` F9.3 makes silence the honest
    answer where a parse declines.
    """
    if PurePosixPath(path).suffix.lower() not in _YAML_SUFFIXES:
        return None
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, Mapping) else None
