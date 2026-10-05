"""The independence-assurance journey -- the pivot's acceptance test, end to end.

*Fails when* any link of the claim the product now sells breaks: an AI
service's API goes unwatched, an unanswerable question is answered KNOWN, a
disputed answer reaches nobody, a build/deploy change reads as cosmetic, or a
passed exit test survives a change to what it was performed against. *Matters
because* the offering is "prove you could run it without them, and know the
moment you couldn't" -- each failure above is a moment the product would say
"still true" when it is not. *No other instrument catches it because* each
link has its own unit test, and the claim is only as good as their composition
on one system, in the order an engagement actually runs.

Every step is a defect reproduced live on 2026-10-05
(`documentations/pivot-discovery/independence/independence-validation-transcript.md`):
T9 (`ai` archetype skipped endpoints), T1 (false KNOWN), T2 (silent dispute),
T7 (the drill), T4a (Dockerfile and CI steps read as cosmetic) and T8 (a passed
task never invalidated). The final step is H5's guard: a comment-only edit must
stale nothing, or every runbook goes stale on every commit.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from adopt_obs import ExitCode

pytestmark = pytest.mark.e2e

ENTRY_POINT = (
    Path(__file__).resolve().parents[2] / "packages" / "adopt-cli" / "src" / "adopt_cli" / "main.py"
)
SCOPE = "northwind/independence/orders-api/prod"
SYSTEM = "orders-api"
ANSWERS = {"artifact_access": True, "deploy_signal": True, "safe_interaction": True}
U = f"onboard-v1://{SCOPE}"
REFUNDS = f"{U}/endpoint/-/POST%20%2Fv1%2Frefunds"
DEPLOY_JOB = f"{U}/job/ci/deploy"
DOCKERFILE = f"{U}/metadata_component/file/Dockerfile"
MODEL = f"{U}/model_pin/anthropic/claude-sonnet-4-5-20250929"
PROMPT = f"{U}/prompt/-/prompts/support_system_prompt.md"

DOCKER = "FROM python:3.12-slim\nCOPY . /app\nRUN pip install -r /app/requirements.txt\n"
WORKFLOW = (
    "name: deploy\non: push\njobs:\n"
    "  migrate:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - run: alembic upgrade head\n"
)
REPO = {
    "app/main.py": (
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
        '@app.post("/v1/refunds")\ndef create_refund(order_id: str):\n'
        '    return {"status": "pending_approval"}\n'
    ),
    "app/settings.py": 'MODEL = "claude-sonnet-4-5-20250929"\n',
    "prompts/support_system_prompt.md": "You are the order-support assistant.\n",
    "Dockerfile": DOCKER,
    "requirements.txt": "fastapi>=0.110\n",
    ".github/workflows/deploy.yml": WORKFLOW,
    "docs/redeploy.md": (
        "---\naudience: client_ops\n---\n# Redeploy from scratch\n\n"
        f"Build the image from {DOCKERFILE} and run the pipeline {DEPLOY_JOB}: "
        "migrate with alembic, then release.\n"
    ),
    "docs/refunds.md": (
        "---\naudience: client_ops\n---\n# Replaying a stuck refund\n\n"
        f"Refunds go through {REFUNDS}. To replay a stuck refund after a restore, "
        "re-POST it with the original order id.\n"
    ),
    "docs/assistant.md": (
        "---\naudience: client_ops\n---\n# Operating the support assistant\n\n"
        f"The assistant runs on {MODEL} with the system prompt {PROMPT}.\n"
    ),
}
CHECKLIST = f"""audience: client_ops
tasks:
  - id: redeploy
    task: "Redeploy into a clean environment from the pack alone"
    outcome: pass
    uri: "{DEPLOY_JOB}"
    performed_by: lee
  - id: replay-refund
    task: "Replay a stuck refund after a restore"
    outcome: pass
    uri: "{REFUNDS}"
    performed_by: lee
  - id: rotate-db
    task: "Rotate the database credentials without the builder"
    outcome: fail
