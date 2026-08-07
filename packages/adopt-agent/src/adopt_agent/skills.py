"""The SKILL.md loader: AI spec §6, and the other half of `skill_ref`.

**Why this exists at all.** `AgentRequest.skill_ref` and `agent_run.skill_sha256`
are both unimplementable without it, and no sprint task created it -- a coverage
gap closed at S7 rather than a shape invented here *(CR-44)*. AI spec §5.2 says
`skill_sha256` binds every run to exact prompt bytes so that an audit **inside a
client environment** can reconstruct what was asked without the provider. A
digest that covered only `SKILL.md` would leave the `references/` a skill loads
outside the binding, and two runs with different reference material would carry
the same digest.

**So the digest covers the whole directory**, over sorted relative paths and
content, and the path is hashed alongside the content: renaming a file changes
what the skill means and must therefore change the digest, which hashing content
alone would not catch.

**`scripts/` are materialized and never executed.** AI spec §6's loading
contract says so, and PRD §10 makes "executing agent-generated code in-process"
a permanently cut non-goal. This module has no `subprocess`, no `exec`, no
`eval` and no import of a loaded script -- the guarantee is the absence of an
API rather than a check someone remembers to run.

**Invalid frontmatter raises before any provider call.** That ordering is the
point rather than an implementation detail: a malformed skill must cost nothing,
and a loader that validated after dispatch would bill a client for a request
that could never have been answered.

**A prompt version directory is one of these** *(CR-47)*. `04` §5.1 gives
`detect-001@1` a **user template** as well as a system text, and `02` §10.1's
`AgentRequest` has no field for one -- it carries `skill_ref` and `inputs`, and
the seam rendered the user message as canonical JSON of the inputs. So the
prompt's own text had no path to the provider: the CR-34/CR-42/CR-44 class again,
a shape specified in a form that cannot be implemented as written.

The resolution takes `04` §5.2 rule 3 at its word -- *"`skill_sha256` binds every
run to exact **prompt** bytes"* -- which is only true if the prompt is what this
loader loaded. So `user.md` and `output_schema.json` are read from the same
directory and land in the same whole-directory digest. **No declared shape
changes**: `LoadedSkill` is this package's own type, `02` §10.1 is untouched, and
a skill directory without either file behaves exactly as before. The alternative
was a second loader with a second digest field, which is two answers to "what was
asked" -- the drift the version discipline exists to prevent.
"""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict

from adopt_const import SKILL_DESCRIPTION_MAX_CHARS, SKILL_NAME_MAX_CHARS
from adopt_obs import AdoptError, ErrorCode

__all__ = ["LoadedSkill", "load_skill"]

_SKILL_FILENAME: Final[str] = "SKILL.md"
_FRONTMATTER_FENCE: Final[str] = "---"
_SCRIPTS_DIRNAME: Final[str] = "scripts"
#: A prompt version directory's other two files (CR-47). Optional: an ordinary
#: skill has neither, and the loader must not require a carrier format to grow
#: files it has no use for.
_USER_TEMPLATE_FILENAME: Final[str] = "user.md"
_OUTPUT_SCHEMA_FILENAME: Final[str] = "output_schema.json"

#: Split into at most three parts: the empty string before the opening fence,
#: the frontmatter, and the body. `maxsplit` matters -- a body containing a
#: horizontal rule is ordinary Markdown and must not be read as a fence.
_FRONTMATTER_SPLITS: Final[int] = 2


