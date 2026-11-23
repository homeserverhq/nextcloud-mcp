"""System status and user/group directory contracts."""

from __future__ import annotations

from ..expect import find_item, require
from ..harness import Scenario, scenario


def _as_id(item):
    return item.get("id") if isinstance(item, dict) else item


@scenario(
    "system_users",
    "status_and_capabilities",
    ["check_server_status", "get_capabilities"],
)
def status_and_capabilities(s: Scenario) -> None:
    status_call = s.call("check_server_status", "check_server_status")
    status = status_call.data
    s.check("status is connected",
            lambda: require(status.get("status") == "connected", status))
    capabilities = status.get("data") or {}
    s.check("status includes capabilities",
            lambda: require(bool(capabilities), "no capabilities in status"))

    caps_call = s.call("get_capabilities", "get_capabilities")
    caps = caps_call.data
    s.check("capabilities include core/files/dav",
            lambda: require(
                all(key in caps for key in ("core", "files", "dav")),
                f"missing keys: {sorted(caps)}",
            ))
    s.check("capabilities expose the user timezone",
            lambda: require(
                isinstance(((caps.get("core") or {}).get("user") or {}).get("timezone"), str),
                "core.user.timezone missing or not a string",
            ))


@scenario(
    "system_users",
    "users_groups_members",
    ["list_users", "get_user", "list_groups", "list_group_members"],
)
def users_groups_members(s: Scenario) -> None:
    users_call = s.call("list_users", "list_users", {"limit": 3})
    users = s.items("list_users returns items", users_call)
    s.check("list_users respects limit",
            lambda: require(len(users) <= 3, f"got {len(users)} users"))
    s.check("list_users returns user objects, not bare ids",
            lambda: require(
                all(isinstance(item, dict) for item in users),
                f"got {[type(item).__name__ for item in users][:5]}",
            ))
    if all(isinstance(item, dict) for item in users):
        s.check("list_users items expose id and display name",
                lambda: require(
                    all(
                        item.get("id")
                        and (item.get("display-name") or item.get("displayname"))
                        for item in users
                    ),
                    f"first user: {users[0] if users else None}",
                ))

    search_call = s.call("list_users search solo", "list_users",
                         {"search": "solo", "limit": 10})
    search_items = s.items("search returns items", search_call)
    s.check("search finds exactly solo",
            lambda: require(
                [str(_as_id(item)) for item in search_items] == ["solo"],
                f"got {[_as_id(item) for item in search_items]}",
            ))

    empty_call = s.call("list_users search no match", "list_users",
                        {"search": "mcp-audit-no-such-user"})
    empty_items = s.items("no-match search returns items", empty_call)
    s.check("no-match search returns an empty list",
            lambda: require(empty_items == [], f"got {empty_items}"))

    solo_call = s.call("get_user solo", "get_user", {"user_id": "solo"})
    solo = solo_call.data
    s.check("get_user returns the requested user",
            lambda: require(solo.get("id") == "solo", solo))
    s.check("get_user includes display name and email",
            lambda: require(
                (solo.get("display-name") or solo.get("displayname")) == "Han Solo"
                and solo.get("email") == "solo@selfhostingbox.com",
                solo,
            ))
    s.check("get_user includes group membership",
            lambda: require("primaryusers" in (solo.get("groups") or []), solo))
    s.check("get_user reports the account enabled",
            lambda: require(solo.get("enabled") is True, solo))

    missing_call = s.expect_error(
        "get_user for an unknown id reports not-found",
        "get_user", {"user_id": "mcp-audit-no-such-user"},
    )
    s.check("unknown user error mentions 404",
            lambda: require("404" in missing_call.error,
                            missing_call.error or missing_call.data))

    groups_call = s.call("list_groups", "list_groups", {"limit": 100})
    groups = s.items("list_groups returns items", groups_call)
    s.check("list_groups returns group objects, not bare ids",
            lambda: require(
                all(isinstance(item, dict) for item in groups),
                f"got {groups[:3]}",
            ))
    s.check("list_groups includes primaryusers with a display name",
            lambda: require(
                bool(find_item(groups, id="primaryusers").get("displayname")
                     or find_item(groups, id="primaryusers").get("display-name")),
                find_item(groups, id="primaryusers"),
            ))

    members_call = s.call("list_group_members primaryusers",
                          "list_group_members",
                          {"group_id": "primaryusers", "limit": 100})
    members = s.items("list_group_members returns items", members_call)
    s.check("group members are returned as objects",
            lambda: require(
                all(isinstance(item, dict) for item in members),
                f"got {members[:3]}",
            ))
    s.check("group members include solo",
            lambda: require(
                bool(find_item(members, id="solo")),
                f"ids: {[_as_id(item) for item in members]}",
            ))
