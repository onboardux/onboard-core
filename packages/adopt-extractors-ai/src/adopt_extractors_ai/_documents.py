"""Documents that declare themselves, and the reason this module is shared.

`ai.retrieval` and `ai.evalsets` both read structured documents, and both face
the problem B1-CR-67 found the hard way: **`common.config` reads every YAML,
JSON and TOML file in the tree**, so a retrieval configuration or an eval set is
claimed twice unless somebody decides which extractor owns it.

The rule, and it is the same one `common.config` already applies to an OpenAPI
document: **a document that declares itself is owned by the extractor whose
subject it declares.** `retrieval:` at the top level makes a document
`ai.retrieval`'s; `evalset:` (or `evals:`) makes it `ai.evalsets`'. Anything
else stays a configuration source and `common.config` keeps it.

Declaring keys rather than filenames, because a filename is a habit and a
top-level key is a statement: `config/retrieval.yaml` and `deploy/rag.yml` are
the same document, and `settings.yaml` with a `retrieval:` section is not.
"""

import json
import tomllib
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, Final

import yaml
from adopt_map.documents import OWNED_DOCUMENT_KEYS

__all__ = ["EVALSET_KEYS", "RETRIEVAL_KEYS", "declared_section", "load_document"]

#: The top-level keys that hand a document to `ai.retrieval`, read from
#: `adopt_map.documents` rather than restated: `common.config` skips exactly the
#: documents this pack claims, and two lists would be one edit from disagreeing
#: -- with the symptom being a silently double-claimed file rather than an error.
RETRIEVAL_KEYS: Final[tuple[str, ...]] = tuple(
    key for key, owner in OWNED_DOCUMENT_KEYS if owner == "ai.retrieval"
)

#: The top-level keys that hand a document to `ai.evalsets`, same source.
EVALSET_KEYS: Final[tuple[str, ...]] = tuple(
    key for key, owner in OWNED_DOCUMENT_KEYS if owner == "ai.evalsets"
)

_YAML_SUFFIXES: Final[tuple[str, ...]] = (".yaml", ".yml")


def load_document(path: str, text: str) -> Mapping[str, Any] | None:
    """The document as a mapping, or `None` when it is not one this pack reads.

    **`yaml.safe_load` constructs no Python objects** -- the same choice
    B1-CR-67's repair made in `common.config`, and the reason a client's YAML
    cannot execute anything on the way in.
    """
    suffix = PurePosixPath(path).suffix.lower()
    try:
        if suffix in _YAML_SUFFIXES:
            loaded = yaml.safe_load(text)
        elif suffix == ".json":
            loaded = json.loads(text)
        elif suffix == ".toml":
            loaded = tomllib.loads(text)
        else:
            return None
    except (yaml.YAMLError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError):
        # A malformed document produces no facts and no exception. `02` §7
        # obligation 8 makes failure local, and `01` F9.3 makes silence the
        # honest answer where a parse declines -- the run records the gap.
        return None
    return loaded if isinstance(loaded, Mapping) else None


def declared_section(
    document: Mapping[str, Any], keys: tuple[str, ...]
) -> Mapping[str, Any] | None:
    """The section `keys` names, or `None`.

    Ordered by `keys` rather than by the document, so two spellings in one file
    resolve the same way on every run (`02` §7 obligation 3).
    """
    for key in keys:
        section = document.get(key)
        if isinstance(section, Mapping):
            return section
    return None
