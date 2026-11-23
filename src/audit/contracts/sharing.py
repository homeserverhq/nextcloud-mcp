"""File sharing contracts, including recipient visibility."""

from __future__ import annotations

import os

from ..backend import Backend
from ..expect import require
from ..harness import Scenario, scenario

SECOND_USER = os.getenv("AUDIT_SECOND_USER", "solo")
SECOND_PASSWORD = os.getenv("AUDIT_SECOND_PASSWORD", "")


@scenario(
    "sharing",
    "user_and_public_share_lifecycle",
    [
        "create_share", "get_share", "list_all_shares",
        "list_shares_by_path", "update_share", "delete_share",
    ],
)
def user_and_public_share_lifecycle(s: Scenario) -> None:
    owner = Backend()
    recipient = Backend(user=SECOND_USER, password=SECOND_PASSWORD) \
        if SECOND_PASSWORD else None
    folder = f"{s.prefix}-share"
    file_name = "shared.txt"
    content = "shared content"

    s.call("create folder", "create_folder", {"path": folder})
    s.cleanup("delete_folder", {"path": folder}, "cleanup share folder")
    s.call("write file", "write_file",
           {"path": f"{folder}/{file_name}", "content": content})

    share_call = s.call("create user share", "create_share", {
        "path": folder,
        "share_type": "user",
        "share_with": SECOND_USER,
        "permissions": "read",
    })
    share = share_call.data
    share_id = share.get("id")
    s.cleanup("delete_share", {"id": share_id}, "cleanup user share")
    s.check("share id is returned", lambda: require(share_id is not None, share))
    s.check("user share targets the requested user",
            lambda: require(share.get("share_with") == SECOND_USER, share))
    s.check("read-only share is created with permissions=1",
            lambda: require(share.get("permissions") == 1,
                            f"got permissions={share.get('permissions')!r}"))

    get_call = s.call("get share", "get_share", {"id": share_id})
    s.check("get_share returns the same share",
            lambda: require(
                str(get_call.data.get("id")) == str(share_id)
                and get_call.data.get("path") == share.get("path"),
                get_call.data,
            ))
    s.check("get_share preserves read-only permissions",
            lambda: require(get_call.data.get("permissions") == 1,
                            get_call.data))

    all_call = s.call("list all shares", "list_all_shares", {})
    all_shares = s.items("list_all_shares returns items", all_call)
    s.check("list_all_shares includes the new share",
            lambda: require(
                any(str(item.get("id")) == str(share_id) for item in all_shares),
                f"ids: {[item.get('id') for item in all_shares]}",
            ))

    path_call = s.call("list shares by path", "list_shares_by_path",
                       {"path": folder})
    path_shares = s.items("list_shares_by_path returns items", path_call)
    s.check("list_shares_by_path includes the new share",
            lambda: require(
                any(str(item.get("id")) == str(share_id) for item in path_shares),
                f"ids: {[item.get('id') for item in path_shares]}",
            ))

    if recipient is None:
        s.skip("recipient visibility", "AUDIT_SECOND_PASSWORD not set")
    else:
        response = recipient.fetch(
            f"/remote.php/dav/files/{SECOND_USER}/{folder}/{file_name}")
        s.check("recipient can read the shared file",
                lambda: require(
                    response.status_code == 200 and response.text == content,
                    f"HTTP {response.status_code}: {response.text[:120]!r}",
                ))

    update_call = s.call("grant full permissions", "update_share",
                         {"id": share_id, "permissions": "all"})
    s.check("update_share returns full permissions",
            lambda: require(update_call.data.get("permissions") == 31,
                            update_call.data))
    get_call = s.call("get updated share", "get_share", {"id": share_id})
    s.check("updated permissions persist",
            lambda: require(get_call.data.get("permissions") == 31,
                            get_call.data))

    update_call = s.call("restore read-only permissions", "update_share",
                         {"id": share_id, "permissions": "read"})
    s.check("permissions can be reduced back to read-only",
            lambda: require(update_call.data.get("permissions") == 1,
                            update_call.data))

    public_call = s.call("create public link", "create_share", {
        "path": f"{folder}/{file_name}",
        "share_type": "public",
        "share_with": "",
        "permissions": "read",
    })
    public = public_call.data
    public_id = public.get("id")
    s.cleanup("delete_share", {"id": public_id}, "cleanup public share")
    s.check("public share has the link share type",
            lambda: require(public.get("share_type") == 3, public))
    s.check("public share exposes a token and URL",
            lambda: require(
                bool(public.get("token")) and bool(public.get("url")),
                f"token={public.get('token')!r} url={public.get('url')!r}",
            ))
    s.call("delete public share", "delete_share", {"id": public_id})
    s.expect_error("deleted public share is gone", "get_share",
                   {"id": public_id})

    s.call("delete user share", "delete_share", {"id": share_id})
    s.expect_error("deleted user share is gone", "get_share", {"id": share_id})
    all_call = s.call("list shares after delete", "list_all_shares", {})
    all_shares = s.items("list_all_shares after delete returns items", all_call)
    s.check("deleted user share is no longer listed",
            lambda: require(
                not any(str(item.get("id")) == str(share_id) for item in all_shares),
                f"ids: {[item.get('id') for item in all_shares]}",
            ))

    if recipient is not None:
        response = recipient.fetch(
            f"/remote.php/dav/files/{SECOND_USER}/{folder}/{file_name}")
        s.check("recipient loses access after the share is deleted",
                lambda: require(response.status_code in (403, 404),
                                f"HTTP {response.status_code}"))
