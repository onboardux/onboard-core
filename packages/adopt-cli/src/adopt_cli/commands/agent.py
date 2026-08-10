"""`adopt agent adapters | check` -- contracts §14.

**A coverage gap, closed here.** `02` §14 defines this command group and `05` S7's
Final Output Validation item 4 runs it, and **no sprint task ever created it** --
the behaviour underneath was built and asserted at S7 while nothing exposed it on
a command line. Recorded as a gap rather than folded in silently, exactly as S8
did for `adopt store info` and S6 for `adopt init`.

Both subcommands emit the one envelope §14 declares -- `{adapters[{id, kind,
available, reason}]}` -- and that shape is `AdapterInfo` (CR-44), rendered rather
than re-shaped. A group whose subcommands each invented a payload would make one
contract into two.

**`adapters` reports and `check` acts, which is why their exit codes differ.**
The same division `02` §14 records for `adopt boundary` against `adopt init`:
`adapters` lists every registered adapter with the reason each unusable one is
unusable and exits `0`, because being told Anthropic is denied offline *is* the
answer. `check` attempts construction, so an offline hosted adapter is a policy
refusal and exits `3` -- and nothing was sent, because the refusal happens before
the adapter module is imported.

**No store is opened and no annex is touched.** Neither subcommand runs a model,
so neither needs the runtime annex; `adopt_agent.adapters.base` is reached
directly. That keeps `adopt agent check` usable before there is a store at all,
which is when an operator is configuring credentials.
"""

from pathlib import Path
from typing import Annotated, Any

import typer

from adopt_agent import AdapterInfo
from adopt_agent.adapters.base import build_adapter, describe_adapters
from adopt_cli.config import resolve_all
from adopt_cli.json_out import emit
from adopt_obs import AdoptError, ErrorCode

__all__ = [
    "adapter_settings",
    "app",
    "build_payload",
    "disambiguation_enabled",
    "prompts_root",
]

