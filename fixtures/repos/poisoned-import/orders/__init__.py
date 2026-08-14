"""Importing the package alone is enough to detonate its submodule."""

from pathlib import Path

Path(__file__).with_name("PACKAGE_IMPORTED.txt").write_text("package imported\n", encoding="utf-8")
