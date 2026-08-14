"""What `web.sqlalchemy.schema` and `web.migrations` must agree on.

**These two extractors describe the same tables.** A model declaration and the
migration that created it are two observations of one referent, so if they
disagree about the namespace or the key, every column in the system is minted
twice and every coverage figure doubles. That is `02` §3.1 rule 3 at its sharpest,
and the reason the key construction lives here rather than once in each module.

`02` §3.1 fixes the `db_field` namespace as `<dialect>:<schema>.<table>` and the
key as the column name, **with the table itself keyed `*`**.

**The dialect is detected, and its fallback is a statement rather than a guess.**
Nothing in a source tree necessarily declares which database it runs against --
that lives in a connection string, which is a secret and which `01` N9 forbids
reading the value of. So the dialect is taken from the non-secret declarations
that do appear (a Django `ENGINE`, an Alembic URL scheme) and, when none does,
the namespace records `sql` -- meaning *"a SQL database whose dialect this tree
does not declare"*, which is true, rather than `pg`, which would be a guess that
silently forks against the next run on a tree that does declare one.
"""

from typing import Final

__all__ = [
    "DEFAULT_DIALECT",
    "DEFAULT_SCHEMA",
    "TABLE_KEY",
    "detect_dialect",
    "field_namespace",
]

#: `02` §3.1: *"the table itself uses `*`"*.
TABLE_KEY: Final[str] = "*"

#: When the tree declares no schema. Both PostgreSQL and MySQL resolve an
#: unqualified table against a default schema, and `public` is the one the
#: `02` §3.1 example uses.
DEFAULT_SCHEMA: Final[str] = "public"

#: See the module docstring. Not a tunable: it is a vocabulary member meaning
#: "undeclared", so it has no evidence to be ratified against and no `03` §3 row.
DEFAULT_DIALECT: Final[str] = "sql"

#: Substrings that identify a dialect in a Django `ENGINE` or a URL scheme,
#: longest first so `postgresql` is not matched by a shorter neighbour.
_DIALECT_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("postgresql", "pg"),
    ("postgres", "pg"),
    ("psycopg", "pg"),
    ("mysql", "mysql"),
    ("mariadb", "mysql"),
    ("sqlite", "sqlite"),
    ("oracle", "oracle"),
    ("mssql", "mssql"),
    ("sqlserver", "mssql"),
)


def detect_dialect(text: str) -> str | None:
    """The dialect a declaration names, or `None`.

    Reads the **scheme or engine name only**. A connection string's credentials
    are never read, recorded or returned: this looks at whether the text contains
    `postgresql`, not at what follows it.
    """
    lowered = text.lower()
    for marker, dialect in _DIALECT_MARKERS:
        if marker in lowered:
            return dialect
    return None


def field_namespace(dialect: str, schema: str, table: str) -> str:
    """`<dialect>:<schema>.<table>` -- `02` §3.1's `db_field` namespace."""
    return f"{dialect}:{schema}.{table}"
