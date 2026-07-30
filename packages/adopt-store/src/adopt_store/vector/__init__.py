"""The `VectorIndex` seam. The only module permitted to import a vector driver.

Empty by design at S0. The protocol lands in S2 with no implementation and the
feature flag off.

Swapping the index must not touch the canonical schema or bump `export_version`
-- that is the whole reason the seam exists, given the driver is pre-1.0.
"""
