"""The adapter registry and the offline gate. AI spec §2.

**The offline gate runs before the import, not before the request.** F13.7 says a
hosted adapter under offline mode raises "before any socket opens", and the
strongest available reading is stronger than that: this module refuses *before
the adapter module is even imported*, so no provider code loads, no client is
constructed and no connection pool exists. That also keeps `adopt` cold-start
inside `CLI_COLD_START_MS` -- nothing provider-shaped is imported by `adopt
version`.

**Adapters are resolved by name through a lazy import.** The registry maps an id
to a module path and `build_adapter` imports it on demand. `no-provider-sdk`
still sees every import edge statically, so the contract loses nothing: the
rule is about *where* a provider client may be imported, and every one of them
is under `adopt_agent.adapters`.

**There is no fallback.** AI spec §2: if the configured adapter is unavailable
the seam raises. A silent substitution changes cost, behaviour and data
residency without the operator knowing, and in a client environment that is a
security-review finding rather than a convenience.

**No model identifier appears anywhere in this package outside `prices.json`.**
`ADOPT_MODEL` names the model and an adapter fails fast with
`AGENT_ADAPTER_UNKNOWN` when it is absent. A hard-coded default is how a tool
aimed at every lab acquires a house lab.
"""

import importlib
from typing import Final, Protocol

from adopt_agent.api import Adapter, AdapterInfo, AdapterKind
from adopt_obs import AdoptError, ErrorCode

__all__ = ["REGISTRY", "AdapterFactory", "build_adapter", "describe_adapters"]


class AdapterFactory(Protocol):
    """What every adapter module exposes as `build`."""

    def __call__(self, *, model: str | None, endpoint: str | None) -> Adapter: ...


class _Entry:
    """One registry row: the kind, and where the code lives."""

    def __init__(self, kind: AdapterKind, module: str, *, needs_endpoint: bool = False) -> None:
        self.kind = kind
        self.module = module
        self.needs_endpoint = needs_endpoint


#: AI spec §2's registry, verbatim in its ids and kinds.
#:
#: `fake_recorded` is `test`, **not** `local`. That typing is load-bearing: the
#: `conformance-matrix` gate requires at least one *local* adapter green, and if
#: the recorded fake counted as local then a pipeline exercising no real model at
#: all would satisfy the gate whose entire purpose is to prove one was exercised.
REGISTRY: Final[dict[str, _Entry]] = {
    "anthropic": _Entry("hosted", "adopt_agent.adapters.anthropic"),
    "openai": _Entry("hosted", "adopt_agent.adapters.openai"),
    "local_openai": _Entry("local", "adopt_agent.adapters.local_openai", needs_endpoint=True),
    "fake_recorded": _Entry("test", "adopt_agent.adapters.fake_recorded"),
}


def _unknown(adapter_id: str) -> AdoptError:
    return AdoptError(
        ErrorCode.AGENT_ADAPTER_UNKNOWN,
        message=f"no adapter is registered as {adapter_id!r}",
        hint=f"Registered adapters: {', '.join(sorted(REGISTRY))}.",
    )


def _offline_denied(adapter_id: str) -> AdoptError:
    return AdoptError(
        ErrorCode.AGENT_OFFLINE_ADAPTER_DENIED,
        message=f"{adapter_id!r} is a hosted adapter and this process is offline",
        hint=(
            "Offline is the default (contracts §1.7). Pass --allow-network, or use "
            "the local adapter, which is always available offline. Nothing was sent: "
            "the refusal happens before the adapter module is imported."
        ),
    )


def _unavailable_reason(
    entry: _Entry, *, offline: bool, model: str | None, endpoint: str | None
) -> str | None:
    """Why this adapter cannot be used right now, or `None` if it can.

    Three different causes with three different fixes -- an offline denial, a
    missing endpoint and an unset model -- so `AdapterInfo.reason` says which.
    Reporting a bare `available: false` would leave an operator guessing.
    """
    if offline and entry.kind == "hosted":
        return "hosted adapter denied under offline mode (ADOPT_OFFLINE)"
    if entry.needs_endpoint and not endpoint:
        return "ADOPT_ADAPTER_ENDPOINT is not set"
    if entry.kind != "test" and not model:
        return "ADOPT_MODEL is not set; the seam never picks a model for you"
    return None


def describe_adapters(
    *, offline: bool, model: str | None = None, endpoint: str | None = None
) -> list[AdapterInfo]:
    """Contracts §14's `adapters[{id, kind, available, reason}]`, unshaped.

    Reports every registered adapter rather than only the usable ones: an
    operator asking "why can I not use Anthropic" needs the row that says so.
    """
    infos: list[AdapterInfo] = []
    for adapter_id in sorted(REGISTRY):
        entry = REGISTRY[adapter_id]
        reason = _unavailable_reason(entry, offline=offline, model=model, endpoint=endpoint)
        infos.append(
            AdapterInfo(id=adapter_id, kind=entry.kind, available=reason is None, reason=reason)
        )
    return infos


def build_adapter(
    adapter_id: str, *, offline: bool, model: str | None = None, endpoint: str | None = None
) -> Adapter:
    """Resolve and construct one adapter, or raise.

    The offline check is the **first** thing that happens after the id is
    recognised, and it happens before `import_module`. That ordering is what
    makes "no socket opened" provable by the blocked-socket harness rather than
    argued from reading the adapter's code.
    """
    entry = REGISTRY.get(adapter_id)
    if entry is None:
        raise _unknown(adapter_id)
    if offline and entry.kind == "hosted":
        raise _offline_denied(adapter_id)

    module = importlib.import_module(entry.module)
    factory: AdapterFactory = module.build
    return factory(model=model, endpoint=endpoint)
