"""The probe file: what it accepts, and what it refuses at the door.

*Fails when* probe-file validation stops refusing a document that would produce
an unsafe or silently-wrong run. *Matters because* a probe is a security
document executed against a client's system, and every refusal here is cheaper
than the same discovery at connection time in their environment. *No other
instrument catches it because* the runner's tests exercise steps that already
parsed -- an invariant that was never declared, because its key was misspelled,
reaches the runner as a probe with nothing to check and passes forever.

**Build 0's six manifest rejections are deliberately not re-tested here.** They
belong to `validate_capability_manifest` and have their own suite; v6.1 §4 R6(c)
says inherited substrate gates are never re-proven. What *is* tested is the half
this build added, plus the one place the two meet: a step host that the
manifest's own `network.allow` does not carry.
"""

import textwrap

import pytest
from adopt_probe import HttpStep, PromptStep, parse_probe
from adopt_probe.manifest import render_interaction

from adopt_const import PROBE_DIFF_SIM_THRESHOLD, PROBE_TIMEOUT_SECONDS
from adopt_obs import AdoptError, ErrorCode

pytestmark = pytest.mark.unit


def _probe(**overrides: str) -> str:
    """The worked example, with named sections swappable per test."""
    parts = {
        "probe_id": "probe_id: checkout-happy-path",
        "safe_path": "safe_path: sandbox",
        "network": 'network: { deny_by_default: true, allow: ["127.0.0.1:8123"] }',
        "http_methods": "http_methods: { allow: [GET, POST] }",
        "side_effect_policy": "side_effect_policy: prohibited",
        "secret_refs": "secret_refs: [env:PROBE_API_TOKEN]",
        "runtime": "runtime: { max_seconds: 30, max_memory_mb: 256, max_requests: 10 }",
        "cost": "cost: { max_model_calls: 2, max_tokens: 8000 }",
        "output": "output: { retain_raw: false, redaction_policy: pii-default }",
        "cleanup": "cleanup: { required: true }",
        "steps": textwrap.dedent("""\
            steps:
              - kind: http
                method: POST
                url: "http://127.0.0.1:8123/v1/checkout"
                headers: { Authorization: "Bearer {{secret.PROBE_API_TOKEN}}" }
                body: { sku: "demo-1", qty: 1 }
                expect: { status: 200, json_fields: ["order_id"], latency_under_ms: 2000 }
            """).rstrip(),
    }
    parts.update(overrides)
    return "\n".join(parts.values()) + "\n"


def test_accepts_the_worked_example() -> None:
    spec = parse_probe(_probe())

    assert spec.name == "checkout-happy-path"
    assert spec.safe_path == "sandbox"
    assert spec.declared_hosts == frozenset({"127.0.0.1:8123"})
    assert spec.secret_refs == frozenset({"PROBE_API_TOKEN"})
    assert spec.max_seconds == 30
    assert spec.max_requests == 10
    assert spec.max_model_calls == 2
    assert spec.max_tokens == 8000
    assert spec.redaction_policy == "pii-default"

    step = spec.steps[0]
    assert isinstance(step, HttpStep)
    assert step.method == "POST"
    assert step.host == "127.0.0.1:8123"
    assert step.expect.status == 200
    assert step.expect.json_fields == ("order_id",)


def test_capability_manifest_is_stored_verbatim() -> None:
    """Contracts §7 stores the manifest whole. The bytes a human approved are kept.

    A re-serialization would mean the document reviewed and the document recorded
    are different documents, which is the gap an approval is supposed to close.
    """
    text = _probe()
    assert parse_probe(text).capability_manifest == text


def test_interaction_is_canonical_and_stable() -> None:
    """Two parses of the same file render byte-identical `interaction`.

    `add`'s idempotence compares this string, so an unstable rendering would make
    a re-add either a spurious new revision or a coin toss.
    """
    first, second = parse_probe(_probe()), parse_probe(_probe())
    assert first.interaction == second.interaction
    assert first.interaction == render_interaction(first.steps, first.exercises, first.diff_method)
    # Sorted keys, no spaces -- the export writer's rendering discipline.
    assert '"diff_method":"exact"' in first.interaction
    assert ", " not in first.interaction


def test_default_min_similarity_is_the_programme_threshold() -> None:
    spec = parse_probe(
        _probe(
            steps=textwrap.dedent("""\
        steps:
          - kind: prompt
            input: "Summarize the checkout policy."
        """).rstrip()
        )
    )
    step = spec.steps[0]
    assert isinstance(step, PromptStep)
    assert step.expect.min_similarity == PROBE_DIFF_SIM_THRESHOLD


