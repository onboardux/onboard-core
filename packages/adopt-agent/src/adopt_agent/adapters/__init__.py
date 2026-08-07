"""The only package permitted to import a model-provider client SDK.

Adapters translate; they never enforce policy (AI spec §1, §2). Everything hard
-- budget metering, the single output-schema retry, idempotency, tracing -- is
the seam's, which is what keeps an adapter thin enough to be written twice.

**This module deliberately imports no adapter.** `base.REGISTRY` maps an id to a
module path and `build_adapter` imports it on demand, for two reasons that both
matter:

1. **`adopt_store` depends on `adopt_agent`** -- it realizes the runtime-annex
   port (contracts §12, CR-45). If importing `adopt_agent` pulled every adapter
   in, `adopt_store` would transitively reach a provider client and
   `no-provider-sdk` would break -- correctly, because it would then be true.
2. **Offline must refuse before the import**, not merely before the request.
   F13.7 says a hosted adapter raises "before any socket opens"; refusing
   before the module loads is the stronger claim, and it is what lets S6's
   blocked-socket harness prove it rather than argue it from reading code.

`no-provider-sdk` still sees every import edge statically -- the contract is
about *where* a provider client may be imported, and the answer is here.

**At S7 no adapter imports a vendor SDK at all** *(CR-46)*. The hosted adapters
speak HTTP over the standard library, because `03` §7.3 forbids copyleft
`in-binary` and both official SDKs drag `certifi` (MPL-2.0) into the wheel. The
contract stays declared and preventive: it fires the moment anyone adds one.
"""

__all__: list[str] = []
