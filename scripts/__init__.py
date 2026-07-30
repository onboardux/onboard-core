"""Shared helpers for the CI gate scripts.

These scripts are importable so their tests can drive them with synthetic
inputs instead of shelling out. A gate that can only be exercised through a
subprocess is a gate nobody writes a negative test for.
"""
