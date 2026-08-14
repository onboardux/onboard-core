# `poisoned-import` — the fixture that must not detonate

A synthetic client tree whose modules have **side effects on import**. It exists
for one assertion, `01` F7.2 and N8:

> The system never imports, executes, evaluates or dynamically loads client code.
> Verified by a poisoned-fixture test that must not detonate.

`detonator.py` writes a canary file the moment it is imported. `tests/integration/
test_no_client_import.py` runs a full `adopt map` over this tree and asserts the
canary is **absent** afterwards. If any extractor, the file index, the plugin
audit or the archetype detector ever imports client code, the canary appears and
the suite goes red naming the file that wrote it.

**The modules are deliberately plausible.** A tree of obviously-hostile files
would be one a careless implementation might special-case; these look like an
ordinary Django-ish service, which is what a real client tree looks like when it
runs code at module scope — a database connection opened at import, a metrics
client registered, a migration applied. Those are not attacks. They are normal,
and they are exactly why the tool reads bytes instead of importing.

This tree is **not** linted (`ruff.toml` excludes `tests/fixtures/repos`, and the
same argument applies here): every file exists to carry a signal, not to be good
code.
