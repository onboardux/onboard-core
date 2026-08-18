"""The four `map-*-001` prompts -- `04` §4, §5, §7, and B1-CR-32.

`05` S1.7 asks for *"a test asserting prompt text is loaded verbatim from the
skill directory, never constructed in code"*. That is two claims, and the second
is the one a source scan can make: **no prompt text exists in `packages/`**, so
"loaded from disk" is a property of there being nowhere else to load it from.

The third claim here is B1-CR-80's: the `04` §4 USER templates render. A template
written in a placeholder syntax Build 0's renderer treats as an escaped literal
renders *successfully*, sending the model the text `{coverage_report_json}`
instead of the evidence -- silently, on every call, with no error anywhere.
"""

import json
import re
from pathlib import Path

import pytest
from adopt_map.quarantine import (
    GLUE_PROMPT_REF,
    LABEL_PROMPT_REF,
    PROSE_PROMPT_REF,
    TRIAGE_PROMPT_REF,
)
from adopt_map.schemas.agent import AGENT_OUTPUT_MODELS

from adopt_agent.skills import load_skill

pytestmark = pytest.mark.unit

PROMPTS = Path("prompts")
REFS = (TRIAGE_PROMPT_REF, GLUE_PROMPT_REF, LABEL_PROMPT_REF, PROSE_PROMPT_REF)


#: **The byte comparison against `04` §4's document is deliberately absent.**
#: Every CI job checks out `adopt-core` alone and the pack lives in a separate
#: repository, so a test reading `../builds/build_1/04-ai-spec.md` would pass on
#: the authoring machine and fail -- or worse, skip -- everywhere else. Once
#: merged, the prompt directory **is** the artefact (`00` §9 rule 3 makes it
#: immutable per id); these four files were generated from `04` §4 by extraction
#: rather than retyped, and what this suite asserts is that they load, render,
#: match their schemas, and exist nowhere else in the tree.


@pytest.mark.parametrize("ref", REFS)
def test_each_prompt_loads_through_build_0s_loader(ref: str) -> None:
    """B1-CR-32: `prompts/<id>/v<n>/`, beside Build 0's `detect-001/v1`.

    Loaded through `adopt_agent.skills.load_skill` rather than read as files,
    because that is what a run does: a directory this loader rejects is a prompt
    that fails at call time, and `skill_sha256` covers the whole directory.
    """
    skill = load_skill(ref, root=PROMPTS)
    assert skill.user_template is not None, "`04` §4 gives every prompt a USER template"
    assert skill.output_schema is not None, "`04` §5 gives every prompt a closed schema"
    assert skill.sha256


@pytest.mark.parametrize("ref", REFS)
def test_the_system_text_names_its_hard_constraints(ref: str) -> None:
    """The prompt text a run loads is the `04` §4 text, not a paraphrase of it.

    Checked by content rather than by byte comparison, for the reason recorded
    above the constants: the document is not in this repository. Each prompt is
    asserted to still carry the sentence that makes it that prompt -- the closed
    kind enum for triage and glue, the empty-list instruction for label, the
    word ceiling for prose -- so a rewritten prompt fails here even though its id
    and its digest would happily change together.
    """
    body = load_skill(ref, root=PROMPTS).body
    required = {
        "map-triage-001": "no execution of the repository's code",
        "map-glue-001": "MUST NOT import, execute, evaluate, or dynamically load",
        "map-label-001": "An empty list is the correct answer",
        "map-prose-001": "at most 60 words",
    }[ref.split("/")[0]]
    assert required in body


@pytest.mark.parametrize("ref", REFS)
def test_the_user_template_renders_every_placeholder(ref: str) -> None:
    """B1-CR-80: a `{{name}}` template renders the literal `{name}` and raises nothing.

    Build 0's renderer is `str.format`, where `{{` is an **escaped brace**. `04`
    §4 was written in mustache syntax, so every template would have sent the model
    the placeholder's own name in place of the evidence -- succeeding, on every
    call, with no error and no missing-input refusal to notice. The repair is in
    `04` §4 and this is the instrument that keeps it repaired.
    """
    skill = load_skill(ref, root=PROMPTS)
    template = skill.user_template or ""
    names = set(re.findall(r"\{(\w+)\}", template))
    assert names, "a USER template with no placeholder sends no evidence at all"

    rendered = template.format(**dict.fromkeys(names, "EVIDENCE"))
    assert "{" not in rendered and "}" not in rendered, (
        f"{ref} rendered with braces surviving: {rendered!r}. That is the "
        "doubled-brace form, which renders a placeholder's name to the model."
    )
    assert rendered.count("EVIDENCE") >= len(names)


@pytest.mark.parametrize("ref", REFS)
def test_the_shipped_schema_is_the_model_it_validates_against(ref: str) -> None:
    """The prompt's `output_schema.json` and `04` §5's model cannot drift.

    Generated from the model at build time; asserted here rather than hoped,
    because a schema shipped to a provider that disagrees with the validator this
    side is a reply that satisfies the model's request and fails ours.
    """
    prompt_id = ref.split("/")[0]
    shipped = json.loads((PROMPTS / prompt_id / "v1" / "output_schema.json").read_text("utf-8"))
    expected = _strip(AGENT_OUTPUT_MODELS[prompt_id].model_json_schema())
    assert shipped == expected


def _strip(node: object) -> object:
    """Drop `description`/`title`, which pydantic derives from our docstrings.

    A prompt's schema is product data; our implementation commentary is not, and
    one of those docstrings used to carry an `04` §8 threshold into a shipped file
    -- which `constants_sync` caught as prose stating a tunable.
    """
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in ("description", "title")}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def test_no_prompt_text_is_constructed_in_code() -> None:
    """`05` S1.7: *"never constructed in code"*, as a scan over `packages/`.

    Looks for the opening sentence of each system prompt anywhere in the shipped
    source. A prompt assembled in Python would make `skill_sha256` a digest of a
    file nobody sent, and `00` §9 rule 3's immutability would then be a property
    of the wrong artefact.
    """
    openings = [load_skill(ref, root=PROMPTS).body.splitlines()[0] for ref in REFS]
    for path in Path("packages").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for opening in openings:
            assert opening not in text, f"prompt text found in {path}"


def test_every_prompt_version_directory_carries_evals() -> None:
    """`04` §7: *"cannot register without them"*.

    Build 1 owns this check because `adopt_agent.skills` is a **protected** Build 0
    module that does not make it (B1-CR-81), and Build 0's own `detect-001/v1`
    does not satisfy it -- reported rather than amended, since `03` §4 forbids this
    build to edit either.
    """
    for ref in REFS:
        evals = PROMPTS / ref.split("/")[0] / "v1" / "evals"
        assert evals.is_dir(), f"{ref} has no evals/ directory"
        assert (evals / "manifest.json").is_file()


def test_the_active_prompt_refs_name_a_version() -> None:
    """`04` §7: the active id per position is a module constant naming `<id>/v<n>`.

    Following Build 0's `DISAMBIGUATION_PROMPT_REF = "detect-001/v1"`, because
    *"rollback is repointing that constant"* only works if there is exactly one to
    repoint.
    """
    for ref in REFS:
        assert re.fullmatch(r"map-[a-z]+-001/v\d+", ref), ref
    assert len(set(REFS)) == len(AGENT_OUTPUT_MODELS)
