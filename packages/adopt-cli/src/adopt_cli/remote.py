"""Remote mode: the CLI as another channel, never a second writer.

v6.1 §6 Build 7, F4: once a system is operated, *the plane is the sole writer*
(R9) and the field store is a read replica. Local `adopt ask` and `adopt pack`
keep running against that replica -- reading is what a replica is for -- but
**capture-class writes go through the plane's endpoints**. An FDE answering a
question on their laptop is a person using a channel, exactly as somebody typing
in Slack is, and their answer has to land in the same canon or the two diverge
the moment either is read.

**Consent is the configuration** (sprint plan D-9). There is no
`--allow-network` here and there deliberately is not: the whole purpose of these
two verbs in remote mode is to reach one configured host, so configuring that
host *is* the decision, and its absence is a typed refusal rather than a quiet
local write. That is the probe allow-list's pattern rather than the model seam's
-- a probe reaches hosts a manifest names, and a model call reaches a vendor an
operator opted into per invocation.

**The transport is `urllib`, from the standard library.** `adopt_agent.adapters.
_wire` and `adopt_probe.runner` already speak raw HTTP this way, and a new HTTP
distribution in the Apache-2.0 tree needs a justification nothing here supplies.
The module lives in `adopt_cli` rather than in a package `probe-io` constrains,
so that contract stays exactly as true as it was: it names `adopt_probe` as its
source, and `adopt_probe` still holds one socket.

**Nothing here decides anything about an answer.** It posts a body and hands
back what came out, translating a non-2xx into the plane's own typed error --
the contracts §13 envelope both surfaces already speak, so an operator reads one
error vocabulary whether the refusal came from their own store or from ours.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final

from adopt_const import PULL_TIMEOUT_SECONDS, REMOTE_CHANNEL_TIMEOUT_SECONDS
from adopt_obs import AdoptError, ErrorCode, get_logger

__all__ = [
    "PLANE_SYSTEM_KEY",
    "PLANE_TOKEN_ENV_KEY",
    "PLANE_URL_KEY",
    "Remote",
    "fetch_bytes",
    "get_json",
    "post_json",
    "resolve_remote",
]

_log = get_logger(__name__)

PLANE_URL_KEY: Final[str] = "ADOPT_PLANE_URL"
PLANE_SYSTEM_KEY: Final[str] = "ADOPT_PLANE_SYSTEM"
# ruff's S105 fires on the *name*, not the value: this is the name of the config
# key that names the environment variable, and holds no secret at any remove.
# That indirection is the point -- see the key's registry entry.
PLANE_TOKEN_ENV_KEY: Final[str] = "ADOPT_PLANE_TOKEN_ENV"  # noqa: S105

_HINT: Final[str] = (
    "Set ADOPT_PLANE_URL and ADOPT_PLANE_SYSTEM (in `.adopt/config.toml`, or in the "
    "environment) and put the engagement token in the variable ADOPT_PLANE_TOKEN_ENV "
    "names. Configuring a remote is the consent: with none configured this command "
    "refuses rather than writing into a local store that is a read replica, because a "
    "capture that lands only on your laptop is one the next asker never gets."
)


@dataclass(frozen=True, slots=True)
class Remote:
    """A configured control plane, and the system this store replicates."""

    url: str
    system_id: str
    token: str

    def endpoint(self, path: str) -> str:
        """`path` under the configured base. Slashes normalised once, here.

        A base URL with a trailing slash and a path with a leading one produce
        `//v1/...`, which some proxies route and some 404 -- a failure that
        depends on the deployment rather than on anything the operator did.
        """
        return f"{self.url.rstrip('/')}/{path.lstrip('/')}"


def resolve_remote(config: dict[str, str | None], token: str | None) -> Remote:
    """The configured remote, or a typed refusal.

    Args:
        config: Resolved configuration -- key to value, as `doctor` reports it.
        token: The engagement token, already read from the environment variable
            `ADOPT_PLANE_TOKEN_ENV` names. **Read by the caller**, because this
            module has no business touching `os.environ` for a secret: the
            config layer is the one place that knows how a secret is located,
            and a second reader is a second place to leak one.

    Raises:
        AdoptError: ``PLANE_REMOTE_NOT_CONFIGURED`` when any of the three is
            missing. **All three, not one**: a URL with no system id posts to
            an endpoint that cannot exist, and a URL with no token gets a `401`
            that reads like a broken plane rather than a missing credential.
    """
    url = (config.get(PLANE_URL_KEY) or "").strip()
    system_id = (config.get(PLANE_SYSTEM_KEY) or "").strip()
    missing = [
        name
        for name, value in ((PLANE_URL_KEY, url), (PLANE_SYSTEM_KEY, system_id), ("token", token))
        if not value
    ]
    if missing:
        raise AdoptError(
            ErrorCode.PLANE_REMOTE_NOT_CONFIGURED,
            message=f"no control plane is configured: {', '.join(missing)} is unset",
            hint=_HINT,
        )
    # `token` is non-empty here, which the comprehension above proved; the
    # narrowing is spelled out because `mypy` cannot read a list of names.
    return Remote(url=url, system_id=system_id, token=token or "")


def post_json(
    remote: Remote,
    path: str,
    body: dict[str, Any],
    *,
    timeout: int = REMOTE_CHANNEL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST `body` to `path` and return the decoded response.

    Args:
        timeout: Seconds to wait. Defaults to the interactive budget, which is
            right for the channel verbs an FDE waits at a terminal for.
            `adopt ci-sense` passes `CI_SENSE_TIMEOUT_SECONDS` instead: it posts
            every identity in a repository against a server budget of
            `INGEST_P95_SECONDS`, and a client that gave up first would report a
            failure for work the plane then completed anyway.

    Raises:
        AdoptError: The plane's own typed error, rebuilt from the contracts §13
            envelope it returned -- so `ESCALATION_ALREADY_ANSWERED` from the
            plane exits `3` on the operator's machine exactly as it would from
            their own store. A response this module cannot parse becomes
            ``PLANE_REMOTE_NOT_CONFIGURED``'s sibling condition: an
            ``ADOPT_OFFLINE_DENIED``-style transport failure, reported with the
            URL and never with the token.
    """
    request = urllib.request.Request(  # noqa: S310 -- the URL is the operator's own configuration
        remote.endpoint(path),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {remote.token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- as above
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as failure:
        raise _from_response(failure.read(), failure.code) from failure
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        # **The URL, never the token.** A transport failure is the one place an
        # operator most wants to see what was called, and the one place a
        # careless format string puts a credential into a terminal scrollback.
        _log.warn("remote_unreachable", url=remote.url)
        raise AdoptError(
            ErrorCode.ADOPT_OFFLINE_DENIED,
            message=f"the control plane at {remote.url} could not be reached: {failure}",
            hint="Check the URL and that the plane is running. Reading still works: "
            "`adopt ask` answers from the local replica, which is what a replica "
            "is for. Only capture-class writes need the plane.",
        ) from failure
    if not isinstance(payload, dict):
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=f"the control plane at {remote.url} returned a non-object response",
            hint="Every plane endpoint answers with a JSON object. A body of another "
            "shape means something other than the plane answered -- a proxy, a "
            "captive portal, or the wrong URL.",
        )
    return payload


def get_json(
    remote: Remote, path: str, *, timeout: int = REMOTE_CHANNEL_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """GET `path` and return the decoded response. `post_json`'s read sibling.

    Separate from `fetch_bytes` rather than a flag on it, because the two differ
    in what they return and why: that one hands back bytes and headers for a
    bundle a caller writes to disk, and this one hands back the object a listing
    renders. A single function returning both would make every caller unpack a
    tuple it does not want.

    Raises:
        AdoptError: The plane's own typed error, rebuilt from the contracts 13
            envelope, exactly as `post_json` does -- so a refusal reads the same
            whichever verb met it. A transport failure becomes
            ``ADOPT_OFFLINE_DENIED`` naming the URL and never the token.
    """
    request = urllib.request.Request(  # noqa: S310 -- the URL is the operator's own configuration
        remote.endpoint(path),
        headers={"Authorization": f"Bearer {remote.token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- as above
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as failure:
        raise _from_response(failure.read(), failure.code) from failure
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        _log.warn("remote_unreachable", url=remote.url)
        raise AdoptError(
            ErrorCode.ADOPT_OFFLINE_DENIED,
            message=f"the control plane at {remote.url} could not be reached: {failure}",
            hint="Check the URL and that the plane is running. The replica beside you "
            "still answers `adopt ask` -- only the operated queue lives on the plane.",
        ) from failure
    if not isinstance(payload, dict):
        raise AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=f"the control plane at {remote.url} returned a non-object response",
            hint="Every plane endpoint answers with a JSON object. A body of another "
            "shape means something other than the plane answered -- a proxy, a "
            "captive portal, or the wrong URL.",
        )
    return payload


def fetch_bytes(remote: Remote, path: str) -> tuple[bytes, dict[str, str]]:
    """GET `path` and return its body and response headers.

    The sibling of `post_json`, and separate rather than a `method` argument on
    it, because the two differ in everything after the URL: this one carries no
    request body, decodes nothing, and returns headers -- `adopt pull` needs
    `X-Adopt-Bundle-Sha256` to record what it pulled, and a function that threw
    the headers away would make the replica marker unwritable.

    **Read whole rather than streamed to disk.** A bundle is bounded by what an
    engagement's canon weighs, and the caller writes it to a temporary file
    immediately; streaming would buy nothing here and would mean this module
    knowing about paths, which is the one thing keeping it a transport.

    Raises:
        AdoptError: The plane's own typed error, rebuilt from the contracts §13
            envelope, exactly as `post_json` does -- so a `401` from the plane
            exits `3` on the operator's machine with the plane's own message. A
            transport failure becomes ``ADOPT_OFFLINE_DENIED`` naming the URL
            and never the token.
    """
    request = urllib.request.Request(  # noqa: S310 -- the URL is the operator's own configuration
        remote.endpoint(path),
        headers={"Authorization": f"Bearer {remote.token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 -- as above
            request, timeout=PULL_TIMEOUT_SECONDS
        ) as response:
            body = bytes(response.read())
            headers = {str(name).lower(): str(value) for name, value in response.headers.items()}
    except urllib.error.HTTPError as failure:
        raise _from_response(failure.read(), failure.code) from failure
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        _log.warn("remote_unreachable", url=remote.url)
        raise AdoptError(
            ErrorCode.ADOPT_OFFLINE_DENIED,
            message=f"the control plane at {remote.url} could not be reached: {failure}",
            hint="Check the URL and that the plane is running. The replica beside you "
            "is untouched and still answers `adopt ask` -- a pull that could not "
            "start has cost you nothing but freshness.",
        ) from failure
    return body, headers


def _from_response(raw: bytes, status: int) -> AdoptError:
    """Rebuild the plane's typed error from its contracts §13 envelope.

    **The plane's code, not a translated one.** Both surfaces speak the same
    registry, so an operator who has read `adopt`'s errors does not have to learn
    a second vocabulary for the same refusals -- and a code invented here would
    exit with a category the plane never chose.

    A body that is not an envelope -- a proxy's HTML error page, an empty
    response -- becomes `MANIFEST_INVALID` with the status named, because
    "something on the way answered" is a different fact from "the plane refused"
    and sends an operator somewhere else entirely.
    """
    try:
        decoded = json.loads(raw.decode("utf-8"))
        error = decoded["error"]
        code = ErrorCode(error["code"])
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
        return AdoptError(
            ErrorCode.MANIFEST_INVALID,
            message=f"the control plane answered {status} with no typed error",
            hint="A response that is not the contracts 13 envelope usually means "
            "something between the CLI and the plane answered -- a proxy, a load "
            "balancer, or the wrong URL.",
        )
    return AdoptError(
        code,
        message=str(error.get("message") or f"the control plane refused with {code}"),
        hint=str(error.get("hint") or ""),
    )