"""


def _run(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_POINT), *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def _payload(done: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    text = done.stdout
    start = text.rindex("\n{\n") + 1 if "\n{\n" in text else text.index("{")
    return dict(json.loads(text[start:]))


@pytest.fixture
def system(tmp_path: Path) -> dict[str, Path]:
    if not shutil.which("git"):  # pragma: no cover -- every CI runner ships git
        pytest.skip("git is not on PATH, and ingest reads a real checkout")
    checkout = tmp_path / "orders-api"
    for relative, content in REPO.items():
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for argv in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *argv], cwd=str(checkout), check=True, capture_output=True)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    checklist = tmp_path / "checklist.yaml"
    checklist.write_text(CHECKLIST, encoding="utf-8")
    return {
        "checkout": checkout,
        "store": tmp_path / "store.db",
        "tmp": tmp_path,
        "answers": answers,
        "checklist": checklist,
    }


def test_independence_is_proved_and_knows_when_it_stopped_being_true(
    system: dict[str, Path],
) -> None:
    checkout, store = system["checkout"], system["store"]

    def adopt(*argv: str, expect: int = ExitCode.SUCCESS) -> dict[str, Any]:
        done = _run(*argv, "--store", str(store), "--json", cwd=checkout)
        assert done.returncode == expect, (
            f"adopt {' '.join(argv)} -> {done.returncode}: {done.stderr}"
        )
        return _payload(done)

    # Enrol an AI service. T9: the `ai` archetype now maps the API it is reached through.
    adopt("init", ".", "--scope", SCOPE, "--answers", str(system["answers"]), "--archetype", "ai")
    adopt("map", ".")
    adopt("ingest", "docs")
    uris = {row[0] for row in sqlite3.connect(store).execute("SELECT uri FROM identity")}
    assert REFUNDS in uris, "the ai archetype did not map the service's endpoint"

    # T1: an unanswerable question sharing two words with a runbook is UNKNOWN.
    unknown = adopt("ask", "how do I rotate the database password for the support assistant?")
    assert unknown["branch"] == "unknown", unknown

    # T2: disputing a KNOWN answer reaches the owner as a bug report on what was served.
    disputed = adopt("ask", "which system prompt does the support assistant run on?", "--escalate")
    assert disputed["branch"] == "known", disputed
    branch = (
        sqlite3.connect(store)
        .execute(
            "SELECT branch, prior_revision_id FROM escalation WHERE id = ?",
            (disputed["escalation_id"],),
        )
        .fetchone()
    )
    assert branch[0] == "bug_report" and branch[1]

    # T7: the cold drill -- two exit tests performed without the builder, one not.
    adopt("handover", "start", "--system", SYSTEM, "--receiving-owner", "northwind-platform")
    adopt("handover", "elicit", "--system", SYSTEM, "--out", str(system["tmp"] / "ho"))
    adopt("handover", "pack", "--system", SYSTEM, "--out", str(system["tmp"] / "ho"))
    adopt(
        "handover",
        "verify",
        "--system",
        SYSTEM,
        "--checklist",
        str(system["checklist"]),
        expect=ExitCode.DEGRADED_WITH_FINDINGS,
    )
    adopt("handover", "snapshot", "--system", SYSTEM, "--out", str(system["tmp"] / "acc"))
    adopt("handover", "close", "--system", SYSTEM, "--accepted-by", "priya")
    held = adopt("handover", "status", "--system", SYSTEM, "--strict")["independence"]
    assert (held["passed"], held["valid"], held["invalidated"]) == (2, 2, 0)

    # T4a: the runtime image and the migration step change. Nothing else does.
    (checkout / "Dockerfile").write_text("FROM node:20-alpine\nRUN npm ci\n", encoding="utf-8")
    workflow = checkout / ".github" / "workflows" / "deploy.yml"
    workflow.write_text(
        WORKFLOW.replace("alembic upgrade head", "python manage.py migrate"), encoding="utf-8"
    )
    changed = adopt("refresh", ".", "--no-probes", expect=ExitCode.DEGRADED_WITH_FINDINGS)
    assert changed["counts_by_class"].get("BINDING_INTACT_SEMANTICS_CHANGED", 0) >= 2, changed
    assert not changed["exempt"], "a referent was exempted: a pack did not run"
    assert adopt("ask", f"how do I redeploy using {DEPLOY_JOB}?")["branch"] == "stale"

    # T8: the drilled redeploy no longer holds; the refund drill, untouched, does.
    after = adopt(
        "handover", "status", "--system", SYSTEM, "--strict", expect=ExitCode.DEGRADED_WITH_FINDINGS
    )["independence"]
    by_id = {task["task_id"]: task for task in after["tasks"]}
    assert by_id["redeploy"]["status"] == "invalidated"
    assert by_id["redeploy"]["cause"] == "BINDING_INTACT_SEMANTICS_CHANGED"
    assert by_id["replay-refund"]["status"] == "valid"

    # H5's guard: a comment-only edit to the same recipe is recorded as render-only
    # and exits 0 -- reviewable, never actionable, and it stales nothing.
    dockerfile = checkout / "Dockerfile"
    dockerfile.write_text(
        "# runtime image\n" + dockerfile.read_text(encoding="utf-8"), encoding="utf-8"
    )
    quiet = adopt("refresh", ".", "--no-probes")
    assert set(quiet["counts_by_class"]) <= {"BINDING_INTACT_RENDER_ONLY"}, quiet
    assert quiet["written"]["bindings_staled"] == 0
