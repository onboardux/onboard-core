"""One HTTPS client for every hosted adapter, over the standard library.

**Why the standard library and not a vendor SDK.** Owner decision, 2026-08-06,
recorded as CR-46. `03` §7.3 makes `in-binary` **permissive only, no copyleft,
ever** -- and the transitive closure of both official SDKs includes `certifi`
(MPL-2.0) and `tqdm` (MPL-2.0 AND MIT). The licence gate refused them, correctly.
Rather than amend the policy for two convenience libraries, the adapters talk
HTTP directly: `urllib.request` over `ssl.create_default_context()` uses the
**operating system's** trust store, so there is no CA bundle to vendor and no
copyleft to carry.

That trade is cheap precisely because of how thin an adapter is here. AI spec §1
assigns budget metering, the output-schema retry, idempotency and tracing to the
seam; an adapter translates one request and one response and owns exactly one
thing of its own -- a bounded retry on a *transient* failure, inside
`AGENT_ADAPTER_TIMEOUT_S`, where the seam's meter cannot double-count it.

**Nothing here reads a credential from anywhere but the environment**, and no
credential is logged, traced or returned (`03` §3). The error raised on failure
carries a status code and never a response body, because a provider's error body
can echo the prompt back.
"""

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Final

from adopt_const import AGENT_ADAPTER_TIMEOUT_S
from adopt_obs import AdoptError, ErrorCode

__all__ = ["post_json"]

#: Statuses worth one retry: the provider said "not now", not "not ever".
#: 4xx other than 429 are the caller's fault and retrying them spends money to
#: receive the same refusal.
_RETRYABLE: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})

#: One retry, and only for the statuses above. AI spec §1 puts transient retry
#: with the adapter and bounds it; the seam does not retry at all, because that
#: would double-count cost.
_MAX_ATTEMPTS: Final[int] = 2

_CONTEXT: Final[ssl.SSLContext] = ssl.create_default_context()

#: Hosts a plaintext POST is permitted to. A locally served model on loopback
#: has no network to intercept; anything else carries a prompt in the clear, and
#: AI spec §8 has no exception for convenience.
_LOOPBACK: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


#: The only fields lifted out of a provider's error body. Enumerated identifiers
#: naming a parameter or a failure class -- never free text. `message` is
#: excluded on purpose: a content-policy refusal quotes what triggered it.
_ERROR_IDENTIFIERS: Final[tuple[str, ...]] = ("type", "code", "param")

#: A hard ceiling on each identifier, so a provider that returns something
#: unexpected in one of these fields cannot turn this into a body dump.
# const-sync: ok -- a display cap for an error identifier, not a tunable.
_IDENTIFIER_MAX_CHARS: Final[int] = 64


def _identifiers(exc: urllib.error.HTTPError) -> str:
    """`(type=…, param=…)` from a provider error body, or `""`.

    Reading the body cannot itself fail the request: a provider under load
    returns HTML, and an error path that raises while explaining an error is
    strictly worse than one that says less.
    """
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
    except Exception:  # diagnosis is best-effort: an error path must not raise
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    parts = [
        f"{field}={str(error[field])[:_IDENTIFIER_MAX_CHARS]}"
        for field in _ERROR_IDENTIFIERS
        if error.get(field)
    ]
    return f" ({', '.join(parts)})" if parts else ""


def post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], *, adapter_id: str
) -> dict[str, Any]:
    """POST JSON and return the decoded object, or raise `AGENT_PROVIDER_ERROR`.

    The body is never included in the raised message. A provider's error payload
    routinely echoes the request back, and AI spec §8.3 says prompt text is not
    retrievable from our artifacts -- an exception message is one of ours.

    **Three identifier fields are the exception, and they are an allowlist rather
    than a relaxation** *(CR-52)*. `HTTP 400` alone is undiagnosable: it says a
    request was malformed and not which part, so the first real-model run left
    "the request shape is wrong somewhere" as the whole finding, and the privacy
    rule that produced that message is not one to soften. `error.type`,
    `error.code` and `error.param` are **enumerated identifiers naming a
    parameter or a failure class** -- `unsupported_parameter`, `temperature` --
    and cannot carry prompt text, because they are not free-text fields.
    `error.message` is deliberately **excluded**: it is free text, and a
    content-policy refusal quotes the content that triggered it.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 -- scheme checked immediately below
        url, data=body, headers={**headers, "content-type": "application/json"}, method="POST"
    )
    if request.type != "https" and request.host.split(":")[0] not in _LOOPBACK:
        raise AdoptError(
            ErrorCode.AGENT_PROVIDER_ERROR,
            message=f"{adapter_id} refused a plaintext endpoint that is not loopback",
            hint=(
                "Plaintext is permitted only to localhost, where there is no network "
                "to intercept. Anything else carries a prompt across a wire in the "
                "clear, and AI spec §8 does not have an exception for convenience."
            ),
        )

    last: str = "no attempt was made"
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(  # noqa: S310 -- scheme checked above
                request, timeout=AGENT_ADAPTER_TIMEOUT_S, context=_CONTEXT
            ) as response:
                decoded: dict[str, Any] = json.loads(response.read().decode("utf-8"))
                return decoded
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}{_identifiers(exc)}"
            if exc.code not in _RETRYABLE or attempt == _MAX_ATTEMPTS - 1:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last = f"{type(exc).__name__}"
            if attempt == _MAX_ATTEMPTS - 1:
                break
        except json.JSONDecodeError:
            last = "the provider returned a body that is not JSON"
            break

    raise AdoptError(
        ErrorCode.AGENT_PROVIDER_ERROR,
        message=f"{adapter_id} failed after {_MAX_ATTEMPTS} attempt(s): {last}",
        hint=(
            "The seam does not retry beyond the adapter's own bounded attempts, and "
            "it does not fall back to another adapter -- a silent substitution changes "
            "cost, behaviour and data residency without the operator knowing."
        ),
    )
