"""The writer's source text -- implementation spec §5.5 invariants 3 and 5.

*"The module contains no `UPDATE … revision`, no `DELETE`, no `DROP` -- asserted
by source scan, not review."*

**Why a source scan when the SQL trace already exists.** The trace proves what
one run executed against one fixture; this proves what the module *can* execute
at all. A `DELETE` on a branch no fixture reaches is invisible to the trace and
obvious here -- and the branch nobody reaches is exactly where a mutation gets
added, because that is where it looks harmless.

`no-revision-update` in `importlinter.ini` covers `UPDATE … *_revision` across
both repositories. This adds the two things that rule does not say: **no
`DELETE` and no `DROP` anywhere in this module**, and **`status='dead'` is never
written** (B1-CR-07). Neither is a widening of the import contract -- a `DELETE`
against `identity` is legal under `no-revision-update` and forbidden here.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_WRITER = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "adopt-map"
    / "src"
    / "adopt_map"
    / "writer.py"
)

#: The module's own text, minus its docstrings. The docstrings **name** what the
#: module must not do -- "no `UPDATE … revision`, no `DELETE`, no `DROP`" -- so
#: scanning raw lines would make this test fail on the sentence describing the
#: rule it enforces, which is how a gate acquires an exemption it never needed.
_CODE = re.sub(r'"""(?:.|\n)*?"""', "", _WRITER.read_text(encoding="utf-8"))


def test_the_writer_module_exists_and_has_content() -> None:
    """The scan's subject resolves.

    *Fails when* the module is renamed or moved and this test keeps passing over
    an empty string. *Matters because* a scan over a file that does not exist
    reports clean forever -- Build 0 CR-67's finding, and the reason
    `build0_untouched.py` refuses to run against a name that does not resolve.
    """
    assert _WRITER.is_file()
    assert len(_CODE) > 1000, "the writer is suspiciously small; is the scan reading the file?"


@pytest.mark.append_only
@pytest.mark.parametrize(
    ("pattern", "why"),
    [
        (r"\bUPDATE\s+\w*_revision\b", "an UPDATE against a revision table"),
        (r"\bDELETE\b", "a DELETE, against any table"),
        (r"\bDROP\b", "a DROP, against anything"),
        (r"\bTRUNCATE\b", "a TRUNCATE"),
    ],
)
def test_the_writer_contains_no_destructive_statement(pattern: str, why: str) -> None:
    """*Fails when* a destructive statement appears anywhere in the writer.

    *Matters because* append-only is the guarantee every downstream build's
    history depends on, and the writer is the one module in Build 1 that could
    break it. *No other instrument catches the unreachable branch* -- the SQL
    trace only sees statements a fixture actually executed.
    """
    assert not re.search(pattern, _CODE, re.IGNORECASE), f"the writer contains {why}"


@pytest.mark.append_only
def test_the_writer_never_writes_status_dead() -> None:
    """*Fails when* the writer learns to mark an identity dead (B1-CR-07).

    *Matters because* absence and parse failure are **indistinguishable from
    here**: a referent missing from a run may have been deleted or may simply
    have defeated the parser, and a false death is a false retirement in every
    build downstream. PRD §8's autonomy matrix says the decision is made by
    *"nobody in Build 1"*. *No other instrument catches it because* `dead` is a
    legal `IdentityStatus` and the row would insert cleanly.
    """
    assert '"dead"' not in _CODE
    assert "'dead'" not in _CODE


@pytest.mark.append_only
def test_the_writer_reaches_no_sql_of_its_own() -> None:
    """*Fails when* the writer starts assembling SQL instead of calling a port.

    *Matters because* `no-raw-sqlite` names `adopt_map` a source module and this
    package's whole storage story is "declare a port, be handed a realization"
    (`adopt_map.ports`). A hand-written statement here would be dialect knowledge
    in a package the Postgres realization also has to serve. *No other instrument
    catches a string that is built but not yet executed.*
    """
    for keyword in ("INSERT INTO", "SELECT ", "BEGIN;", "COMMIT;", "ROLLBACK;"):
        assert keyword not in _CODE, f"the writer contains raw SQL: {keyword!r}"
