"""Key schemes, one registry -- which `identity_kind` and namespace each kind of
referent gets, and how its local key is built.

**Read this before adding an extractor.** A kind/namespace choice made here
becomes a persisted URI the moment anyone runs `adopt map` on a real engagement,
and Build 0's rule is that a URI is never rewritten. Changing a scheme later does
not rename anything: it mints a second identity for the same referent and leaves
the first one orphaned. It is cheap today and expensive forever after.

**Why the existing thirteen-value `identity_kind` enum is not extended**
(decision D4). Post-`0.3.0` the enums are `CHECK` constraints under additive-only
migration discipline, and evolving them is precisely the ALTER sequence this
rebuild deleted. Every Build 1 target maps onto an existing value with a
namespace carrying the distinction, losslessly:

| Extracted referent          | kind                 | namespace           |
|-----------------------------|----------------------|---------------------|
| HTTP endpoint               | `endpoint`           | framework or `None` |
| Schema / migration field    | `db_field`           | table name          |
| Config key                  | `config_key`         | source file stem    |
| Environment variable        | `config_key`         | `env`               |
| Scheduled job               | `job`                | scheduler           |
| CI workflow                 | `job`                | `ci`                |
| Declared dependency         | `metadata_component` | `dependency:<eco>`  |
| File of interest            | `metadata_component` | `file`              |
| Middleware / auth boundary  | `symbol`             | `middleware`        |
| Agent graph node            | `symbol`             | `agent_graph`       |
| Prompt                      | `prompt`             | `None`              |
| Tool / function schema      | `tool_schema`        | `None`              |
| Pinned model identifier     | `model_pin`          | provider or `None`  |
| Retrieval / data source     | `retrieval_config`   | `None`              |

**Keys are sequences, and the distinction is load-bearing.** `POST /v1/orders`
is *one* segment whose slash is data; `billing/charges/refund` is *three*
segments whose slashes are structure. Build 0's URI builder renders each segment
separately, so handing it one string where three were meant produces a different,
permanently different, referent.
"""

from collections.abc import Sequence
from pathlib import PurePosixPath

__all__ = [
    "AGENT_GRAPH_NAMESPACE",
    "CI_NAMESPACE",
    "ENV_NAMESPACE",
    "FILE_NAMESPACE",
    "MIDDLEWARE_NAMESPACE",
    "dependency_namespace",
    "endpoint_key",
    "model_provider_namespace",
    "module_key",
    "path_key",
]

#: Namespaces that are literals rather than derived. Named here so an extractor
#: cannot spell one two ways -- `env` and `environment` would be two registries
#: of environment variables that never reconcile.
ENV_NAMESPACE = "env"
CI_NAMESPACE = "ci"
FILE_NAMESPACE = "file"
MIDDLEWARE_NAMESPACE = "middleware"
AGENT_GRAPH_NAMESPACE = "agent_graph"


def dependency_namespace(ecosystem: str) -> str:
    """`dependency:<ecosystem>` -- `pypi`, `npm`, `cargo`, and so on.

    The ecosystem is part of the namespace rather than the key because `requests`
    on PyPI and `requests` on npm are different referents, and a shared key would
    make one of them silently overwrite the other's coverage.
    """
    return f"dependency:{ecosystem}"


def model_provider_namespace(provider: str | None) -> str | None:
    """The provider a model pin is namespaced by, spelled one way.

    `None` when the provider is unknown, which is a real answer: a bare
    `llama-3.1-70b` is served by a dozen hosts and inventing one would record a
    guess as a fact. `google_genai` and `google` are the same vendor written two
    ways by two libraries, and normalizing them here is the whole reason this
    lives in the key registry rather than in the extractor -- two spellings
    would be two permanent namespaces for one provider.
    """
    if provider is None:
        return None
    normalized = provider.strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized) or None


#: Provider spellings that name one vendor. Deliberately small: only pairs where
#: two libraries genuinely disagree about the name of the same company.
_PROVIDER_ALIASES = {
    "azure": "azure_openai",
    "google_genai": "google",
    "mistralai": "mistral",
    "vertexai": "google",
}


def endpoint_key(method: str, path: str) -> Sequence[str]:
    """One segment: `POST /v1/orders`.

    The slash is **data** here. An endpoint's path is a single opaque address,
    not a structural hierarchy -- splitting it would make `/v1/orders` and
    `/v1/orders/{id}` share ancestry they do not have, and would make the key
    depend on how many slashes a framework happened to use.
    """
    return (f"{method.upper()} {path}",)


def path_key(path: str) -> Sequence[str]:
    """A file path as structural segments: `src/api/orders.py` -> three.

    Structure, not data: these segments *are* a hierarchy, and rendering them
    separately is what lets a later build reason about a directory without
    string-matching a URI.
    """
    return tuple(PurePosixPath(path).parts)


def module_key(path: str, *symbol: str) -> Sequence[str]:
    """A dotted module derived from a path, plus optional symbol segments.

    `src/app/mw.py` + `RequireAuth` -> `('src', 'app', 'mw', 'RequireAuth')`.
    The suffix is dropped: `mw.py` and a future `mw.pyi` describing the same
    referent should not be two identities.
    """
    pure = PurePosixPath(path)
    parts = [*pure.parent.parts, pure.stem] if pure.parent.parts else [pure.stem]
    return (*parts, *symbol)