app = typer.Typer(
    name="agent",
    help="Inspect and check model adapters. Offline by default; nothing is sent.",
    no_args_is_help=True,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit the strict JSON envelope only.")]
AdapterOption = Annotated[
    str | None,
    typer.Option("--adapter", help="Adapter id to check. Defaults to the resolved ADOPT_ADAPTER."),
]

#: Values that mean "not offline". Written as a set of spellings rather than a
#: truthiness test because `ADOPT_OFFLINE=false` must not read as "true" merely
#: for being a non-empty string -- the same three spellings `adopt doctor`
#: already accepts, and one home would be better than two if either grows.
_FALSEY: frozenset[str] = frozenset({"0", "false", "no"})


def _allow_network(ctx: typer.Context) -> bool:
    """The root `--allow-network` flag, or `False` when invoked without a context."""
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    return bool(obj.get("allow_network", False))


def adapter_settings(
    *, allow_network: bool = False
) -> tuple[bool, str | None, str | None, str | None]:
    """`(offline, adapter, model, endpoint)` from the §3 config registry.

    Resolved through `resolve_all` so the order is flag > env > project > user >
    default and `adopt doctor` can explain any of them. Defaulting offline to
    **on** when the key is absent is the posture, not a fallback: a seam whose
    default were online would make "offline by default" a property of whoever
    happened to set the variable.

    **`--allow-network` is the flag layer of that order and it was not wired.**
    `main._root` wrote `ctx.obj["allow_network"]` and **nothing anywhere read
    it**, so the root flag `adopt_cli.main`'s own docstring calls the way to
    permit egress did nothing at all -- while `AGENT_OFFLINE_ADAPTER_DENIED`'s
    hint told operators to pass it. A remedy a document names and no code
    honours costs whoever hits it an hour, and the CI preflight hit it first.
    """
    by_key = {resolution.key: resolution.value for resolution in resolve_all()}
    offline = (by_key.get("ADOPT_OFFLINE") or "1").strip().lower() not in _FALSEY
    if allow_network:
        offline = False
    return (
        offline,
        by_key.get("ADOPT_ADAPTER"),
        by_key.get("ADOPT_MODEL"),
        by_key.get("ADOPT_ADAPTER_ENDPOINT"),
    )


def disambiguation_enabled() -> bool:
    """Whether `ADOPT_FEATURE_AGENT_DISAMBIGUATION` is on.

    **Default off, without exception** (`03` §5): new behaviour arrives behind a
    flag that is off until it has earned being on, and this flag is the one that
    decides whether a model is called at all. Read through the registry so
    `adopt doctor` can say where the value came from -- "it made a model call on my
    machine" is a resolution-order question before it is anything else.
    """
    by_key = {resolution.key: resolution.value for resolution in resolve_all()}
    return (by_key.get("ADOPT_FEATURE_AGENT_DISAMBIGUATION") or "0").strip().lower() not in _FALSEY


def prompts_root() -> Path:
    """Where immutable prompt versions live (AI spec §5, `03` §1.2).

    Configuration rather than a computed path: `03` §1.2 places `prompts/` at the
    **repository root**, which is inside no package, so nothing importable can
    derive it. `ADOPT_PROMPTS_DIR` defaults to `prompts` relative to the working
    directory, and `doctor` reports which layer supplied it.
    """
    by_key = {resolution.key: resolution.value for resolution in resolve_all()}
    return Path(by_key.get("ADOPT_PROMPTS_DIR") or "prompts")


def build_payload(infos: list[AdapterInfo]) -> dict[str, Any]:
    """Contracts §14's `{adapters[{id, kind, available, reason}]}`."""
    return {
        "adapters": [
            {
                "id": info.id,
                "kind": info.kind,
                "available": info.available,
                "reason": info.reason,
            }
            for info in infos
        ]
    }


def _no_adapter_named() -> AdoptError:
    """`check` with no id anywhere. A usage error, not a policy refusal.

    The seam raises for the same reason (`04` §2, no fallback): picking one on
    the operator's behalf would change cost, behaviour and data residency
    without them knowing.
    """
    return AdoptError(
        ErrorCode.AGENT_ADAPTER_UNKNOWN,
        message="no adapter was named and ADOPT_ADAPTER has no value",
        hint="Pass --adapter, or set ADOPT_ADAPTER. Run `adopt agent adapters` to "
        "see every registered id and why each unavailable one is unavailable.",
    )


@app.command()
def adapters(ctx: typer.Context, json_output: JsonOption = False) -> None:
    """List every registered adapter and why each unusable one is unusable.

    Exits `0` even when nothing is available: the report is the answer.
    """
    offline, _, model, endpoint = adapter_settings(allow_network=_allow_network(ctx))
    payload = build_payload(describe_adapters(offline=offline, model=model, endpoint=endpoint))
    emit(payload, as_json=json_output, title="adopt agent adapters")


@app.command()
def check(
    ctx: typer.Context, adapter: AdapterOption = None, json_output: JsonOption = False
) -> None:
    """Construct one adapter, or refuse with the reason.

    Raises `AGENT_ADAPTER_UNKNOWN` (usage, exit `2`) for an unregistered id or
    when none is configured, and `AGENT_OFFLINE_ADAPTER_DENIED` (policy, exit
    `3`) for a hosted adapter offline. The envelope and the exit code are
    `adopt_cli.main`'s doing, so this command does not restate the mapping.
    """
    offline, configured, model, endpoint = adapter_settings(allow_network=_allow_network(ctx))
    chosen = adapter or configured
    if chosen is None:
        raise _no_adapter_named()

    # Construction is the check. Asking `describe_adapters` instead would report
    # availability without ever proving the adapter can be built -- and building
    # is where a hosted adapter refuses, before its module is imported.
    built = build_adapter(chosen, offline=offline, model=model, endpoint=endpoint)
    reported = [
        info
        for info in describe_adapters(offline=offline, model=model, endpoint=endpoint)
        if info.id == built.id
    ]
    emit(build_payload(reported), as_json=json_output, title="adopt agent check")
