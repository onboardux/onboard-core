"""Capability-manifest validation, envelope validation, the signature interface.

Empty by design at S0. Implemented in S6.

Invariants carried forward: both validators raise and neither returns a soft
warning, and the observability boundary is the authority for permitted outbound
policies -- never the caller's declaration.
"""
