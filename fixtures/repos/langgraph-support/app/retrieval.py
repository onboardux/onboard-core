"""Retrieval, configured from `config/retrieval.yaml`."""

from pathlib import Path

import yaml

CONFIG = Path(__file__).parent.parent / "config" / "retrieval.yaml"


def settings() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["retrieval"]


def search(question: str) -> list:
    """Return the top-k grounding passages for a question."""
    return []
