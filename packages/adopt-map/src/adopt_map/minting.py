"""`SurfaceFact` -> identity URI -- contracts §3, implementation spec §5.3.

Normalization in the exact §3.2 order, then `build_uri()`. Nothing here
assembles a URI; `uri-construction` in `importlinter.ini` proves it.

**Rule 2 is the load-bearing one.** Path parameters collapse to `{name}`
whatever syntax the source used, so a Django `<int:id>` and an OpenAPI `{id}`
describing the same endpoint mint the *same* URI. Without it the identity forks
and coverage silently double-counts -- one referent, two rows, a denominator
that grows while the system stands still.

**Normalization never percent-encodes** (`02` §3.2). `build_uri()` encodes
exactly once and refuses input that already carries an escape, because a key
whose literal text is ``a%20b`` and a double-encoding of ``a b`` are the same
string and no parser can separate them afterwards. So an extractor hands
``GET /api/v1/orders/{id}`` through and never ``GET%20%2Fapi...``.

**The environment segment comes from `ResolvedScope` and from nowhere else.**
That is `03` §5.3 invariant 2 and it is what makes PRD F6 structural: this module
takes a `Scope` and a fact, and a fact has no field that could name an
environment.
"""

import re
import unicodedata
from typing import Final

from adopt_identity import build_uri
from adopt_map.schemas.surface import SurfaceFact
from adopt_model._enums import IdentityKind
from adopt_obs import AdoptError, ErrorCode
from adopt_scope import Scope

__all__ = [
    "ANY_METHOD",
    "PARAMETER_PLACEHOLDER",
    "mint",
    "normalize_local_key",
]

#: How every path parameter renders after rule 2, whatever syntax declared it.
PARAMETER_PLACEHOLDER: Final[str] = "{{{name}}}"

