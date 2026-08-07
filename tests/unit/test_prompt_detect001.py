"""`detect-001@1` is the bytes AI spec §5.1 publishes, and nothing else.

*Fails when* the prompt is reflowed, re-worded, re-wrapped or silently corrected
while keeping its id. *Matters because* `04` §5.2 makes `detect-001@1` mean one
byte sequence forever and `skill_sha256` binds every run to it: an audit inside a
client environment reconstructs *what was asked* from the digest, so an edited `v1`
makes every trace that names it wrong -- retroactively, and with no way to tell.
*No other instrument catches it because* nothing else compares the file to the
document. The loader hashes whatever it finds and the golden set measures whether
the answers are good; both are perfectly happy with a prompt that drifted.

**The expected text is a literal here, on purpose.** Reading it out of
`04-ai-spec-build0.md` would make the test pass whenever the document and the file
drifted *together*, which is exactly the change control this file exists to catch:
`00` §5 rule 5 says a prompt is never edited, so the document's §5.1 and these
bytes are two independent copies that must agree, and a test that read one of them
would be comparing a thing to itself.
"""

import json
from pathlib import Path
from typing import get_args

import pytest

from adopt_agent.skills import load_skill
from adopt_const import DETECT_CONFIDENCE_MIN
from adopt_model._enums import Archetype

pytestmark = pytest.mark.unit

PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"

#: AI spec §5.1's System block, verbatim. Note the em dash in the `data` line and
#: the two-space continuation indents: both are part of the byte sequence.
_SYSTEM = """You classify software systems into exactly one of five archetypes for a
documentation tool. You are being called only because deterministic file-tree
heuristics were inconclusive.

Archetypes:
- web: an application or service whose source is under version control and whose
  behavior changes when someone edits that source.
- platform: a customization layer on a packaged ERP/CRM product, where
  configuration lives in a vendor metadata store rather than in files.
- lowcode: a solution built in a low-code platform and exported as a package.
- data: a data platform — transformation models, semantic models, a catalog.
- ai: a system whose behavior depends on model calls, prompts, or retrieval
  configuration, and can therefore change without any code edit.

You will receive: per-archetype scores from the heuristics, the rules that fired
with the paths that triggered them, and a bounded directory listing. You will NOT
receive file contents, and you must not ask for them.

Rules:
1. Choose from the five archetypes only. Never invent a category.
2. If the evidence does not distinguish between archetypes, say so by returning
   low confidence. Low confidence is a correct answer; guessing is not.
3. A system may contain several archetypes. Return the primary one and list the
   others in `secondary`.
4. Base `reasoning` only on the evidence provided. Do not speculate about code
   you cannot see.
5. Reply with a single JSON object matching the schema. No prose, no markdown
   fences, no preamble.
"""

#: AI spec §5.1's User block, verbatim, including its four placeholders.
_USER = """Heuristic scores:
{scores_json}

Rules that fired:
{rules_fired_json}

Directory listing (truncated to {listing_limit} entries):
{listing}
"""


def test_the_system_text_is_byte_identical_to_the_ai_spec() -> None:
    skill = load_skill("detect-001/v1", root=PROMPTS)

    assert skill.body == _SYSTEM
    assert skill.name == "detect-001"


def test_the_user_template_is_byte_identical_and_keeps_its_four_placeholders() -> None:
    """The placeholders are the contract between the prompt and its caller.

    Renaming one is a prompt change requiring a new version id, and it would fail
    at render time with a `MANIFEST_INVALID` naming the missing input -- but only
    once someone ran it. This catches it at unit speed.
    """
    skill = load_skill("detect-001/v1", root=PROMPTS)

    assert skill.user_template == _USER
    for placeholder in ("{scores_json}", "{rules_fired_json}", "{listing_limit}", "{listing}"):
        assert placeholder in (skill.user_template or "")


