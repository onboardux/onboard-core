"""The identity URI: build, parse, validate.

Empty by design at S0. Implemented in S3.

Invariants carried forward: built from slugs and never from ULIDs, deterministic
across runs and machines, NFC then a single pass of percent-encoding, and a URI
is never rewritten in place.
"""
