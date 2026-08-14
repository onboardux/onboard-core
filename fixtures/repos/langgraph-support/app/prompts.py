"""Prompt sources for the support agent — three of them, deliberately.

Two live in this repository as files, one lives in this module as a template
literal, and two live somewhere a commit cannot reach: a hosted console and the
application database.
"""

from pathlib import Path

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

ANSWER_GROUNDED = PROMPT_DIR / "answer_grounded.md"
TRIAGE_CLASSIFIER = PROMPT_DIR / "triage_classifier.md"

ESCALATION_TEMPLATE = """You are drafting an escalation note for a support case.

Case: {case_id}
Customer tier: {tier}
Summary: {summary}

Write two sentences. State what was tried and what is blocked. Do not apologise.
"""


class ConsolePrompt:
    """A prompt held in the vendor console, referenced here by id only."""

    def __init__(self, prompt_id: str) -> None:
        self.prompt_id = prompt_id


class DbPrompt:
    """A prompt row read from the application database at request time."""

    def __init__(self, key: str) -> None:
        self.key = key


GREETING = ConsolePrompt("support-greeting")
FAQ_ANSWER = DbPrompt("faq_answer")


def load(path: Path) -> str:
    return path.read_text(encoding="utf-8")
