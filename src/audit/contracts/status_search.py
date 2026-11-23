"""User status, global search, notifications, and activity."""

from __future__ import annotations

import base64
import datetime as dt
import os

from toon_mcp import toon_to_json

from ..expect import find_item, require
from ..harness import Scenario, scenario

try:
    API_USER = base64.b64decode(os.getenv("API_KEY", "")).decode().split(":", 1)[0]
except Exception:
    API_USER = os.getenv("NEXTCLOUD_USER", "")
CLEAR_AT = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()


def _results(call) -> list[dict]:
    raw = call.data.get("results")
    if isinstance(raw, str):
        raw = toon_to_json(raw)
    require(isinstance(raw, list), f"results is not a list: {raw!r}")
    return raw


@scenario(
    "status_search",
    "user_status_lifecycle",
    [
        "get_user_status", "set_status", "set_predefined_message",
        "set_custom_message", "clear_message", "list_predefined_statuses",
        "list_user_statuses",
    ],
)
def user_status_lifecycle(s: Scenario) -> None:
    current = s.call("get status", "get_user_status", {})
    status = current.data
    original_status = status.get("status") or "online"
    original_message = status.get("message")
    original_icon = status.get("icon") or "💬"
    s.check("get_user_status returns a status",
            lambda: require(bool(status.get("userId")) and isinstance(
                status.get("status"), str), status))

    predefined = s.call("list predefined statuses", "list_predefined_statuses", {})
    options = s.items("predefined statuses return items", predefined)
    s.check("predefined statuses are objects with id/message",
            lambda: require(
                bool(options) and all(
                    item.get("id") and item.get("message") for item in options
                ),
                options[:3],
            ))

    listed = s.call("list user statuses", "list_user_statuses", {"limit": 100})
    statuses = s.items("list_user_statuses returns items", listed)
    s.check("list_user_statuses includes the current user",
            lambda: require(
                any(item.get("userId") == API_USER for item in statuses),
                [item.get("userId") for item in statuses][:10],
            ))

    if original_message and status.get("clearAt"):
        s.skip("status mutation",
               f"existing status message has an expiry and is preserved: {status}")
        return

    # Preserve and restore the existing status/message while exercising the
    # full mutation surface.
    s.cleanup("set_status", {"status_type": original_status},
              "restore original status")
    if original_message:
        s.cleanup("set_custom_message",
                  {"message": original_message, "status_icon": original_icon},
                  "restore original status message")
    else:
        s.cleanup("clear_message", {}, "clear status message")
    s.call("clear existing status message", "clear_message", {})

    s.call("set status away", "set_status", {"status_type": "away"})
    got = s.call("get away status", "get_user_status", {})
    s.check("set_status persists the new status",
            lambda: require(got.data.get("status") == "away", got.data))

    s.call("set predefined message", "set_predefined_message",
           {"message_id": "vacationing", "clear_at": CLEAR_AT})
    got = s.call("get predefined message", "get_user_status", {})
    s.check("predefined message is visible",
            lambda: require(
                got.data.get("messageId") == "vacationing"
                and bool(got.data.get("message")),
                got.data,
            ))

    s.call("set custom message", "set_custom_message",
           {"message": "Audit status", "status_icon": "😀",
            "clear_at": CLEAR_AT})
    got = s.call("get custom message", "get_user_status", {})
    s.check("custom message is visible with its icon",
            lambda: require(
                got.data.get("message") == "Audit status"
                and got.data.get("icon") == "😀",
                got.data,
            ))

    s.call("clear status message", "clear_message", {})
    got = s.call("get cleared status", "get_user_status", {})
    s.check("clear_message removes the message",
            lambda: require(
                not got.data.get("message")
                and not got.data.get("messageId"),
                got.data,
            ))


@scenario(
    "status_search",
    "global_search",
    ["list_search_providers", "search"],
)
def global_search(s: Scenario) -> None:
    providers_call = s.call("list search providers", "list_search_providers", {})
    providers = s.items("search providers return items", providers_call)
    s.check("search providers include files",
            lambda: require(
                any(item.get("id") == "files" for item in providers),
                [item.get("id") for item in providers],
            ))

    files = s.call("search files provider", "search",
                   {"provider_id": "files", "term": "Photos", "limit": 10})
    results = s.check("file search results decode", lambda: _results(files)) or []
    s.check("file search returns matching entries",
            lambda: require(
                any("photo" in str(item.get("title", "")).casefold()
                    for item in results),
                results[:5],
            ))
    s.check("search results are objects with title and resource URL",
            lambda: require(
                all(item.get("title") and item.get("resourceUrl")
                    for item in results),
                results[:3],
            ))

    s.expect_error("unknown search provider is rejected", "search",
                   {"provider_id": "mcp-audit-no-such-provider",
                    "term": "x"},
                   contains=("404", "not found", "provider"))


