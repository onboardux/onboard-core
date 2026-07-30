# Planted-secret fixture tree

Every file in this directory carries at least one **planted secret**: a string
that must never appear in a log line, a trace, an error envelope or a telemetry
payload.

`tests/property/test_log_egress.py` reads every secret out of this tree and
asserts, over generated payload shapes, that none of them can reach an emitted
log line.

**Nothing here is a real credential.** Each value is syntactically shaped like
the credential class it stands for so that a redactor which pattern-matches on
shape is exercised honestly, but every value is invalid by construction.

Add a file here whenever a new class of content becomes loggable-adjacent. The
property test picks it up automatically -- it globs the tree rather than naming
files, so a new fixture strengthens the test without touching the test.
