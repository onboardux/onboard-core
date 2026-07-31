"""The four emitters. Each is a pure function of the manifest.

No emitter reads a database, a clock or an environment variable, and no emitter
holds a fact the manifest does not. That is what makes two runs byte-identical,
which is what makes `generate --check` a drift check rather than a coin toss.

These modules are the one place outside `schema/migrations/**` permitted to
contain a `CREATE TABLE` statement (CR-24): they are the code that turns the
manifest into those files, so exempting them narrows nothing the
`no-foreign-tables` contract was protecting.
"""

__all__: list[str] = []
