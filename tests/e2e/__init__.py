"""The nine critical user journeys, one test each.

An E2E test here drives the journey the way a downstream build item will: through
the public facades, against a real store, asserting the observable outcome of
every numbered step **and** the journey's failure branch. PRD §4 is the source of
record for the steps; nothing in this directory invents one.
"""
