"""Build 3's third verb: `adopt serve` -- the same answers, on loopback.

The whole of this module is: resolve options, build the callback that opens a
store and answers one question, print the address (and the warning, if one is
owed), serve until interrupted. The HTTP layer is `adopt_ask.serve` and the
pipeline is `_ask_support.answer_question` -- the same function `adopt ask`
calls, which is what makes "the same paths" true rather than asserted.

**A store per request, deliberately.** `ThreadingHTTPServer` answers each request
on its own thread and a `sqlite3` connection belongs to the thread that made it,
so a shared handle would fail wherever it was not created -- intermittently, and
only under the concurrency serve exists to provide. Opening per request costs a
file open on a local SQLite database and buys a server that cannot corrupt or
crash on its second caller.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_cli.store_option import open_configured_store

__all__ = ["serve"]

HostOption = Annotated[
    str | None,
    typer.Option("--host", help="Bind address. Loopback by default; anything else warns."),
]
PortOption = Annotated[int | None, typer.Option("--port", help="Bind port.")]
ScopeOption = Annotated[
    str | None,
    typer.Option("--scope", help="firm/engagement/system/environment. Defaults to the store's."),
]
StoreOption = Annotated[Path | None, typer.Option("--store", help="Store path override.")]


def serve(
    host: HostOption = None,
    port: PortOption = None,
    scope: ScopeOption = None,
    store: StoreOption = None,
) -> None:
    """Answer questions over loopback HTTP: `POST /ask`, `GET /healthz`."""
    from adopt_ask.serve import (
        ASK_ROUTE,
        HEALTH_ROUTE,
        LOOPBACK_HOST,
        build_server,
        exposure_warning,
    )

    from adopt_cli.commands._ask_support import answer_question
    from adopt_const import ASK_SERVE_PORT

    bind_host = host if host is not None else LOOPBACK_HOST
    bind_port = port if port is not None else ASK_SERVE_PORT

    def ask_once(question: str, escalate: bool) -> dict[str, Any]:
        handle = open_configured_store(store, read_only=False)
        try:
            return answer_question(
                handle,
                question,
                scope=scope,
                escalate_flag=escalate,
                # Never interactive. There is no human on the other end of a
                # socket, and `consented` returns False for this path before it
                # consults anything -- so escalation over HTTP is the explicit
                # `escalate` field or nothing.
                interactive=False,
                confirm=None,
            ).payload
        finally:
            handle.close()

    server = build_server(ask_once, host=bind_host, port=bind_port)
    warning = exposure_warning(bind_host, bind_port)
    if warning is not None:
        typer.echo(warning, err=True)
    typer.echo(f"adopt serve on http://{bind_host}:{bind_port}")
    typer.echo(f'  POST {ASK_ROUTE}      {{"question": "...", "escalate": false}}')
    typer.echo(f"  GET  {HEALTH_ROUTE}")
    typer.echo("  The payload shape is the CLI's `--json` shape and is unstable until B7.")
    typer.echo("Ctrl-C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("stopping")
    finally:
        server.server_close()
