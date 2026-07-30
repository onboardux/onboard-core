"""The `adopt` command surface.

Deliberately thin. This package holds no business logic: it resolves
configuration, dispatches, renders, and maps errors to exit codes. Rich is used
here and nowhere else -- a library that formats for a terminal cannot be
embedded.
"""

from adopt_cli.main import app, main

__all__ = ["app", "main"]
