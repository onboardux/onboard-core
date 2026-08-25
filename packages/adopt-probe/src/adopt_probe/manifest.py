"""The probe file: parsed, validated, and rendered into what the store holds.

A probe is **data** (v6.1 D2, upheld by the F8 audit). This module is the whole
of what turns a file on disk into something the runner will execute, and it
contains no I/O of its own beyond reading the text it was handed -- the parse is
separated from the socket by a package boundary and an import contract.

**Two documents live in one file, and the split is the schema's.**
`probe_definition_revision` carries `capability_manifest` and `interaction` as
two `NOT NULL` text columns, so this module renders exactly two strings:

* `capability_manifest` -- the authored document **verbatim**, because contracts
  §7 says the manifest is "stored whole ... as TEXT" and because
  `validate_capability_manifest` (Build 0) is the thing that reads it. Storing a
  re-serialization would mean the bytes a human approved and the bytes we kept
  are different bytes.
* `interaction` -- the canonical JSON rendering of the executable half (steps,
  `exercises`, `diff_method`), sorted keys and no spaces, which is `adopt_export`'s
  rendering discipline applied for the same reason: two adds of the same probe
  must produce byte-identical revisions or idempotence is a coin toss, and Build
  6 compares these strings to decide whether *the probe* changed.

**Validation is strict about unknown keys**, everywhere, at every level. A probe
is a security document: a typo'd `netwrok:` block that parsed as "no network
declaration" would be a probe with no allow-list, and a mistyped `expect:` key
would be an invariant nobody checks that reports success forever. Strictness here
is the egress posture, not fastidiousness.

**What this module refuses in v1**, each with the register row that would lift it:

* a `diff_method` other than `exact` -- `embedding_sim` needs the vector seam
  (D5's trigger), `llm_judge` needs judge machinery, `contract_delta` needs
  contract sensing. All three are representable in the schema and none is built.
* a secret ref that is not `env:NAME` -- v6.1 §6 B5 says "secret refs by env
  name"; a `vault://` reference names custody we do not have.
* a declared `runtime.max_seconds` above `PROBE_TIMEOUT_SECONDS` -- the ceiling
  exists so a probe cannot declare its way out of the wall clock.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import yaml

from adopt_const import PROBE_DIFF_SIM_THRESHOLD, PROBE_TIMEOUT_SECONDS
from adopt_identity import parse_uri
from adopt_obs import AdoptError, ErrorCode
from adopt_policy import validate_capability_manifest

__all__ = [
    "EXECUTABLE_KEYS",
    "HttpStep",
    "ProbeSpec",
    "PromptStep",
    "Step",
    "load_probe",
    "parse_probe",
    "render_interaction",
]

#: The keys that belong to the executable half rather than to the capability
#: manifest. `capability_manifest` is stored verbatim including these -- the
#: split is about what `interaction` renders, not about mutilating the document.
EXECUTABLE_KEYS: Final[frozenset[str]] = frozenset({"steps", "exercises", "diff_method"})

#: The only `diff_method` v1 executes. See the module docstring.
_SUPPORTED_DIFF_METHOD: Final[str] = "exact"

#: The one secret-reference form v1 accepts. Named for the *indirection* rather
#: than for what it points at -- it is a lookup scheme, never a credential.
_ENV_REF_PREFIX: Final[str] = "env:"
#: `{{secret.NAME}}` -- the only interpolation a step may carry.
_SECRET_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(
    r"\{\{secret\.([A-Za-z_][A-Za-z0-9_]*)\}\}"
)
_ENV_NAME: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_HTTP_STEP_KEYS: Final[frozenset[str]] = frozenset(
    {"kind", "method", "url", "headers", "body", "expect"}
)
_PROMPT_STEP_KEYS: Final[frozenset[str]] = frozenset({"kind", "input", "expect"})
_EXPECT_KEYS: Final[frozenset[str]] = frozenset(
    {"status", "json_fields", "latency_under_ms", "min_similarity"}
)


@dataclass(frozen=True, slots=True)
class Expectation:
    """The declared invariants of one step.

    Every field is optional and an absent one is *not checked*, which is the
    honest reading: a probe that declares nothing asserts nothing, and reporting
    success for it is correct. What is not permitted is an invariant that looks
    declared and is not -- hence the unknown-key refusal.
    """

    status: int | None = None
    json_fields: tuple[str, ...] = ()
    latency_under_ms: int | None = None
    min_similarity: float = PROBE_DIFF_SIM_THRESHOLD

    def as_canonical(self) -> dict[str, Any]:
        rendered: dict[str, Any] = {}
        if self.status is not None:
            rendered["status"] = self.status
        if self.json_fields:
            rendered["json_fields"] = list(self.json_fields)
        if self.latency_under_ms is not None:
            rendered["latency_under_ms"] = self.latency_under_ms
        rendered["min_similarity"] = self.min_similarity
        return rendered


@dataclass(frozen=True, slots=True)
class HttpStep:
    """One HTTP interaction. `host` is derived once, here, and enforced twice."""

    method: str
    url: str
    host: str
    headers: dict[str, str]
    body: Any | None
    expect: Expectation

    kind: Literal["http"] = "http"

    def as_canonical(self) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "kind": "http",
            "method": self.method,
            "url": self.url,
            "expect": self.expect.as_canonical(),
        }
        if self.headers:
            rendered["headers"] = dict(self.headers)
        if self.body is not None:
            rendered["body"] = self.body
        return rendered


@dataclass(frozen=True, slots=True)
class PromptStep:
    """One model interaction, executed through the agent seam and nowhere else."""

    input: str
    expect: Expectation

    kind: Literal["prompt"] = "prompt"

    def as_canonical(self) -> dict[str, Any]:
        return {"kind": "prompt", "input": self.input, "expect": self.expect.as_canonical()}


Step = HttpStep | PromptStep


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    """A validated probe: what to run, where it may reach, and what it costs.

    `capability_manifest` and `interaction` are the two strings the revision
    carries; everything else on this object is derived from them and exists so
    the runner never re-parses.
    """

    name: str
    safe_path: str
    diff_method: str
    steps: tuple[Step, ...]
    exercises: tuple[str, ...]
    declared_hosts: frozenset[str]
    secret_refs: frozenset[str]
    max_seconds: int
    max_requests: int
    max_model_calls: int
    max_tokens: int
    redaction_policy: str | None
    capability_manifest: str
    interaction: str


def _reject(code: ErrorCode, message: str, hint: str) -> AdoptError:
    return AdoptError(code, message=message, hint=hint)


def _invalid(message: str, hint: str) -> AdoptError:
    return _reject(ErrorCode.MANIFEST_INVALID, message, hint)


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(
            f"`{name}` must be a mapping, found {type(value).__name__}",
            "The probe file's shape is in the Build 5 sprint plan and in `adopt probe add --help`.",
        )
    return value


def _no_unknown_keys(doc: dict[str, Any], permitted: frozenset[str], where: str) -> None:
    unknown = sorted(set(doc) - permitted)
    if unknown:
        raise _invalid(
            f"{where} carries unknown key(s) {unknown}",
            f"Permitted keys are {sorted(permitted)}. Unknown keys are refused rather "
            "than ignored: a mistyped `expect` key is an invariant nobody checks, and "
            "it would report success forever.",
        )


def _expectation(raw: Any, where: str) -> Expectation:
    if raw is None:
        return Expectation()
    doc = _require_mapping(raw, f"{where}.expect")
    _no_unknown_keys(doc, _EXPECT_KEYS, f"{where}.expect")

    status = doc.get("status")
    if status is not None and not isinstance(status, int):
        raise _invalid(f"{where}.expect.status must be an integer", "For example `status: 200`.")

    fields = doc.get("json_fields", [])
    if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields):
        raise _invalid(
            f"{where}.expect.json_fields must be a list of strings",
            "Each entry is a top-level key or a dotted path, e.g. `order.id`.",
        )

    latency = doc.get("latency_under_ms")
    if latency is not None and not isinstance(latency, int):
        raise _invalid(
            f"{where}.expect.latency_under_ms must be an integer number of milliseconds",
            "The latency bucket is probe data -- each probe declares its own bound.",
        )

    similarity = doc.get("min_similarity", PROBE_DIFF_SIM_THRESHOLD)
    if not isinstance(similarity, int | float) or isinstance(similarity, bool):
        raise _invalid(
            f"{where}.expect.min_similarity must be a number",
            "It is compared against the recorded baseline; the default is the "
            "programme-wide diff-similarity threshold.",
        )

    return Expectation(
        status=status,
        json_fields=tuple(fields),
        latency_under_ms=latency,
        min_similarity=float(similarity),
    )


def _host_of(url: str, where: str) -> str:
    """`host[:port]`, by exact text, without importing a URL parser's opinions.

    Deliberately literal: the allow-list is compared as text at `add` time and
    again at connection time, and a normalization applied in one place and not
    the other is precisely how an allow-list stops meaning what it says.
    """
    if "://" not in url:
        raise _invalid(
            f"{where}.url must be absolute, found {url!r}",
            "A relative URL has no host, and a probe with no host is a probe whose "
            "allow-list cannot be checked.",
        )
    scheme, _, rest = url.partition("://")
    if scheme not in {"http", "https"}:
        raise _invalid(
            f"{where}.url must use http or https, found scheme {scheme!r}",
            "Only these two schemes are executed; anything else names a transport "
            "this runner does not have.",
        )
    authority = rest.split("/", 1)[0]
    if "@" in authority:
        raise _invalid(
            f"{where}.url must not carry userinfo",
            "Credentials belong in `secret_refs` and a header, never in a URL that is "
            "recorded with the run.",
        )
    if not authority:
        raise _invalid(f"{where}.url has an empty host", "Name the host the probe reaches.")
    return authority


def _check_secret_placeholders(value: str, declared: frozenset[str], where: str) -> None:
    for name in _SECRET_PLACEHOLDER.findall(value):
        if name not in declared:
            raise _invalid(
                f"{where} interpolates undeclared secret {name!r}",
                f"Declared refs are {sorted(declared) or '(none)'}. Add `env:{name}` to "
                "`secret_refs`: a probe may only reach for a secret it declared.",
            )


def _walk_strings(value: Any, declared: frozenset[str], where: str) -> None:
    if isinstance(value, str):
        _check_secret_placeholders(value, declared, where)
    elif isinstance(value, dict):
        for key, inner in value.items():
            _walk_strings(inner, declared, f"{where}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            _walk_strings(inner, declared, f"{where}[{index}]")


def _http_step(doc: dict[str, Any], declared_secrets: frozenset[str], where: str) -> HttpStep:
    _no_unknown_keys(doc, _HTTP_STEP_KEYS, where)
    method = doc.get("method")
    if not isinstance(method, str) or not method:
        raise _invalid(f"{where}.method is required", "For example `method: GET`.")
    url = doc.get("url")
    if not isinstance(url, str) or not url:
        raise _invalid(f"{where}.url is required", "Name the absolute URL the probe calls.")

    headers_raw = doc.get("headers", {})
    headers = _require_mapping(headers_raw, f"{where}.headers") if headers_raw else {}
    if any(not isinstance(v, str) for v in headers.values()):
        raise _invalid(
            f"{where}.headers values must be strings",
            "A header value is text; structure belongs in the body.",
        )

    body = doc.get("body")
    _walk_strings(headers, declared_secrets, f"{where}.headers")
    _walk_strings(body, declared_secrets, f"{where}.body")

    return HttpStep(
        method=method.upper(),
        url=url,
        host=_host_of(url, where),
        headers={str(k): str(v) for k, v in headers.items()},
        body=body,
        expect=_expectation(doc.get("expect"), where),
    )


def _prompt_step(doc: dict[str, Any], where: str) -> PromptStep:
    _no_unknown_keys(doc, _PROMPT_STEP_KEYS, where)
    text = doc.get("input")
    if not isinstance(text, str) or not text.strip():
        raise _invalid(
            f"{where}.input is required and must be non-empty",
            "A prompt step sends this text through the agent seam.",
        )
    return PromptStep(input=text, expect=_expectation(doc.get("expect"), where))


def _steps(raw: Any, declared_secrets: frozenset[str]) -> tuple[Step, ...]:
    if not isinstance(raw, list) or not raw:
        raise _invalid(
            "`steps` must be a non-empty list",
            "A probe with no steps observes nothing and would record a success that means nothing.",
        )
    built: list[Step] = []
    for index, entry in enumerate(raw):
        where = f"steps[{index}]"
        doc = _require_mapping(entry, where)
        kind = doc.get("kind")
        if kind == "http":
            built.append(_http_step(doc, declared_secrets, where))
        elif kind == "prompt":
            built.append(_prompt_step(doc, where))
        else:
            raise _invalid(
                f"{where}.kind is {kind!r}, not 'http' or 'prompt'",
                "v1 executes exactly two step kinds. Generated-code probes are "
                "deferred until two real baselines prove inexpressible as data.",
            )
    return tuple(built)


def _secret_refs(raw: Any) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise _invalid("`secret_refs` must be a list", "Each entry is `env:NAME`.")
    names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry.startswith(_ENV_REF_PREFIX):
            raise _invalid(
                f"secret ref {entry!r} is not `env:NAME`",
                "v1 resolves secrets from the environment by name (v6.1 §6 B5). A "
                "`vault://` reference names a custody path this runner does not have.",
            )
        name = entry[len(_ENV_REF_PREFIX) :]
        if not _ENV_NAME.match(name):
            raise _invalid(
                f"secret ref {entry!r} does not name an environment variable",
                "Use `env:PROBE_API_TOKEN` -- letters, digits and underscores.",
            )
        names.add(name)
    return frozenset(names)


def _exercises(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(entry, str) for entry in raw):
        raise _invalid(
            "`exercises` must be a list of canonical identity URIs",
            "Each entry names an identity this probe exercises; a drift against one "
            "is what becomes a conflict row.",
        )
    for uri in raw:
        # Parsed rather than pattern-matched: `adopt_identity` is the authority on
        # what a canonical URI is, and a URI that will not resolve at conflict
        # time is better refused now than recorded and discovered later.
        parse_uri(uri)
    return tuple(raw)


def _positive_int(block: Mapping[str, Any], key: str, where: str) -> int:
    """One declared limit, as an integer the runner can actually enforce.

    Build 0's validator already refuses an *absent* limit; this refuses a present
    one that is not a number, which is the case that would otherwise reach a
    comparison at run time and fail somewhere far from the document that caused
    it. `bool` is excluded explicitly because `True` is an `int` in Python and
    `max_requests: yes` is a YAML spelling of it.
    """
    value = block.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _invalid(
            f"`{where}.{key}` must be a non-negative integer, found {value!r}",
            "Build 0's manifest validator requires the limit to be present; this "
            "requires it to be a number the runner can enforce.",
        )
    return value


def _limits(doc: dict[str, Any]) -> tuple[int, int, int, int]:
    runtime = _require_mapping(doc.get("runtime"), "runtime")
    cost = _require_mapping(doc.get("cost"), "cost")
    max_seconds = _positive_int(runtime, "max_seconds", "runtime")
    max_requests = _positive_int(runtime, "max_requests", "runtime")
    max_model_calls = _positive_int(cost, "max_model_calls", "cost")
    max_tokens = _positive_int(cost, "max_tokens", "cost")
    if max_seconds > PROBE_TIMEOUT_SECONDS:
        raise _invalid(
            f"`runtime.max_seconds` is {max_seconds}, above the {PROBE_TIMEOUT_SECONDS}s ceiling",
            "The ceiling exists so a probe cannot declare its way out of the wall "
            "clock. Lower the probe's budget.",
        )
    return max_seconds, max_requests, max_model_calls, max_tokens


def render_interaction(
    steps: tuple[Step, ...], exercises: tuple[str, ...], diff_method: str
) -> str:
    """The canonical JSON of the executable half -- the one rendering, used twice.

    `add` stores this and `add` again compares against it, so a second renderer
    anywhere would make idempotence depend on which one ran.
    """
    return json.dumps(
        {
            "diff_method": diff_method,
            "exercises": list(exercises),
            "steps": [step.as_canonical() for step in steps],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def parse_probe(text: str) -> ProbeSpec:
    """Validate a probe document and render what the store will hold.

    Args:
        text: The probe file's text, exactly as authored.

    Returns:
        A `ProbeSpec` carrying the executable steps and the two stored strings.

    Raises:
        AdoptError: ``MANIFEST_INVALID`` for any structural violation of the
            probe half; ``MANIFEST_UNDECLARED_HOST`` when a step reaches a host
            outside `network.allow`; and every code Build 0's
            `validate_capability_manifest` raises for the manifest half.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _invalid(
            f"the probe file is not valid YAML: {exc.__class__.__name__}",
            "A probe is a declarative document; fix the syntax and re-run.",
        ) from exc

    doc = _require_mapping(loaded, "the probe file")

    # Build 0's validator first: it owns the six manifest rejections, and a probe
    # whose manifest is unsafe should be refused before anything reads its steps.
    verdict = validate_capability_manifest(doc)

    diff_method = doc.get("diff_method", _SUPPORTED_DIFF_METHOD)
    if diff_method != _SUPPORTED_DIFF_METHOD:
        raise _invalid(
            f"`diff_method` is {diff_method!r}; v1 executes only {_SUPPORTED_DIFF_METHOD!r}",
            "`embedding_sim` waits on the vector seam's measured-miss trigger, "
            "`llm_judge` on judge machinery, `contract_delta` on contract sensing. "
            "All three are representable in the schema and none is built.",
        )

    name = doc.get("probe_id") or verdict.probe_id
    if not isinstance(name, str) or not name.strip():
        raise _invalid(
            "`probe_id` is required and names the probe",
            "It is the name `adopt probe run <name>` takes and the key `add` is idempotent on.",
        )

    secret_refs = _secret_refs(doc.get("secret_refs"))
    steps = _steps(doc.get("steps"), secret_refs)
    exercises = _exercises(doc.get("exercises"))
    max_seconds, max_requests, max_model_calls, max_tokens = _limits(doc)

    declared_hosts = frozenset(verdict.declared_hosts)
    undeclared = sorted({s.host for s in steps if isinstance(s, HttpStep)} - declared_hosts)
    if undeclared:
        # The same code the runner raises, and deliberately so: `add` refusing
        # here is a courtesy that saves a round trip, while the runner's check at
        # connection time is the invariant. One code, one meaning, two places.
        raise _reject(
            ErrorCode.PROBE_HOST_UNDECLARED,
            f"step host(s) {undeclared} are not in `network.allow`",
            f"Declared hosts are {sorted(declared_hosts)}. Add the host to "
            "`network.allow` if the probe is meant to reach it -- the allow-list is "
            "the statement the runner enforces.",
        )

    output = doc.get("output")
    redaction = None
    if isinstance(output, dict):
        policy = output.get("redaction_policy")
        redaction = str(policy) if policy is not None else None

    return ProbeSpec(
        name=name.strip(),
        safe_path=str(doc["safe_path"]),
        diff_method=diff_method,
        steps=steps,
        exercises=exercises,
        declared_hosts=declared_hosts,
        secret_refs=secret_refs,
        max_seconds=max_seconds,
        max_requests=max_requests,
        max_model_calls=max_model_calls,
        max_tokens=max_tokens,
        redaction_policy=redaction,
        # Verbatim: contracts §7 stores the manifest whole, and the bytes a human
        # approved must be the bytes we keep.
        capability_manifest=text,
        interaction=render_interaction(steps, exercises, diff_method),
    )


def load_probe(text: str) -> ProbeSpec:
    """`parse_probe`, named for the call site that reads a file first."""
    return parse_probe(text)