class LoadedSkill(BaseModel):
    """A resolved, hashed, validated skill directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    path: Path
    name: str
    description: str
    #: The whole-directory digest that lands in `agent_run.skill_sha256`.
    sha256: str
    #: `SKILL.md` with its frontmatter removed. This is what a prompt renders.
    body: str
    #: Where `scripts/` were copied, when a scratch directory was supplied.
    #: **Never executed by this loader**, and there is no API here that would.
    materialized_scripts: tuple[Path, ...] = ()
    #: `user.md`, verbatim, when the directory is a prompt version (CR-47). The
    #: seam renders it with the request's inputs; `None` means the seam falls back
    #: to the canonical JSON rendering, which is what an ordinary skill wants.
    user_template: str | None = None
    #: `output_schema.json`, when the directory declares one. `04` §5's table
    #: gives every prompt an output schema, so a prompt that declares one has it
    #: enforced without each caller remembering to pass it.
    output_schema: dict[str, Any] | None = None


def _reject(message: str, hint: str) -> AdoptError:
    """Every refusal in this module is one code.

    `MANIFEST_INVALID` is `02` §13's `usage` code for "a document you supplied is
    malformed", which is exactly what a broken SKILL.md is. Minting an
    agent-specific code would give the registry a second way to say the same
    thing at a second call site -- the drift CR-31 and CR-38 both argued
    against.
    """
    return AdoptError(ErrorCode.MANIFEST_INVALID, message=message, hint=hint)


def _resolve(skill_ref: str, root: Path) -> Path:
    """Resolve `skill_ref` under `root`, refusing anything that escapes it.

    A `skill_ref` is caller-supplied, and a caller that can name `../../etc`
    can hash and materialize any directory on the machine. Resolving both sides
    and comparing is what makes the containment a property of the filesystem
    rather than of a string check that `..` was absent -- a symlink inside the
    tree defeats the string check and not this one.
    """
    if not skill_ref.strip():
        raise _reject(
            "skill_ref is empty",
            "AI spec §6: a skill_ref names a directory under the skills root.",
        )
    root_resolved = root.resolve()
    candidate = (root_resolved / skill_ref).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise _reject(
            f"skill_ref {skill_ref!r} resolves outside the skills root",
            "A skill_ref names a directory inside the skills library, never a path out of it.",
        )
    if not candidate.is_dir():
        raise _reject(
            f"skill_ref {skill_ref!r} is not a directory",
            "AI spec §6: skills are versioned by directory (`extract-django/v1/`).",
        )
    return candidate


def _digest_directory(path: Path) -> str:
    """SHA-256 over sorted (relative path, content) pairs.

    The path is fed into the hash as well as the content. Renaming
    `references/erp.md` to `references/crm.md` changes what the skill means to
    whoever reads it, and a content-only digest would report the two as the same
    skill.
    """
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def _parse_frontmatter(text: str, skill_ref: str) -> tuple[str, str, str]:
    """Return `(name, description, body)` or raise.

    Both bounds come from `adopt_const`; AI spec §6 states them and
    `constants-sync` is the instrument that keeps the two agreeing.
    """
    if not text.startswith(_FRONTMATTER_FENCE):
        raise _reject(
            f"{skill_ref}/SKILL.md has no YAML frontmatter",
            "AI spec §6: SKILL.md opens with a `---` fenced frontmatter block.",
        )
    parts = text.split(_FRONTMATTER_FENCE, _FRONTMATTER_SPLITS)
    if len(parts) < _FRONTMATTER_SPLITS + 1:
        raise _reject(
            f"{skill_ref}/SKILL.md has an unterminated frontmatter block",
            "Close the frontmatter with a second `---` line.",
        )
    try:
        loaded = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise _reject(
            f"{skill_ref}/SKILL.md frontmatter is not valid YAML: {exc.__class__.__name__}",
            "Frontmatter is YAML; the parse error is the loader's, not the model's.",
        ) from exc
    if not isinstance(loaded, dict):
        raise _reject(
            f"{skill_ref}/SKILL.md frontmatter is not a mapping",
            "AI spec §6: frontmatter declares `name` and `description`.",
        )

    name = loaded.get("name")
    description = loaded.get("description")
    if not isinstance(name, str) or not name.strip():
        raise _reject(
            f"{skill_ref}/SKILL.md frontmatter has no `name`",
            f"AI spec §6: `name` is required and at most {SKILL_NAME_MAX_CHARS} characters.",
        )
    if not isinstance(description, str) or not description.strip():
        raise _reject(
            f"{skill_ref}/SKILL.md frontmatter has no `description`",
            "AI spec §6: `description` is required and at most "
            f"{SKILL_DESCRIPTION_MAX_CHARS} characters.",
        )
    if len(name) > SKILL_NAME_MAX_CHARS:
        raise _reject(
            f"{skill_ref} declares a {len(name)}-character name, over the "
            f"{SKILL_NAME_MAX_CHARS}-character limit",
            "Shorten the name; it is an identifier, not a summary.",
        )
    if len(description) > SKILL_DESCRIPTION_MAX_CHARS:
        raise _reject(
            f"{skill_ref} declares a {len(description)}-character description, over the "
            f"{SKILL_DESCRIPTION_MAX_CHARS}-character limit",
            "Move the detail into the body; the description is what a router reads.",
        )
    return name, description, parts[2].lstrip("\n")


def _materialize_scripts(source: Path, scratch: Path) -> tuple[Path, ...]:
    """Copy `scripts/` into the scratch directory. **Never execute them.**

    Copying rather than pointing at the library is deliberate: a skill's scripts
    are inputs to whatever the agent is asked to do, and handing out a path
    inside the installed library invites something to write back into it. There
    is no execution here and no API in this package that would run one.
    """
    scripts = source / _SCRIPTS_DIRNAME
    if not scripts.is_dir():
        return ()
    target = scratch / _SCRIPTS_DIRNAME
    shutil.copytree(scripts, target, dirs_exist_ok=True)
    return tuple(sorted(p for p in target.rglob("*") if p.is_file()))


def load_skill(skill_ref: str, *, root: Path, scratch: Path | None = None) -> LoadedSkill:
    """Resolve, validate and hash a skill directory. AI spec §6.

    Raises `MANIFEST_INVALID` on anything malformed, and it raises **before**
    any provider call because it is called before one -- the ordering is the
    runner's, and `test_skills.py` asserts the loader is reached first by
    driving a broken skill through `Runner.run` and observing zero adapter
    calls.
    """
    path = _resolve(skill_ref, root)
    skill_file = path / _SKILL_FILENAME
    if not skill_file.is_file():
        raise _reject(
            f"{skill_ref} has no {_SKILL_FILENAME}",
            "AI spec §6: a skill directory is defined by its SKILL.md.",
        )
    name, description, body = _parse_frontmatter(skill_file.read_text(encoding="utf-8"), skill_ref)
    return LoadedSkill(
        ref=skill_ref,
        path=path,
        name=name,
        description=description,
        sha256=_digest_directory(path),
        body=body,
        materialized_scripts=(_materialize_scripts(path, scratch) if scratch is not None else ()),
        user_template=_optional_text(path / _USER_TEMPLATE_FILENAME),
        output_schema=_optional_schema(path / _OUTPUT_SCHEMA_FILENAME, skill_ref),
    )


def _optional_text(path: Path) -> str | None:
    """Read a file verbatim, or `None` when it is absent.

    Verbatim matters here more than anywhere else in this module: `04` §5.2 makes a
    prompt one byte sequence forever, so nothing is stripped, reflowed or
    normalized on the way in. A loader that tidied trailing whitespace would change
    what `detect-001@1` means while leaving its id alone.
    """
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _optional_schema(path: Path, skill_ref: str) -> dict[str, Any] | None:
    """Read a declared output schema, or `None`.

    A malformed schema raises here, with the loader's other refusals, and
    therefore **before any provider call** -- the same ordering a broken
    frontmatter gets, for the same reason: a prompt whose schema cannot be parsed
    can never be answered, and discovering that after dispatch bills a client for
    it.
    """
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _reject(
            f"{skill_ref}/{_OUTPUT_SCHEMA_FILENAME} is not valid JSON: {exc.msg}",
            "A prompt's output schema is JSON Schema; the parse error is the file's.",
        ) from exc
    if not isinstance(loaded, dict):
        raise _reject(
            f"{skill_ref}/{_OUTPUT_SCHEMA_FILENAME} is not a JSON object",
            "AI spec §5.1: an output schema is a strict, closed JSON Schema object.",
        )
    return loaded
