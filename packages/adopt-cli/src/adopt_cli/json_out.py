"""Output rendering. `--json` suppresses every human affordance.

Two renderings, one source. The JSON envelope is a contract from 0.3.0: command
names, flags, JSON keys and exit codes are additive-only thereafter, exactly as
for the schema. The human rendering is free to change.
"""

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

__all__ = ["emit", "emit_error"]

_stdout = Console(file=sys.stdout, highlight=False, soft_wrap=True)
_stderr = Console(file=sys.stderr, highlight=False, soft_wrap=True)


def emit(payload: dict[str, Any], *, as_json: bool, title: str = "") -> None:
    """Render a command result.

    In JSON mode nothing but the envelope reaches stdout -- no banner, no
    colour, no progress. A caller piping into `jq` must not have to strip
    anything.
    """
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=False, default=str))
        return
    _render_human(payload, title)


def _render_human(payload: dict[str, Any], title: str) -> None:
    if title:
        _stdout.print(f"[bold]{title}[/bold]")
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            table = Table(title=key, title_justify="left", show_lines=False)
            for column in value[0]:
                table.add_column(column)
            for row in value:
                table.add_row(*[str(row.get(c, "")) for c in value[0]])
            _stdout.print(table)
        elif isinstance(value, list):
            _stdout.print(f"{key}: {', '.join(str(v) for v in value) or '(none)'}")
        elif isinstance(value, dict):
            _stdout.print(f"{key}:")
            for inner_key, inner_value in value.items():
                _stdout.print(f"  {inner_key}: {inner_value}")
        else:
            _stdout.print(f"{key}: {value}")


def emit_error(envelope: dict[str, Any], *, as_json: bool) -> None:
    """Render the one documented error envelope to stderr."""
    if as_json:
        print(json.dumps(envelope, indent=2, default=str), file=sys.stderr)
        return
    error = envelope.get("error", {})
    _stderr.print(f"[bold red]{error.get('code')}[/bold red] ({error.get('category')})")
    if error.get("message"):
        _stderr.print(str(error["message"]))
    if error.get("hint"):
        _stderr.print(f"[dim]hint: {error['hint']}[/dim]")
