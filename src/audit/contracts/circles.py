"""Circle (Team) contracts."""

from __future__ import annotations

from ..expect import find_item, require
from ..harness import Scenario, scenario

SECOND_DISPLAY = "Han Solo"


@scenario(
    "circles",
    "circle_lifecycle",
    [
        "list_circles", "get_circle", "create_circle", "update_circle",
        "delete_circle", "list_circle_members", "add_circle_member",
        "remove_circle_member", "update_circle_member_level",
    ],
)
def circle_lifecycle(s: Scenario) -> None:
    circle = s.call("create circle", "create_circle",
                    {"name": f"{s.prefix}-circle"})
    circle_id = circle.data.get("id") or circle.data.get("circle_id")
    s.check("create_circle returns an id",
            lambda: require(bool(circle_id), circle.data))
    s.cleanup("delete_circle", {"circle_id": circle_id}, "cleanup circle")

    got = s.call("get circle", "get_circle", {"circle_id": circle_id})
    s.check("get_circle returns the new circle",
            lambda: require(
                got.data.get("id") == circle_id
                and got.data.get("name") == f"{s.prefix}-circle",
                got.data,
            ))

    circles = s.call("list circles", "list_circles", {})
    circle_items = s.items("list_circles returns items", circles)
    s.check("new circle is listed",
            lambda: require(any(item.get("id") == circle_id for item in circle_items),
                            [item.get("id") for item in circle_items][:10]))

    updated = s.call("update circle", "update_circle",
                     {"circle_id": circle_id, "name": f"{s.prefix}-circle-v2"})
    s.check("update_circle returns the new name",
            lambda: require(updated.data.get("name") == f"{s.prefix}-circle-v2",
                            updated.data))
    got = s.call("get renamed circle", "get_circle", {"circle_id": circle_id})
    s.check("circle rename persisted",
            lambda: require(got.data.get("name") == f"{s.prefix}-circle-v2",
                            got.data))

    members = s.call("list members before add", "list_circle_members",
                     {"circle_id": circle_id, "include_all_fields": True})
    before = s.items("members before add return items", members)
    s.check("circle starts with the owner as a member",
            lambda: require(bool(before), before))

    s.call("add member", "add_circle_member",
           {"circle_id": circle_id, "user_id": "solo"})
    members = s.call("list members after add", "list_circle_members",
                     {"circle_id": circle_id, "include_all_fields": True})
    after = s.items("members after add return items", members)
    member = None
    for item in after:
        if item.get("displayName") == SECOND_DISPLAY:
            member = item
            break
    s.check("added member is listed",
            lambda: require(member is not None, after))

    if member:
        member_id = member.get("id") or member.get("member_id")
        level = s.call("update member level", "update_circle_member_level",
                       {"circle_id": circle_id, "member_id": member_id,
                        "level": "Moderator"}, required=False)
        s.check("member level accepts the documented capitalized value",
                lambda: require(
                    not level.is_error,
                    level.error or level.data,
                ))
        if not level.is_error:
            members = s.call("list members after level", "list_circle_members",
                             {"circle_id": circle_id,
                              "include_all_fields": True})
            after = s.items("members after level return items", members)
            current = find_item(after, id=member_id)
            if "level" in current:
                s.check("member level change persists",
                        lambda: require(
                            str(current.get("level")) in ("4", "Moderator", "moderator"),
                            current,
                        ))
            else:
                s.skip("member level verification",
                       "list_circle_members does not expose the member level; "
                       "the update call succeeded but has no MCP read path")

        s.call("remove member", "remove_circle_member",
               {"circle_id": circle_id, "member_id": member_id})
        members = s.call("list members after remove", "list_circle_members",
                         {"circle_id": circle_id, "include_all_fields": True})
        after = s.items("members after remove return items", members)
        s.check("removed member is gone",
                lambda: require(
                    not any(item.get("displayName") == SECOND_DISPLAY
                            for item in after),
                    after,
                ))

    s.call("delete circle", "delete_circle", {"circle_id": circle_id})
    circles = s.call("list circles after delete", "list_circles", {})
    circle_items = s.items("circles after delete return items", circles)
    s.check("deleted circle is no longer listed",
            lambda: require(
                not any(item.get("id") == circle_id for item in circle_items),
                f"still listed: {circle_id}",
            ))
    s.expect_error("deleted circle cannot be fetched", "get_circle",
                   {"circle_id": circle_id},
                   contains=("403", "404", "not found", "does not exist",
                             "deleted"))