#: The five parameter syntaxes `02` §3.2 rule 2 names, in one alternation so
#: that a single pass cannot leave one syntax un-normalized behind another.
#:
#: * ``:id``          -- Rails, Express, Sinatra
#: * ``<int:id>``     -- Django, with an optional converter prefix
#: * ``{id}``         -- OpenAPI, FastAPI, Spring
#: * ``[id]``         -- Next.js, SvelteKit
#: * ``$id``          -- Azure Functions, some Go routers
#:
#: **The name is preserved and only the syntax is normalized** (rule 2). Two
#: frameworks that disagree about the *name* describe two different endpoints as
#: far as this build is concerned, and merging them would be a guess.
_PARAMETER_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?::(?P<colon>[A-Za-z_][A-Za-z0-9_]*))          # :id
  | (?:<(?:[A-Za-z_][A-Za-z0-9_]*:)?                # <converter:
        (?P<angle>[A-Za-z_][A-Za-z0-9_]*)>)         #   id>
  | (?:\{(?P<brace>[A-Za-z_][A-Za-z0-9_]*)\})       # {id}
  | (?:\[(?P<bracket>[A-Za-z_][A-Za-z0-9_]*)\])     # [id]
  | (?:\$(?P<dollar>[A-Za-z_][A-Za-z0-9_]*))        # $id
    """,
    re.VERBOSE,
)

#: Rule 5: query strings, fragments and matrix parameters are excluded from an
#: `endpoint` key. A URI naming one query string is a URI per *call*, not per
#: referent, and the identity set would then grow without bound.
_EXCLUDED_TAIL_RE: Final[re.Pattern[str]] = re.compile(r"[?#;].*$")

#: Rule 4: repeated separators collapse.
_REPEATED_SLASH_RE: Final[re.Pattern[str]] = re.compile(r"/{2,}")

#: What a render-bound locator looks like -- Bet 1, `02` §3.1 rule 3, B1-CR-11.
#: A `ui_component` key must be a stable id or `role:accessible-name`; anything
#: carrying CSS, XPath or coordinate syntax is refused at the mint rather than
#: written and regretted.
_SELECTOR_SHAPED_RE: Final[re.Pattern[str]] = re.compile(
    r"""
      ^\s*[.#]                 # .class / #id at the head of the key
    | ^\s*/{1,2}\*?\w*\[       # /html/body[1] or //div[
    | \[\s*\d+\s*\]            # a positional index: div[2]
    | \bnth-child\b
    | ::\w                     # a pseudo-element
    | \bxpath\b
    | ^\s*\(?\s*-?\d+\s*,\s*-?\d+\s*\)?\s*$   # a coordinate pair
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: The kinds whose `local_key` is a URL path and therefore takes rules 2-5.
#: `db_field`, `symbol` and the rest carry punctuation that is *data*: collapsing
#: a `//` inside a symbol name would rename the referent.
_PATH_SHAPED_KINDS: Final[frozenset[str]] = frozenset({"endpoint"})

#: Rule 3: an HTTP method uppercases. Applied only to the leading token of an
#: `endpoint` key, because `02` §3.1 fixes that key's form as
#: ``<METHOD> <normalized-path>`` -- uppercasing the whole key would destroy the
#: case of a language-declared identifier, which rule 3 explicitly preserves.
#: `ANY` is a method token this build mints and no RFC does: a Django `path()`
#: and an Express `app.use` route *every* method to their handler, so `ANY` is a
#: statement about what the framework declares rather than a guess at `GET`.
#:
#: **It lives here, in the module that owns the key grammar**, because it has to
#: appear in the pattern below -- and it was not here first. S1.4 defined it in
#: the extractor pack instead, where the normalizer could not see it, and the
#: consequence was `ANY admin` escaping rule 4's leading-slash canonicalization
#: while `GET /admin` received it: **B1-CR-66's own fork, reintroduced by a token
#: the normalizer did not know**. Found by scoring a real run against a labeled
#: set that spelled the route with a slash.
ANY_METHOD: Final[str] = "ANY"

_HTTP_METHOD_RE: Final[re.Pattern[str]] = re.compile(
    rf"^(get|put|post|head|patch|trace|delete|connect|options|{ANY_METHOD})(\s+)",
    re.IGNORECASE,
)


def _collapse_parameters(value: str) -> str:
    """Rule 2, one pass over all five syntaxes."""

    def replace(match: re.Match[str]) -> str:
        name = next(group for group in match.groups() if group is not None)
        return PARAMETER_PLACEHOLDER.format(name=name)

    return _PARAMETER_RE.sub(replace, value)


def normalize_local_key(kind: str, local_key: str) -> str:
    """Apply contracts §3.2 rules 1-5, in that order. **Never encodes.**

    Args:
        kind: The fact's `identity_kind`; selects whether the path rules apply.
        local_key: The extractor's raw key.

    Returns:
        The normalized key, ready for `build_uri()`.
    """
    # 1. Unicode NFC. Always, for every kind: two spellings of one identifier
    #    are one referent, and `identity.uri` is UNIQUE.
    value = unicodedata.normalize("NFC", local_key)

    if kind not in _PATH_SHAPED_KINDS:
        return value

    # 3 (first half). The method, before the parameter pass, so a method that
    #    happens to look like a parameter cannot be rewritten by rule 2.
    value = _HTTP_METHOD_RE.sub(lambda m: m.group(1).upper() + m.group(2), value)

    # 5. Query, fragment and matrix parameters, before rule 4: a trailing `?x=1`
    #    would otherwise protect the trailing slash rule 4 is meant to strip.
    value = _EXCLUDED_TAIL_RE.sub("", value)

    # 2. Parameter syntax.
    value = _collapse_parameters(value)

    # 4. Repeated separators collapse; one trailing slash is stripped unless the
    #    path is exactly "/"; and the path carries exactly one **leading** slash.
    #
    #    The leading half is B1-CR-66, and it is rule 2's own argument one
    #    character to the left. Django's `path("api/v1/orders/")` has no leading
    #    slash because Django strips it before matching; FastAPI's
    #    `@app.get("/api/v1/orders")` has one; an OpenAPI document's `paths` keys
    #    always have one. Without this, `GET api/v1/orders` and
    #    `GET /api/v1/orders` are two URIs for one endpoint -- exactly the fork
    #    §3.2's own note on rule 2 says the normalization exists to prevent, and
    #    it double-counts in every coverage figure downstream.
    #
    #    Applied only where the key **has** an HTTP method prefix, because that is
    #    what identifies an http-namespace key. A grpc key
    #    (`orders.OrderService.GetOrder`), a graphql key (`Query.orders`) and an
    #    event topic are all `endpoint` too, and none of them is a URL path.
    value = _REPEATED_SLASH_RE.sub("/", value)
    head, separator, path = value.partition(" ")
    if separator and _HTTP_METHOD_RE.match(value):
        path = "/" + path.lstrip("/")
        if path.endswith("/") and path != "/":
            path = path.rstrip("/")
        value = f"{head}{separator}{path}"
    elif separator and path.endswith("/") and path != "/":
        value = f"{head}{separator}{path.rstrip('/')}"
    elif not separator and value.endswith("/") and value != "/":
        value = value.rstrip("/")

    return value


def _reject_selector_shaped(kind: str, local_key: str) -> None:
    """Bet 1: a render-bound locator never reaches a URI.

    Raises:
        AdoptError: ``MAP_URI_CONSTRUCTION_BYPASS`` when a `ui_component` key is
            selector-shaped. The code names the right thing: a selector reaching
            the writer means a URI was derived from something that is not an
            identity, which is the P0 this rule exists for.
    """
    if kind != "ui_component":
        return
    if _SELECTOR_SHAPED_RE.search(local_key):
        raise AdoptError(
            ErrorCode.MAP_URI_CONSTRUCTION_BYPASS,
            message=f"the ui_component key {local_key!r} is selector-shaped",
            hint="`ui_component` URIs are minted only from locator rung 1 (a stable "
            "component or test id) or rung 2 (accessibility role plus stable "
            "accessible name) -- B1-CR-11. A CSS selector, XPath or coordinate "
            "binds to rendering, and rendering changes without the referent "
            "changing: the binding would break for a reason that is not a change.",
        )


def mint(scope: Scope, fact: SurfaceFact) -> str:
    """The canonical URI for `fact`, in `scope`'s environment.

    Args:
        scope: The run's resolved scope. **The only source of the four leading
            segments**, which is what makes environment isolation structural.
        fact: The extractor's fact. Supplies `(kind, namespace, local_key)` and
            nothing else that reaches the URI.

    Returns:
        The canonical URI, encoded exactly once by `build_uri()`.

    Raises:
        AdoptError: ``MAP_URI_CONSTRUCTION_BYPASS`` for a selector-shaped
            `ui_component` key; any ``URI_*`` code from the builder --
            ``URI_DOUBLE_ENCODED`` when an extractor pre-encoded its key, which
            is a real extractor defect and not something to normalize away.
    """
    kind: IdentityKind = fact.identity_kind
    _reject_selector_shaped(kind, fact.local_key)
    normalized = normalize_local_key(kind, fact.local_key)
    # A bare string, deliberately: `02` §3.1 rule 6 fixes every Build 1 kind's
    # key as **one** segment whose punctuation is data. Passing a sequence here
    # would make `/` structure instead, and no parser could tell afterwards.
    return build_uri(scope, kind, fact.namespace, normalized)
