"""`adopt review` against an operated plane. The CLI as a channel, never a writer.

v6.1 §6 Build 8, F4: once a system is operated the plane is the sole writer (R9)
and the field store is a read replica. A reviewer working the queue on their
laptop is a person using a channel, exactly as somebody clicking a button in
Slack is, and their resolution has to land in the same canon or the two diverge
the moment either is read.

**Absence is the local mode, not a refusal.** A store with no remote configured
is a field store and every Build 2/4/6 path through `adopt review` is exactly as
it was. `configured_remote` returns `None` and the caller does what it always
did; the refusal lives one level down and fires only when a remote is *partly*
configured -- an operator who meant to route and would otherwise watch their
resolution land where nobody else can read it.

**The resolver is named or the command refuses.** The plane requires an actor on
every resolution because a disposition is the record that *a person looked*, and
the token names the integration rather than the human. Defaulting it to the
token, the hostname or the OS user would put a plausible name on a decision
nobody made -- so `--actor` is required here and its absence is a refusal before
anything is sent.
"""

from typing import Any, Final

from adopt_cli.remote import Remote, get_json, post_json

__all__ = ["REMOTE_ACTIONS", "remote_queue", "resolve_remote_item"]

#: The six the plane accepts, in the order its own refusal lists them. Spelled
#: here rather than imported because `plane_freshness` is proprietary and this
#: file is Apache-2.0: the vocabulary travels as a wire value, and a CLI that
#: had to import the plane to know it would be a licence boundary violation
#: dressed as a constant.
#:
#: **Not validated against here.** The plane refuses an unknown action with a
#: typed error the CLI already renders, and a second list in this repository
#: would start rejecting an action the plane had learned -- a client-side
#: allow-list that goes stale is worse than no allow-list at all.
REMOTE_ACTIONS: Final[tuple[str, ...]] = (
    "retire",
    "rebind",
    "confirm-current",
    "confirm",
    "edit",
    "reject",
)


def remote_queue(remote: Remote) -> dict[str, Any]:
    """The plane's open review queue for the configured system.

    Returned as the plane rendered it. **Nothing is re-shaped here**: the causes,
    the drafted bodies and the blast-radius order are the plane's answers, and a
    CLI that re-sorted or re-summarised them would be a second opinion about a
    queue it cannot see the rest of.
    """
    return get_json(remote, f"/v1/systems/{remote.system_id}/reviews")


def resolve_remote_item(
    remote: Remote,
    *,
    review_item_id: str,
    action: str,
    actor: str,
    body_md: str | None = None,
    to_uri: str | None = None,
) -> dict[str, Any]:
    """POST one resolution and return the plane's own outcome payload.

    Optional fields are omitted rather than sent as `null`: the request model
    forbids unknown keys and the plane refuses a body on an action that does not
    take one, so sending `body_md: null` on a confirm would be a value travelling
    only to be checked for absence.
    """
    body: dict[str, Any] = {
        "action": action,
        "actor": actor,
        "system_id": remote.system_id,
    }
    if body_md is not None:
        body["body_md"] = body_md
    if to_uri is not None:
        body["to"] = to_uri
    return post_json(remote, f"/v1/reviews/{review_item_id}/resolve", body)