@scenario(
    "status_search",
    "notifications",
    [
        "list_notifications", "create_notification", "get_notification",
        "delete_notification", "delete_all_notifications",
    ],
)
def notifications(s: Scenario) -> None:
    before = s.call("list notifications before", "list_notifications", {})
    before_items = s.items("notifications before returns items", before)
    created = []

    for index in (1, 2):
        subject = f"{s.prefix}-notification-{index}"
        call = s.call(f"create notification {index}", "create_notification",
                      {"user_id": API_USER, "subject": subject,
                       "message": "audit notification"})
        notification_id = call.data.get("id") or call.data.get("notification_id")
        s.check(f"created notification {index} has an id",
                lambda nid=notification_id: require(nid is not None, call.data))
        created.append((notification_id, subject))

    listed = s.call("list notifications", "list_notifications", {})
    items = s.items("notifications return items", listed)
    for notification_id, subject in created:
        s.check(f"notification {subject} is listed",
                lambda nid=notification_id, subj=subject: require(
                    any(str(item.get("id") or item.get("notification_id")) == str(nid)
                        for item in items),
                    f"ids: {[item.get('id') or item.get('notification_id') for item in items]}",
                ))

    first_id, first_subject = created[0]
    got = s.call("get notification", "get_notification", {"id": first_id})
    s.check("get_notification returns the created subject",
            lambda: require(got.data.get("subject") == first_subject, got.data))

    s.call("delete notification", "delete_notification", {"id": first_id})
    listed = s.call("list notifications after delete", "list_notifications", {})
    items = s.items("notifications after delete return items", listed)
    s.check("deleted notification is gone",
            lambda: require(
                not any(str(item.get("id") or item.get("notification_id")) == str(first_id)
                        for item in items),
                [item.get("id") or item.get("notification_id") for item in items],
            ))

    if before_items:
        s.skip("delete_all_notifications",
               f"{len(before_items)} pre-existing notification(s) would be deleted")
        s.call("delete second notification", "delete_notification",
               {"id": created[1][0]})
    else:
        s.call("delete all notifications", "delete_all_notifications", {})
        listed = s.call("list notifications after delete all", "list_notifications", {})
        items = s.items("notifications after delete all return items", listed)
        s.check("delete_all_notifications empties the list",
                lambda: require(items == [], items))


@scenario(
    "status_search",
    "activity",
    ["list_activities", "list_activity_filters", "list_activity_filtered"],
)
def activity(s: Scenario) -> None:
    name = f"{s.prefix}-activity.txt"
    s.call("create activity file", "write_file",
           {"path": name, "content": "activity"})
    s.cleanup("delete_file", {"path": name}, "cleanup activity file")

    activities = s.call("list activities", "list_activities", {"limit": 20})
    items = s.items("list_activities returns items", activities)
    s.check("activity records the file creation",
            lambda: require(
                any(name in str(item.get("subject", "")) for item in items),
                [item.get("subject") for item in items][:10],
            ))
    s.check("activity entries have ids and ISO timestamps",
            lambda: require(
                all(item.get("activity_id") is not None
                    and item.get("datetime") for item in items),
                items[:3],
            ))

    filters = s.call("list activity filters", "list_activity_filters", {})
    filter_items = s.items("activity filters return items", filters)
    s.check("activity filters include the all filter",
            lambda: require(
                any(item.get("id") == "all" for item in filter_items),
                [item.get("id") for item in filter_items],
            ))

    filtered = s.call("list filtered activities", "list_activity_filtered",
                      {"filter_name": "all", "limit": 20})
    filtered_items = s.items("filtered activities return items", filtered)
    s.check("filtered activity includes the file creation",
            lambda: require(
                any(name in str(item.get("subject", "")) for item in filtered_items),
                [item.get("subject") for item in filtered_items][:10],
            ))
