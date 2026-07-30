"""The single model seam: budget, tracing, idempotency, adapters.

Empty by design at S0. Implemented in S7.

Invariants carried forward: provider SDKs are imported only in `adapters/`,
budget logic lives only in `budget.py`, traces record digests and never
payloads, and no model identifier is hard-coded anywhere including as a default.
"""
