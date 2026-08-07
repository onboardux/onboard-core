"""No planted secret reaches the trace or the runtime annex, for any secret.

*Fails when* a payload -- prompt text, model output, a tool argument, or an input
value -- is written into a trace step or into `agent_run` instead of being
digested. *Matters because* PRD N11 and AI spec §8.3 are what let a trace be kept
*inside a client environment*: an operator proves what was asked without the
payload being retrievable from our artifacts, and a single leaked field turns the
audit record into the place client content accumulates in plain text. *No other
instrument catches it because* `tests/property/test_log_egress.py` covers log
lines and the deny-list covers field *names* -- neither looks at the persisted
trace, and a leak here would be structurally invisible to both.

**The whole record is serialized and searched, rather than checked field by
field.** A field-by-field assertion passes an implementation that stashed the text
somewhere the test did not think to look; this one cannot be evaded by adding a
field, which is the property that matters as the shape grows.

**The secret is planted in all four places at once**, because they leak through
different code: `inputs` through the request digest, the skill body through the
system prompt, the response text through the trace step, and the tool arguments
through the tool-call step. A property planting one would pass with the other
three broken.
"""

import json
import tempfile
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from adopt_agent import AgentRequest, Budget, Runner, ToolSpec
from adopt_store.annex import open_annex

pytestmark = pytest.mark.property

_SCOPE: Final[str] = "northwind/acme-erp"

#: The `planted-` prefix is load-bearing: it guarantees the secret contains
#: characters no hexadecimal digest can, so a match is a real leak rather than
#: two hex characters coinciding inside a SHA-256.
#:
#: **The alphabet is ASCII deliberately.** A secret carrying a non-ASCII
#: character is escaped by `json.dumps`'s default `ensure_ascii=True`, so it
#: would not appear literally in a rendering that used the default -- which makes
#: a substring search report "no leak" for a reason that has nothing to do with
#: the seam. Keeping the secret ASCII means a match means a leak and a miss means
#: absence, under either rendering rule.
_ASCII = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SECRETS = st.builds(
    lambda tail: f"planted-{tail}",
    st.text(alphabet=_ASCII, min_size=4, max_size=24),
)


@given(secret=_SECRETS)
@settings(max_examples=50, deadline=None)
def test_no_planted_secret_reaches_the_trace_or_the_annex(secret: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills = root / "skills" / "probe" / "v1"
        skills.mkdir(parents=True)
        body = f"---\nname: probe\ndescription: A skill.\n---\n\nThe key is {secret}.\n"
        (skills / "SKILL.md").write_text(body, encoding="utf-8")

        fixture = root / "recorded.json"
        fixture.write_text(
            json.dumps(
                {
                    "turns": [
                        {
                            "text": "",
                            "tool_calls": [
                                {"id": "c1", "name": "lookup", "arguments": {"q": secret}}
                            ],
                            "input_tokens": 3,
                            "output_tokens": 3,
                            "reported_usd": 0.0,
                        },
                        {
                            "text": f"the answer contains {secret}",
                            "tool_calls": [],
                            "input_tokens": 3,
                            "output_tokens": 3,
                            "reported_usd": 0.0,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        tool = ToolSpec(
            name="lookup",
            description="Look something up.",
            input_schema={"type": "object"},
            handler=lambda args: secret,
        )
        request = AgentRequest(
            skill_ref="probe/v1",
            inputs={"token": secret},
            tools=[tool],
            budget=Budget(max_usd=1000.0, max_wall_seconds=3600, max_tool_calls=2),
            idempotency_key="k-1",
        )

        with open_annex(root / ".adopt" / "runtime.db") as annex:
            runner = Runner(
                annex=annex,
                scope_ref=_SCOPE,
                skills_root=root / "skills",
                offline=True,
                adapter_id="fake_recorded",
                endpoint=str(fixture),
            )
            result = runner.run(request)
            recorded = annex.find_run(scope_ref=_SCOPE, idempotency_key="k-1")

        # Converse guards. Without these, an empty trace and an unwritten annex
        # row would satisfy every assertion below while proving nothing.
        assert secret in body
        assert secret in json.dumps(request.inputs)
        assert len(result.trace.steps) >= 4  # request, tool_call, tool_result, response
        assert recorded is not None

        serialized: dict[str, Any] = {
            "trace": result.trace.model_dump_json(),
            "annex_trace": recorded.trace_json,
            "annex_row": recorded.model_dump_json(exclude={"trace_json"}),
        }
        for where, blob in serialized.items():
            assert secret not in blob, f"the planted secret reached {where}"

        # `output_ref` is a blob reference; output text is never inlined
        # (contracts §12). A run that produced no artifact has none at all.
        assert recorded.output_ref is None
