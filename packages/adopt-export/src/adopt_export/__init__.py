"""Export bundle writer and reader.

Empty by design at S0. Implemented in S5.

Invariants carried forward: no network, writes confined to the target directory,
and export -> import -> export produces byte-identical table files. Runtime-annex
tables are excluded by construction, not by a filter.
"""
