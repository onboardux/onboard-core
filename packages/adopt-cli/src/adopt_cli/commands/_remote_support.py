"""Deciding whether a verb runs locally or through the plane, in one place.

`adopt ask --escalate` and `adopt answer` both ask the same question -- *is this
store a replica of an operated system?* -- and both must answer it identically.
Two copies of that decision is how one verb keeps writing locally after the other
started routing, which leaves an escalation in the plane whose answer is on a
laptop.

**Absence is the local mode, not a refusal.** A store with no remote configured
is a field store, and every Build 3 path through it is exactly as it was: this
returns `None` and the caller does what it always did. The refusal lives one
level down, in `resolve_remote`, and fires only when a remote is *partly*
configured -- a URL with no system, a system with no token -- because that is an
operator who meant to route and will otherwise watch their answer land nowhere
anybody else can read it.
"""

import os
from collections.abc import Mapping

from adopt_cli.config import resolve_all
from adopt_cli.remote import PLANE_TOKEN_ENV_KEY, PLANE_URL_KEY, Remote, resolve_remote

__all__ = ["configured_remote", "resolved_config"]


def resolved_config(injected: Mapping[str, str | None] | None = None) -> dict[str, str | None]:
    """Configuration as a plain mapping. Injectable, so a test drives no environment."""
    if injected is not None:
        return dict(injected)
    return {item.key: item.value for item in resolve_all()}


def configured_remote(
    config: Mapping[str, str | None] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Remote | None:
    """The configured control plane, or `None` for an ordinary local store.

    Args:
        config: Resolved configuration; read for real when omitted.
        environ: Where the token is looked up. Injected for the same reason the
            config layers are -- a test that had to set a real environment
            variable to exercise remote mode would leak it into every test after
            it in the process.

    Raises:
        AdoptError: ``PLANE_REMOTE_NOT_CONFIGURED`` when a remote is *partly*
            configured. Fully absent is `None`, which is the local mode.
    """
    resolved = resolved_config(config)
    if not (resolved.get(PLANE_URL_KEY) or "").strip():
        return None
    source = os.environ if environ is None else environ
    token_variable = resolved.get(PLANE_TOKEN_ENV_KEY) or ""
    return resolve_remote(resolved, source.get(token_variable))
