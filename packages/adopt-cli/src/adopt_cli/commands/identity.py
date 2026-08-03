"""`adopt identity build | parse | validate` -- contracts §14.

Three pure functions behind three commands. No store is opened and no socket is
touched: a URI is a deterministic function of a scope path and a referent, which
is what lets an integrator check one from a shell script, in a container, with no
`.adopt/` directory anywhere.

**Scope is given, not inferred.** `--scope firm/engagement/system/environment`
is required, because every alternative is worse: reading it from a store makes a
pure function depend on a file, and defaulting it means the tool silently builds
a URI for a scope the caller did not name -- and a wrong URI is not a wrong
answer, it is a *different referent*, which nothing downstream can detect.
"""

from typing import Annotated

import typer

from adopt_cli.json_out import emit
from adopt_identity import build_uri, parse_uri, validate_uri
from adopt_scope import Scope, ScopeNode, ScopePath

__all__ = ["app"]

app = typer.Typer(
    name="identity",
    help="Build, parse and validate identity URIs. Deterministic, offline, no store.",
    no_args_is_help=True,
)

ScopeOption = Annotated[
    str,
    typer.Option(
        "--scope",
        help="firm/engagement/system/environment, as immutable slugs.",
        show_default=False,
    ),
]
KindOption = Annotated[str, typer.Option("--kind", help="The identity_kind.", show_default=False)]
NamespaceOption = Annotated[
    str | None,
    typer.Option("--namespace", help="The namespace. Omit where the kind needs none."),
]
KeyOption = Annotated[
    list[str],
    typer.Option(
        "--key",
        help="The local key. Repeat for a multi-segment key such as a symbol path; "
        "a single value is one segment, so any slash inside it is data.",
        show_default=False,
    ),
]
UriArgument = Annotated[str, typer.Argument(help="The identity URI.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]


def _scope_from(path: str) -> Scope:
    """A `Scope` carrying slugs only.

    The ids are absent because this command never touches a store and the URI is
    built from slugs alone (CR-05). They are set to the slug rather than to an
    empty string so that anything which did misuse one fails visibly instead of
    joining to a row that happens to have an empty id.
    """
    parsed = ScopePath.parse(path)
    levels = (parsed.firm, parsed.engagement, parsed.system, parsed.environment)
    nodes = tuple(None if slug is None else ScopeNode(id=slug, slug=slug) for slug in levels)
    return Scope(
        firm=nodes[0] or ScopeNode(id=parsed.firm, slug=parsed.firm),
        engagement=nodes[1],
        system=nodes[2],
        # const-sync: ok -- positional index into the four scope levels.
        environment=nodes[3],
    )


@app.command()
def build(
    scope: ScopeOption,
    kind: KindOption,
    key: KeyOption,
    namespace: NamespaceOption = None,
    json_output: JsonOption = False,
) -> None:
    """Build the canonical URI for a referent."""
    uri = build_uri(_scope_from(scope), kind, namespace, tuple(key))
    emit({"uri": uri}, as_json=json_output, title="adopt identity build")


@app.command()
def parse(uri: UriArgument, json_output: JsonOption = False) -> None:
    """Parse a URI into its seven segments."""
    parsed = parse_uri(uri)
    emit(
        {
            "firm": parsed.firm,
            "engagement": parsed.engagement,
            "system": parsed.system,
            "environment": parsed.environment,
            "kind": parsed.kind,
            "namespace": parsed.namespace,
            "key": list(parsed.key),
        },
        as_json=json_output,
        title="adopt identity parse",
    )


@app.command()
def validate(uri: UriArgument, json_output: JsonOption = False) -> None:
    """Check a URI against the grammar and canonical form. Exits 2 if it is not."""
    validate_uri(uri)
    emit({"uri": uri}, as_json=json_output, title="adopt identity validate")