def test_refuses_a_step_host_outside_the_allow_list() -> None:
    """The courtesy check at `add`. The runner enforces the same rule at connect."""
    with pytest.raises(AdoptError) as caught:
        parse_probe(
            _probe(
                steps=textwrap.dedent("""\
                steps:
                  - kind: http
                    method: GET
                    url: "http://evil.example/steal"
                """).rstrip()
            )
        )
    assert caught.value.code is ErrorCode.PROBE_HOST_UNDECLARED
    assert "evil.example" in str(caught.value.message)


def test_refuses_an_unsupported_diff_method() -> None:
    """v1 executes `exact`. The other three are schema-representable and unbuilt."""
    with pytest.raises(AdoptError) as caught:
        parse_probe(_probe(probe_id="probe_id: p\ndiff_method: embedding_sim"))
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "embedding_sim" in str(caught.value.message)


def test_refuses_a_non_env_secret_ref() -> None:
    """v6.1 §6 B5 says env names. A `vault://` ref names custody we do not have."""
    with pytest.raises(AdoptError) as caught:
        parse_probe(_probe(secret_refs="secret_refs: [vault://engagement/probe-token]"))
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "vault" in str(caught.value.message)


def test_refuses_a_declared_timeout_above_the_ceiling() -> None:
    """A probe may not declare its way out of the programme wall clock."""
    over = PROBE_TIMEOUT_SECONDS + 1
    with pytest.raises(AdoptError) as caught:
        parse_probe(
            _probe(runtime=f"runtime: {{ max_seconds: {over}, max_memory_mb: 8, max_requests: 1 }}")
        )
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert str(PROBE_TIMEOUT_SECONDS) in str(caught.value.message)


def test_refuses_an_unknown_step_kind() -> None:
    with pytest.raises(AdoptError) as caught:
        parse_probe(_probe(steps='steps:\n  - kind: shell\n    command: "rm -rf /"'))
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "shell" in str(caught.value.message)


def test_refuses_an_unknown_expect_key() -> None:
    """The typo that would otherwise be an invariant nobody checks.

    This is the refusal the module docstring calls the egress posture: a
    misspelled `expect` key is not a harmless extra, it is a declared assertion
    that silently is not one, and the probe reports success forever.
    """
    with pytest.raises(AdoptError) as caught:
        parse_probe(
            _probe(
                steps=textwrap.dedent("""\
                steps:
                  - kind: http
                    method: GET
                    url: "http://127.0.0.1:8123/health"
                    expect: { statuss: 200 }
                """).rstrip()
            )
        )
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "statuss" in str(caught.value.message)


def test_refuses_an_undeclared_secret_placeholder() -> None:
    """A probe may only reach for a secret it declared."""
    with pytest.raises(AdoptError) as caught:
        parse_probe(
            _probe(
                steps=textwrap.dedent("""\
                steps:
                  - kind: http
                    method: GET
                    url: "http://127.0.0.1:8123/health"
                    headers: { Authorization: "Bearer {{secret.OTHER_TOKEN}}" }
                """).rstrip()
            )
        )
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "OTHER_TOKEN" in str(caught.value.message)


def test_refuses_a_url_carrying_userinfo() -> None:
    """Credentials in a URL end up in a recorded observation."""
    with pytest.raises(AdoptError) as caught:
        parse_probe(
            _probe(
                network='network: { deny_by_default: true, allow: ["127.0.0.1:8123"] }',
                steps="steps:\n  - kind: http\n    method: GET\n"
                '    url: "http://user:pw@127.0.0.1:8123/health"',
            )
        )
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "userinfo" in str(caught.value.message)


def test_refuses_a_non_integer_declared_limit() -> None:
    """Build 0 requires the limit to be present; this requires it to be enforceable."""
    with pytest.raises(AdoptError) as caught:
        parse_probe(_probe(cost="cost: { max_model_calls: lots, max_tokens: 8000 }"))
    assert caught.value.code is ErrorCode.MANIFEST_INVALID
    assert "max_model_calls" in str(caught.value.message)


def test_exercises_must_be_canonical_uris() -> None:
    """A URI that will not resolve at conflict time is refused when it is written."""
    with pytest.raises(AdoptError):
        parse_probe(_probe(probe_id='probe_id: p\nexercises: ["not-a-uri"]'))
