"""Scope-injecting facades. Empty by design at S0; implemented in S2.

Facades inject scope and generate ids. A caller-supplied id or scope is
rejected, and no facade exposes an update or delete on any `*_revision` table.
"""