def test_the_output_schema_enum_is_the_canonical_archetype_vocabulary() -> None:
    """`04` §5.1: "the enum values are exactly the `archetype` vocabulary in
    contracts §2.1".

    Asserted against the **generated** enum rather than a literal list, so adding
    an archetype to the manifest without adding it here fails rather than silently
    producing a prompt that cannot name the new one -- and a proposal naming
    anything outside the enum fails schema validation at the seam and never
    reaches a human.
    """
    skill = load_skill("detect-001/v1", root=PROMPTS)
    schema = skill.output_schema

    assert schema is not None
    # `Archetype` is a generated `Literal`, not an `Enum` -- `02` §2.3 maps a
    # manifest enum to a closed literal type. `get_args` is how `adopt_detect.rules`
    # reads the same vocabulary, so this test and the rule loader agree by
    # construction rather than by two lists that happen to match.
    canonical = set(get_args(Archetype))
    assert set(schema["properties"]["primary"]["enum"]) == canonical
    assert set(schema["properties"]["secondary"]["items"]["enum"]) == canonical
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"primary", "confidence", "reasoning", "secondary"}


def test_the_schema_file_is_the_json_the_ai_spec_publishes() -> None:
    """Parsed rather than compared byte-for-byte, deliberately.

    §5.1 renders the schema with aligned columns for a reader, and JSON has no
    canonical whitespace -- so a byte comparison here would assert a formatting
    choice rather than the schema. The `enum`, `required` and
    `additionalProperties` assertions above are the parts that change meaning.
    """
    raw = (PROMPTS / "detect-001" / "v1" / "output_schema.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)

    assert parsed["type"] == "object"
    assert parsed["properties"]["confidence"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert parsed["properties"]["reasoning"] == {"type": "string", "maxLength": 600}
    assert parsed["properties"]["secondary"]["maxItems"] == 4


def test_the_golden_set_holds_to_its_own_rules() -> None:
    """Every golden case is ambiguous, labelled, and evenly spread.

    *Fails when* a case is added whose heuristics already resolve it, or when the
    per-archetype counts drift. *Matters because* `detect-001` is **only ever
    called on an ambiguous tree** -- a set containing resolved systems measures a
    prompt that never runs on them, and a set weighted toward one archetype
    measures a model's prior rather than its reasoning. *No other instrument
    catches it because* `report.py` prints the counts and asserts nothing, and it
    only runs when someone has an adapter.
    """
    cases = json.loads(
        (
            Path(__file__).resolve().parent.parent / "golden_prompts" / "detect_001" / "cases.json"
        ).read_text(encoding="utf-8")
    )["cases"]

    assert len(cases) >= 15  # `04` §7.2: at least fifteen
    per_archetype: dict[str, int] = {}
    for case in cases:
        assert case["archetype"] in get_args(Archetype)
        assert case["notes"].strip(), f"{case['id']} carries no reason for its label"
        # Ambiguous by construction: `detect-001` is reached only below the
        # threshold, so a case at or above it could never occur in production.
        assert max(case["scores"].values()) < DETECT_CONFIDENCE_MIN, (
            f"{case['id']} is not ambiguous: the heuristics already resolve it"
        )
        per_archetype[case["archetype"]] = per_archetype.get(case["archetype"], 0) + 1

    assert set(per_archetype) == set(get_args(Archetype)), "an archetype has no golden case"
    assert min(per_archetype.values()) >= 2  # `04` §7.2: at least two per archetype


def test_the_digest_covers_the_whole_prompt_directory(tmp_path: Path) -> None:
    """Adding a file to a prompt version changes `skill_sha256`.

    *Matters because* `04` §5.2 rule 3 binds a run to exact prompt bytes, and the
    user template and the output schema are prompt bytes: a digest over `SKILL.md`
    alone would report two prompts with different schemas as the same prompt.
    """
    original = load_skill("detect-001/v1", root=PROMPTS).sha256

    copied = tmp_path / "detect-001" / "v1"
    copied.mkdir(parents=True)
    for name in ("SKILL.md", "user.md", "output_schema.json"):
        (copied / name).write_bytes((PROMPTS / "detect-001" / "v1" / name).read_bytes())
    assert load_skill("detect-001/v1", root=tmp_path).sha256 == original

    (copied / "references").mkdir()
    (copied / "references" / "note.md").write_text("extra material\n", encoding="utf-8")

    assert load_skill("detect-001/v1", root=tmp_path).sha256 != original
