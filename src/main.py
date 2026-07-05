import json
import os
import sys
import datetime as dt
from contextvars import ContextVar
from typing import Any, Optional

from fastmcp import FastMCP, Context
from pydantic import BaseModel
from toon_mcp import json_to_toon

from .client import NextcloudClient

_current_user_token: ContextVar[Optional[str]] = ContextVar("current_user_token", default=None)

ALLOW_ALL_AGGREGATE = os.getenv("ALLOW_ALL_AGGREGATE", "false").lower() in ("true", "1", "yes")
IS_STATEFUL = os.getenv("IS_STATEFUL", "false").lower() in ("true", "1", "yes")


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                _current_user_token.set(token)
        await self.app(scope, receive, send)


mcp = FastMCP("nextcloud-mcp-server")

_client: Optional[NextcloudClient] = None


def get_client() -> NextcloudClient:
    global _client
    if _client is None:
        _client = NextcloudClient()
    return _client


def get_user_token() -> Optional[str]:
    return _current_user_token.get()


# =============================================================================
# Pydantic Contract Models & Tools
# =============================================================================

# ---- Users / Groups ----

class CreateShareParam(BaseModel):
    path: str
    shareType: int
    shareWith: str
    permissions: int = 1
    password: str = ""
    expireDate: str = ""


class UpdateShareParam(BaseModel):
    id: int
    permissions: Optional[int] = None
    password: Optional[str] = None
    expireDate: Optional[str] = None


class SetOOOParam(BaseModel):
    firstDay: str
    lastDay: str
    status: str
    message: str


class CreateCalendarParam(BaseModel):
    display_name: str
    color: str = ""


class UpdateCalendarParam(BaseModel):
    calendar_uri: str
    display_name: str
    color: Optional[str] = None


class CreateEventParam(BaseModel):
    calendar_uri: str
    summary: str
    dtstart: str
    dtend: str
    description: str
    location: str = ""
    rrule: str = ""
    timezone: str = ""


class UpdateEventParam(BaseModel):
    calendar_uri: str
    event_uid: str
    summary: Optional[str] = None
    dtstart: Optional[str] = None
    dtend: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None


class CreateMeetingParam(BaseModel):
    summary: str
    dtstart: str
    dtend: str
    attendees: str
    description: str
    location: str = ""
    calendar_uri: str = ""


class FindAvailabilityParam(BaseModel):
    calendar_uris: str
    from_date: str
    to_date: str
    timezone: str = ""


class CreateTodoParam(BaseModel):
    summary: str
    description: str
    due_date: str
    calendar_uri: str = ""
    priority: int = 0
    status: str = "NEEDS-ACTION"


class UpdateTodoParam(BaseModel):
    todo_uid: str
    calendar_uri: str
    summary: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    due_date: Optional[str] = None
    status: Optional[str] = None


class CreateAddressbookParam(BaseModel):
    display_name: str
    description: str


class CreateContactParam(BaseModel):
    addressbook_uri: str
    fn: str
    email: str
    telephone: str = ""
    organization: str = ""
    adr: str = ""


class UpdateContactParam(BaseModel):
    addressbook_uri: str
    contact_uid: str
    fn: Optional[str] = None
    email: Optional[str] = None
    telephone: Optional[str] = None
    organization: Optional[str] = None
    adr: Optional[str] = None


class CreateNoteParam(BaseModel):
    title: str
    content: str
    category: str = ""


class UpdateNoteParam(BaseModel):
    id: int
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    favorite: Optional[bool] = None


class CreateBoardParam(BaseModel):
    title: str
    color: str = ""
    archived: bool = False


class CreateStackParam(BaseModel):
    board_id: int
    title: str
    order: int = 0


class CreateCardParam(BaseModel):
    title: str
    description: str
    duedate: str = ""
    order: int = 0


class UpdateCardParam(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    duedate: Optional[str] = None
    order: Optional[int] = None


class SendMessageParam(BaseModel):
    account_id: int
    subject: str
    body: str
    to: str
    cc: str = ""
    bcc: str = ""


class SetFlagsParam(BaseModel):
    message_id: int
    seen: Optional[bool] = None
    flagged: Optional[bool] = None


class CreateTableParam(BaseModel):
    title: str
    description: str
    emoji: str = ""


class UpdateTableParam(BaseModel):
    table_id: int
    title: Optional[str] = None
    description: Optional[str] = None
    emoji: Optional[str] = None


class CreateCollectiveParam(BaseModel):
    name: str
    emoji: str = ""


class CreatePageParam(BaseModel):
    collective_id: int
    title: str
    parent_id: int = 0


class CreateTagParam(BaseModel):
    collective_id: int
    name: str
    color: str


class UpdateTagParam(BaseModel):
    collective_id: int
    tag_id: int
    name: Optional[str] = None
    color: Optional[str] = None


class MovePageParam(BaseModel):
    collective_id: int
    page_id: int
    destination_collective_id: int
    destination_parent_id: int = 0


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_users(
    search: str = "",
    limit: int = 100,
    offset: int = 0,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List or search users.
    Args:
        search: Search term to filter users by.
        limit: Maximum results. Defaults to 100.
        offset: Pagination offset. Defaults to 0.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_users(get_user_token(), search, limit, offset, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_user(
    user_id: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single user by ID.
    Args:
        user_id: ID of the user. Get this from get_users().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_user(user_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_groups(
    limit: int = 100,
    offset: int = 0,
    search: str = "",
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List or search groups.
    Args:
        limit: Maximum results. Defaults to 100.
        offset: Pagination offset. Defaults to 0.
        search: Search term to filter groups by.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_groups(get_user_token(), limit, offset, search, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_group_members(
    group_id: str,
    limit: int = 100,
    offset: int = 0,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get members of a group.
    Args:
        group_id: ID of the group. Get this from get_groups().
        limit: Maximum results. Defaults to 100.
        offset: Pagination offset. Defaults to 0.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_group_members(group_id, get_user_token(), limit, offset, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def list_all_shares(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all shares.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_shares(get_user_token(), False, "", include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def get_shares_by_path(
    path: str,
    shared_with_me: bool = False,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get shares filtered by file path.
    Args:
        path: File path to filter by.
        shared_with_me: Set to True to only show shares received from others. Defaults to False.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_shares(get_user_token(), shared_with_me, path, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def get_share(
    id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single share by ID.
    Args:
        id: ID of the share. Get this from list_all_shares().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_share(id, get_user_token(), include_all_fields)
    if isinstance(data, list):
        return data[0] if data else {}
    return data


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def create_share(
    path: str,
    share_type: str,
    share_with: str,
    permissions: str = "read",
    password: str = "",
    expire_date: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new share.
    Args:
        path: Path to the file or folder to share.
        share_type: user, group, public, federated, or circle share.
        share_with: User/group ID to share with.
        permissions: read, update, create, delete, share, or all. Defaults to read.
        password: Password for link shares.
        expire_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
    """
    share_type_map = {"user": 0, "group": 1, "public": 3, "federated": 6, "circle": 7}
    perm_map = {"read": 1, "update": 2, "create": 4, "delete": 8, "share": 16, "all": 31}
    params = CreateShareParam(path=path, shareType=share_type_map[share_type], shareWith=share_with,
                              permissions=perm_map[permissions], password=password, expireDate=expire_date)
    return await get_client().create_share(params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def update_share(
    id: int,
    permissions: Optional[str] = None,
    password: Optional[str] = None,
    expire_date: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update an existing share.
    Args:
        id: ID of the share to update. Get this from list_all_shares().
        permissions: read, update, create, delete, share, or all.
        password: New password for link shares.
        expire_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
    """
    perm_map = {"read": 1, "update": 2, "create": 4, "delete": 8, "share": 16, "all": 31}
    if permissions is not None:
        permissions = perm_map[permissions]
    params = UpdateShareParam(id=id, permissions=permissions, password=password, expireDate=expire_date)
    return await get_client().update_share(id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def delete_share(
    id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a share by ID.
    Args:
        id: ID of the share to delete. Get this from list_all_shares().
    """
    data = await get_client().delete_share(id, get_user_token())
    if isinstance(data, list):
        return {"success": True, "deleted_id": id}
    return data


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_user_status(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get the current user's status.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_user_status(get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def set_status(
    status_type: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Set the user status type.
    Args:
        status_type: Status type (online, away, dnd, invisible).
    """
    return await get_client().set_status(status_type, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def set_predefined_message(
    message_id: str,
    clear_at: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Set a predefined status message.
    Args:
        message_id: ID of the predefined message. Get this from get_predefined_statuses() (e.g. "meeting", "commuting").
        clear_at: ISO 8601 format (2026-06-22T15:00:00-04:00).
    """
    return await get_client().set_predefined_message(message_id, get_user_token(), clear_at, ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def set_custom_message(
    message: str,
    status_icon: str,
    clear_at: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Set a custom status message with icon.
    Args:
        message: Custom status message text.
        status_icon: Emoji icon for the status.
        clear_at: ISO 8601 format (2026-06-22T15:00:00-04:00).
    """
    return await get_client().set_custom_message(message, status_icon, get_user_token(), clear_at, ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def clear_message(ctx: Context) -> dict[str, Any]:
    """Clear the current status message."""
    result = await get_client().clear_message(get_user_token(), ALLOW_ALL_AGGREGATE)
    return {"status": "cleared", "result": result}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_predefined_statuses(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List available predefined statuses.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_predefined_statuses(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_user_statuses(
    limit: int = 100,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all user statuses.
    Args:
        limit: Maximum results. Defaults to 100.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_user_statuses(get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_ooo(
    user_id: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get out-of-office settings for a user.
    Args:
        user_id: ID of the user. Get this from get_users().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_ooo(user_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def set_ooo(
    first_day: str,
    last_day: str,
    status: str,
    message: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Set out-of-office settings.
    Args:
        first_day: ISO 8601 format (2026-06-22T15:00:00-04:00).
        last_day: ISO 8601 format (2026-06-22T15:00:00-04:00).
        status: OOO status message.
        message: Long OOO message.
    """
    first_day_dt = dt.datetime.fromisoformat(first_day) if 'T' in first_day else first_day
    last_day_dt = dt.datetime.fromisoformat(last_day) if 'T' in last_day else last_day
    first_day_str = first_day_dt.strftime('%Y-%m-%d') if isinstance(first_day_dt, dt.datetime) else first_day_dt
    last_day_str = last_day_dt.strftime('%Y-%m-%d') if isinstance(last_day_dt, dt.datetime) else last_day_dt
    params = SetOOOParam(firstDay=first_day_str, lastDay=last_day_str, status=status, message=message)
    user_id = NextcloudClient.extract_username(get_user_token() or "")
    return await get_client().set_ooo(user_id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def clear_ooo(
    user_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Clear out-of-office for a user.
    Args:
        user_id: ID of the user. Get this from get_users().
    """
    data = await get_client().clear_ooo(user_id, get_user_token())
    return {"success": True, "data": data}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_upcoming_events(
    location: str = "",
    limit: int = 20,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get upcoming events.
    Args:
        location: Location to filter events by.
        limit: Maximum results. Defaults to 20.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_upcoming_events(get_user_token(), location, limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def get_direct_download_url(
    file_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a direct download URL for a file.
    Args:
        file_id: ID of the file.
    """
    return await get_client().get_direct_download_url(file_id, get_user_token())


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def get_folder_tree(
    depth: int = 3,
    path: str = "",
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get the folder tree.
    Args:
        depth: Maximum depth. Defaults to 3.
        path: Full DAV path: /remote.php/dav/files/{username}/... Root path to start from.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_folder_tree(get_user_token(), depth, path, include_all_fields)
    return {"tree": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def collection_get_by_id(
    id: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get collection details by ID.
    Args:
        id: Collection ID.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().list_collection(id, get_user_token(), include_all_fields)


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def collection_search(
    search: str = "",
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Search collections.
    Args:
        search: Search term.
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().search_collections(search, get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def collection_search_raw(
    search: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Search collections (returns raw JSON). Use this for data extraction.
    Args:
        search: Search term.
    """
    data = await get_client().search_collections(search, get_user_token())
    return {"items": data}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def collection_get_by_resource(
    resource_type: str,
    resource_id: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get collections for a specific resource.
    Args:
        resource_type: Type of resource.
        resource_id: ID of resource.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_collections_by_resource(resource_type, resource_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collection_create(
    resource_type: str = "file",
    resource_id: str = "",
    name: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a collection on a resource.
    Args:
        resource_type: Type of resource (default: "file").
        resource_id: ID of resource.
        name: Collection name.
    """
    return await get_client().create_collection_on_resource(resource_type, resource_id, name, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_file_reminder(
    file_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get the reminder for a file.
    Args:
        file_id: ID of the file. Get this from webdav_list_directory() (the oc:fileid field in PROPFIND response).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_file_reminder(file_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def set_file_reminder(
    file_id: int,
    due_date: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Set a reminder on a file.
    Args:
        file_id: ID of the file. Get this from webdav_list_directory() (the oc:fileid field in PROPFIND response).
        due_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
    """
    result = await get_client().set_file_reminder(file_id, due_date, get_user_token(), ALLOW_ALL_AGGREGATE)
    if isinstance(result, list):
        return {"success": True, "reminder_set": True}
    return result


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def delete_file_reminder(
    file_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete the reminder on a file.
    Args:
        file_id: ID of the file. Get this from webdav_list_directory() (the oc:fileid field in PROPFIND response).
    """
    result = await get_client().delete_file_reminder(file_id, get_user_token())
    if isinstance(result, list):
        return {"success": True, "reminder_deleted": True}
    return result


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_search_providers(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all search providers.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_search_providers(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def search(
    provider_id: str,
    term: str,
    limit: int = 50,
    cursor: str = "",
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Unified search across Nextcloud.
    Args:
        provider_id: Search provider ID. Get this from get_search_providers() (e.g. "files", "calendar", "deck").
        term: Search term.
        limit: Maximum results. Defaults to 50.
        cursor: Pagination cursor.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().search(provider_id, term, get_user_token(), limit, cursor, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"results": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def get_capabilities(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get server capabilities.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_capabilities(get_user_token(), include_all_fields)


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def webdav_list_directory(
    path: str,
    depth: int = 1,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List files and directories in a path.
    Args:
        path: Full DAV path: /remote.php/dav/files/{username}/...
        depth: Directory depth. Defaults to 1.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().webdav_list_directory(path, get_user_token(), depth, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def webdav_read_file(
    path: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Read file content from a path.
    Args:
        path: Full DAV path: /remote.php/dav/files/{username}/...
    """
    return {"content": await get_client().webdav_read_file(path, get_user_token())}


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def webdav_write_file(
    path: str,
    content: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Write content to a file.
    Args:
        path: Full DAV path: /remote.php/dav/files/{username}/...
        content: Content to write.
    """
    return await get_client().webdav_write_file(path, content, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def webdav_create_directory(
    path: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a directory.
    Args:
        path: Full DAV path: /remote.php/dav/files/{username}/...
    """
    return await get_client().webdav_create_directory(path, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def webdav_delete_resource(
    path: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a file or directory.
    Args:
        path: Full DAV path: /remote.php/dav/files/{username}/...
    """
    return await get_client().webdav_delete_resource(path, get_user_token())


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def webdav_move_resource(
    source: str,
    destination: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Move or rename a file or directory.
    Args:
        source: Full DAV path: /remote.php/dav/files/{username}/...
        destination: Full DAV path: /remote.php/dav/files/{username}/...
    """
    return await get_client().webdav_move_resource(source, destination, get_user_token())


@mcp.tool(tags={"write", "basic", "nextcloud"})
async def webdav_copy_resource(
    source: str,
    destination: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Copy a file or directory.
    Args:
        source: Full DAV path: /remote.php/dav/files/{username}/...
        destination: Full DAV path: /remote.php/dav/files/{username}/...
    """
    return await get_client().webdav_copy_resource(source, destination, get_user_token())


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def webdav_search_files(
    query: str,
    limit: int = 50,
    mime_type: str = "",
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Search files by name or content.
    Args:
        query: Search query.
        limit: Maximum results. Defaults to 50.
        mime_type: Filter by MIME type.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().webdav_search_files(query, get_user_token(), limit, mime_type, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def webdav_list_favorites(
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List favorite files.
    Args:
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().webdav_list_favorites(get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "basic", "nextcloud"})
async def notification_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all notifications.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_notifications(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def notification_get(
    id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single notification by ID.
    Args:
        id: Notification ID. Get this from list_notifications().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_notification(id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def notification_delete(
    id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a notification by ID.
    Args:
        id: Notification ID to delete. Get this from list_notifications().
    """
    result = await get_client().delete_notification(id, get_user_token())
    if isinstance(result, list):
        return {"success": True, "deleted": True}
    return result


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def notification_delete_all(ctx: Context) -> dict[str, Any]:
    """Delete all notifications."""
    result = await get_client().delete_all_notifications(get_user_token())
    if isinstance(result, list):
        return {"success": True, "deleted": True}
    return result


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def notification_create(
    user_id: str,
    subject: str,
    message: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a notification for a user (admin only).
    Args:
        user_id: ID of the user to notify.
        subject: Subject of the notification, max 255 chars.
        message: Message of the notification, max 4000 chars.
    """
    return await get_client().create_notification(user_id, subject, message, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def activity_get(
    limit: int = 50,
    sort: str = "",
    object_type: str = "",
    object_id: int = 0,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get the activity stream.
    Args:
        limit: Maximum results. Defaults to 50.
        sort: Sort order. e.g. 'date' (default), 'subject'.
        object_type: Filter by object type. e.g. 'files', 'deck_card', 'calendar'.
        object_id: Filter by object ID.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_activity(get_user_token(), limit, sort, object_type, object_id, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def activity_get_filters(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get available activity filters.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_activity_filters(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def activity_get_filter(
    filter_name: str,
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get filtered activity stream.
    Args:
        filter_name: Filter name. Get this from activity_get_filters() (e.g. "all", "self").
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().get_activity_filter(filter_name, get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_all_calendars(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all calendars.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_calendars(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def create_calendar(
    display_name: str,
    color: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new calendar.
    Args:
        display_name: Display name for the calendar.
        color: Calendar color in #RRGGBB format (e.g. #FF0000 for red).
    """
    params = CreateCalendarParam(display_name=display_name, color=color)
    return await get_client().create_calendar(params.display_name, params.color, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def update_calendar(
    calendar_uri: str,
    display_name: str,
    color: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a calendar's properties.
    Args:
        calendar_uri: URI of the calendar to update. Get this from list_calendars().
        display_name: New display name.
        color: New calendar color in #RRGGBB format (e.g. #FF0000 for red).
    """
    result = await get_client().update_calendar(calendar_uri, display_name, color, get_user_token(), ALLOW_ALL_AGGREGATE)
    return {"status": "updated", "result": result}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def delete_calendar(
    calendar_uri: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a calendar.
    Args:
        calendar_uri: URI of the calendar to delete. Get this from list_calendars().
    """
    return await get_client().delete_calendar(calendar_uri, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_calendar(
    calendar_uri: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single calendar by URI.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_calendar(calendar_uri, get_user_token(), include_all_fields)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_all_events(
    from_date: str = "",
    to_date: str = "",
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List events from ALL calendars.
    Args:
        from_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        to_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    all_cals = await get_client().list_calendars(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    all_events = []
    for cal in all_cals:
        href = cal.get("href", "")
        if "/remote.php/dav/calendars/" in href:
            uri = href.split("/remote.php/dav/calendars/", 1)[1].rstrip("/")
        else:
            continue
        events = await get_client().list_events(uri, get_user_token(), from_date, to_date, limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
        all_events.extend(events)
    return {"items": json_to_toon(all_events[:limit])}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_events_by_calendar(
    calendar_uri: str,
    from_date: str = "",
    to_date: str = "",
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List events in a specific calendar.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        from_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        to_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_events(calendar_uri, get_user_token(), from_date, to_date, limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_event(
    calendar_uri: str,
    event_uid: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single calendar event.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        event_uid: UID of the event. Get this from get_events_by_calendar(calendar_uri).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_event(calendar_uri, event_uid, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def create_event(
    calendar_uri: str,
    summary: str,
    dtstart: str,
    dtend: str,
    description: str,
    location: str = "",
    rrule: str = "",
    timezone: str = "",
    attendees: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new calendar event.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        summary: Event title.
        dtstart: ISO 8601 format (2026-06-22T15:00:00-04:00).
        dtend: ISO 8601 format (2026-06-22T15:00:00-04:00).
        description: Event description.
        location: Event location.
        rrule: Recurrence rule.
        timezone: Timezone of the event.
        attendees: Comma-separated attendee email addresses to invite (e.g. 'leon@example.com'). Leave empty for no attendees.
    """
    return await get_client().create_event(calendar_uri, summary, dtstart, dtend,
                                           description, location, rrule, timezone, api_key=get_user_token(), attendees=attendees, include_all_fields=ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def update_event(
    calendar_uri: str,
    event_uid: str,
    summary: Optional[str] = None,
    dtstart: Optional[str] = None,
    dtend: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a calendar event.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        event_uid: UID of the event to update. Get this from get_events_by_calendar(calendar_uri).
        summary: New event title.
        dtstart: ISO 8601 format (2026-06-22T15:00:00-04:00).
        dtend: ISO 8601 format (2026-06-22T15:00:00-04:00).
        description: New description.
        location: New location.
        attendees: Comma-separated attendee email addresses. Leave empty (default) to preserve existing attendees.
    """
    return await get_client().update_event(calendar_uri, event_uid, summary, dtstart,
                                           dtend, description, location, get_user_token(), attendees=attendees or "", include_all_fields=ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def delete_event(
    calendar_uri: str,
    event_uid: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a calendar event.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        event_uid: UID of the event to delete. Get this from get_events_by_calendar(calendar_uri).
    """
    return await get_client().delete_event(calendar_uri, event_uid, get_user_token())


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def create_meeting(
    summary: str,
    dtstart: str,
    dtend: str,
    attendees: str,
    description: str,
    location: str = "",
    calendar_uri: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Quickly create a meeting with attendees.
    Args:
        summary: Meeting title.
        dtstart: ISO 8601 format (2026-06-22T15:00:00-04:00).
        dtend: ISO 8601 format (2026-06-22T15:00:00-04:00).
        attendees: Comma-separated email addresses.
        description: Meeting description.
        location: Meeting location.
        calendar_uri: Leave empty (default) to use your personal/default calendar. Only fill this if you need to use a specific calendar.
    """
    return await get_client().create_meeting(summary, dtstart, dtend, attendees, description,
                                             location, calendar_uri, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def find_availability(
    calendar_uris: str,
    from_date: str,
    to_date: str,
    timezone: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Find availability across calendars.
    Args:
        calendar_uris: Comma-separated calendar URIs. Get these from list_calendars().
        from_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        to_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        timezone: Timezone for availability.
    """
    return await get_client().find_availability(calendar_uris, from_date, to_date, timezone, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def todo_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List TODO items from ALL calendars.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    all_cals = await get_client().list_calendars(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    all_todos = []
    for cal in all_cals:
        href = cal.get("href", "")
        if "/remote.php/dav/calendars/" in href:
            uri = href.split("/remote.php/dav/calendars/", 1)[1].rstrip("/")
        else:
            continue
        todos = await get_client().list_todos(get_user_token(), uri, include_all_fields if ALLOW_ALL_AGGREGATE else False)
        all_todos.extend(todos)
    return {"items": json_to_toon(all_todos)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def todo_get_by_calendar(
    calendar_uri: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List TODO items in a specific calendar.
    Args:
        calendar_uri: URI of the calendar. Get this from list_calendars().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_todos(get_user_token(), calendar_uri, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def todo_create(
    summary: str,
    description: str,
    due_date: str,
    calendar_uri: str = "",
    priority: int = 0,
    status: str = "NEEDS-ACTION",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new TODO item.
    Args:
        summary: TODO title.
        description: TODO description.
        due_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        calendar_uri: Calendar URI to add the todo to. Get this from list_calendars(). If omitted, uses default task calendar.
        priority: 0-9. Lower = more urgent (1=highest, 9=lowest). 0=no priority. Defaults to 0.
        status: Status (NEEDS-ACTION, IN-PROCESS, COMPLETED). Defaults to NEEDS-ACTION.
    """
    return await get_client().create_todo(summary, description, due_date, priority,
                                          status, calendar_uri, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def todo_get(
    todo_uid: str,
    calendar_uri: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single TODO item by UID.
    Args:
        todo_uid: UID of the todo. Get this from todo_get_by_calendar(calendar_uri).
        calendar_uri: Calendar URI where the todo is stored. Get this from list_calendars().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_todo(todo_uid, calendar_uri, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def todo_update(
    todo_uid: str,
    calendar_uri: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[int] = None,
    due_date: Optional[str] = None,
    status: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a TODO item.
    Args:
        todo_uid: UID of the todo. Get this from todo_get_by_calendar(calendar_uri).
        calendar_uri: Calendar URI where the todo is stored. Get this from list_calendars().
        summary: New title.
        description: New description.
        priority: New priority (0-9). Lower = more urgent (1=highest, 9=lowest). 0=no priority.
        due_date: ISO 8601 format (2026-06-22T15:00:00-04:00).
        status: New status.
    """
    return await get_client().update_todo(todo_uid, calendar_uri, summary, description,
                                          priority, due_date, status, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def todo_delete(
    todo_uid: str,
    calendar_uri: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a TODO item.
    Args:
        todo_uid: UID of the todo to delete. Get this from todo_get_by_calendar(calendar_uri).
        calendar_uri: Calendar URI where the todo is stored. Get this from list_calendars().
    """
    return await get_client().delete_todo(todo_uid, calendar_uri, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def todo_search(
    query: str,
    calendar_uri: str = "",
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Search TODO items by title or description.
    Args:
        query: Search term.
        calendar_uri: Calendar URI to search in. Get this from list_calendars(). If omitted, uses default task calendar.
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().search_todos(query, get_user_token(), calendar_uri, limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_all_addressbooks(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all address books.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_addressbooks(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def create_addressbook(
    display_name: str,
    description: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new address book.
    Args:
        display_name: Display name.
        description: Description.
    """
    params = CreateAddressbookParam(display_name=display_name, description=description)
    return await get_client().create_addressbook(params.display_name, params.description, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def delete_addressbook(
    addressbook_uri: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete an address book.
    Args:
        addressbook_uri: URI of the address book to delete. Get this from list_addressbooks().
    """
    return await get_client().delete_addressbook(addressbook_uri, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_addressbook(
    addressbook_uri: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single address book by URI.
    Args:
        addressbook_uri: URI of the address book. Get this from list_addressbooks().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_addressbook(addressbook_uri, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def update_addressbook(
    addressbook_uri: str,
    display_name: str,
    description: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update an address book's properties.
    Args:
        addressbook_uri: URI of the address book to update. Get this from list_addressbooks().
        display_name: New display name.
        description: New description.
    """
    result = await get_client().update_addressbook(addressbook_uri, display_name, description, get_user_token(), ALLOW_ALL_AGGREGATE)
    return {"status": "updated", "result": result}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_all_contacts(
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List contacts from ALL address books.
    Args:
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    all_abooks = await get_client().list_addressbooks(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    all_contacts = []
    for abook in all_abooks:
        href = abook.get("href", "")
        if "/remote.php/dav/addressbooks/" in href:
            uri = href.split("/remote.php/dav/addressbooks/", 1)[1].rstrip("/")
        else:
            continue
        contacts = await get_client().list_contacts(uri, get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
        all_contacts.extend(contacts)
    return {"items": json_to_toon(all_contacts[:limit])}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_contacts_by_addressbook(
    addressbook_uri: str,
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List contacts in a specific address book.
    Args:
        addressbook_uri: URI of the address book. Get this from list_addressbooks().
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_contacts(addressbook_uri, get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_contact(
    addressbook_uri: str,
    contact_uid: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single contact by UID.
    Args:
        addressbook_uri: URI of the address book. Get this from list_addressbooks().
        contact_uid: UID of the contact. Get this from get_contacts_by_addressbook(addressbook_uri).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_contact(addressbook_uri, contact_uid, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def create_contact(
    addressbook_uri: str,
    fn: str,
    email: str,
    telephone: str = "",
    organization: str = "",
    adr: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new contact.
    Args:
        addressbook_uri: URI of the address book. Get this from list_addressbooks().
        fn: Full name.
        email: Email address.
        telephone: Phone number.
        organization: Organization name.
        adr: Postal address.
    """
    return await get_client().create_contact(addressbook_uri, fn, email, telephone,
                                             organization, adr, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def update_contact(
    addressbook_uri: str,
    contact_uid: str,
    fn: Optional[str] = None,
    email: Optional[str] = None,
    telephone: Optional[str] = None,
    organization: Optional[str] = None,
    adr: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a contact.
    Args:
        addressbook_uri: URI of the address book. Get this from list_addressbooks().
        contact_uid: UID of the contact to update. Get this from get_contacts_by_addressbook(addressbook_uri).
        fn: New full name.
        email: New email.
        telephone: New phone number.
        organization: New organization.
        adr: New postal address.
    """
    return await get_client().update_contact(addressbook_uri, contact_uid, fn, email,
                                             telephone, organization, adr, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def delete_contact(
    addressbook_uri: str,
    contact_uid: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a contact.
    Args:
        addressbook_uri: URI of the address book. Get this from list_addressbooks().
        contact_uid: UID of the contact to delete. Get this from get_contacts_by_addressbook(addressbook_uri).
    """
    return await get_client().delete_contact(addressbook_uri, contact_uid, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def note_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all notes.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_notes(get_user_token(), "", include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def note_get_by_category(
    category: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get notes filtered by category.
    Args:
        category: Category to filter by.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_notes(get_user_token(), category, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def note_get(
    id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single note by ID.
    Args:
        id: Note ID. Get this from note_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().get_note(id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def note_create(
    title: str,
    content: str,
    category: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new note.
    Args:
        title: Note title.
        content: Note content.
        category: Note category.
    """
    params = CreateNoteParam(title=title, content=content, category=category)
    return await get_client().create_note(params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def note_update(
    id: int,
    title: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
    favorite: Optional[bool] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a note.
    Args:
        id: Note ID. Get this from note_list().
        title: New title.
        content: New content.
        category: New category.
        favorite: Set as favorite.
    """
    params = UpdateNoteParam(id=id, title=title, content=content, category=category, favorite=favorite)
    return await get_client().update_note(id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def note_delete(
    id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a note by ID.
    Args:
        id: Note ID to delete. Get this from note_list().
    """
    await get_client().delete_note(id, get_user_token())
    return {"status": "deleted", "id": id}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def note_append_content(
    id: int,
    content: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Append content to an existing note.
    Args:
        id: Note ID. Get this from note_list().
        content: Content to append.
    """
    existing = await get_client().get_note(id, get_user_token())
    combined = (existing.get("content") or "") + "\n\n" + content
    return await get_client().update_note(id, {"content": combined}, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def note_search(
    query: str,
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Search notes by title or content.
    Args:
        query: Search term.
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().list_notes(get_user_token(), "", include_all_fields if ALLOW_ALL_AGGREGATE else False)
    if isinstance(data, list):
        q = query.lower()
        filtered = [n for n in data if q in n.get("title", "").lower() or q in n.get("content", "").lower()]
        data = filtered[:limit]
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def note_get_attachment(
    note_id: int,
    path: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Get an attachment from a note.
    Args:
        note_id: Note ID.
        path: Filename of the attachment within the Notes folder.
    """
    return await get_client().get_attachment(note_id, path, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all boards.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().deck_list_boards(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_get_board(
    board_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single board by ID.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().deck_get_board(board_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def deck_create_board(
    title: str,
    color: str = "",
    archived: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new board.
    Args:
        title: Board title.
        color: Display color in #RRGGBB format (e.g. #FF0000 for red).
        archived: Whether the board is archived. Defaults to False.
    """
    params = CreateBoardParam(title=title, color=color, archived=archived)
    return await get_client().deck_create_board(params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_update_board(
    board_id: int,
    title: Optional[str] = None,
    color: Optional[str] = None,
    archived: Optional[bool] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a board.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        title: New title.
        color: New display color in #RRGGBB format (e.g. #FF0000 for red).
        archived: Whether the board is archived.
    """
    payload = {}
    if title is not None:
        payload["title"] = title
    if color is not None:
        payload["color"] = color
    if archived is not None:
        payload["archived"] = archived
    return await get_client().deck_update_board(board_id, payload, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_list_stacks(
    board_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List stacks in a board.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().deck_list_stacks(board_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def deck_create_stack(
    board_id: int,
    title: str,
    order: int = 0,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new stack in a board.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        title: Stack title.
        order: Display order. Defaults to 0.
    """
    params = CreateStackParam(board_id=board_id, title=title, order=order)
    return await get_client().deck_create_stack(board_id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_get_stack(
    board_id: int,
    stack_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single stack by ID.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().deck_get_stack(board_id, stack_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_update_stack(
    board_id: int,
    stack_id: int,
    title: Optional[str] = None,
    order: Optional[int] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a stack.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        title: New title.
        order: New display order.
    """
    payload = {}
    if title is not None:
        payload["title"] = title
    if order is not None:
        payload["order"] = order
    return await get_client().deck_update_stack(board_id, stack_id, payload, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_list_cards(
    board_id: int,
    stack_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List cards in a stack.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().deck_list_cards(board_id, stack_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_get_card(
    board_id: int,
    stack_id: int,
    card_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single card by ID.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().deck_get_card(board_id, stack_id, card_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def deck_create_card(
    board_id: int,
    stack_id: int,
    title: str,
    description: str,
    duedate: str = "",
    order: int = 0,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        title: Card title.
        description: Card description.
        duedate: ISO 8601 format (2026-06-22T15:00:00-04:00).
        order: Display order. Defaults to 0.
    """
    params = CreateCardParam(title=title, description=description,
                              duedate=duedate, order=order)
    return await get_client().deck_create_card(board_id, stack_id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def deck_update_card(
    board_id: int,
    stack_id: int,
    card_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    duedate: Optional[str] = None,
    order: Optional[int] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
        title: New title.
        description: New description.
        duedate: ISO 8601 format (2026-06-22T15:00:00-04:00).
        order: New display order.
    """
    params = UpdateCardParam(title=title, description=description,
                              duedate=duedate, order=order)
    return await get_client().deck_update_card(board_id, stack_id, card_id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_delete_board(
    board_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a board by ID.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
    """
    return await get_client().deck_delete_board(board_id, get_user_token())


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_delete_stack(
    board_id: int,
    stack_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a stack by ID.
    Args:
        board_id: Board ID the stack belongs to.
        stack_id: Stack ID to delete. Get this from deck_list_stacks(board_id).
    """
    return await get_client().deck_delete_stack(board_id, stack_id, get_user_token())


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def deck_delete_card(
    board_id: int,
    stack_id: int,
    card_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a card by ID.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID to delete. Get this from deck_list_cards(board_id, stack_id).
    """
    return await get_client().deck_delete_card(board_id, stack_id, card_id, get_user_token())


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_archive_card(
    board_id: int,
    stack_id: int,
    card_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Archive a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
    """
    return await get_client().deck_archive_card(board_id, stack_id, card_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_unarchive_card(
    board_id: int,
    stack_id: int,
    card_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Unarchive a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
    """
    return await get_client().deck_unarchive_card(board_id, stack_id, card_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def deck_list_labels(
    board_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List labels in a board.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().deck_list_labels(board_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_assign_label(
    board_id: int,
    stack_id: int,
    card_id: int,
    label_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Assign a label to a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
        label_id: Label ID. Get this from deck_list_labels(board_id).
    """
    return await get_client().deck_assign_label(board_id, stack_id, card_id, label_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_remove_label(
    board_id: int,
    stack_id: int,
    card_id: int,
    label_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Remove a label from a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
        label_id: Label ID. Get this from deck_list_labels(board_id).
    """
    return await get_client().deck_remove_label(board_id, stack_id, card_id, label_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_assign_user(
    board_id: int,
    stack_id: int,
    card_id: int,
    user_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Assign a user to a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
        user_id: User ID to assign.
    """
    return await get_client().deck_assign_user(board_id, stack_id, card_id, user_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def deck_remove_user(
    board_id: int,
    stack_id: int,
    card_id: int,
    user_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Remove a user from a card.
    Args:
        board_id: Board ID. Get this from deck_list_boards().
        stack_id: Stack ID. Get this from deck_list_stacks(board_id).
        card_id: Card ID. Get this from deck_list_cards(board_id, stack_id).
        user_id: User ID to remove.
    """
    return await get_client().deck_remove_user(board_id, stack_id, card_id, user_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_all_accounts(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List mail accounts.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().mail_list_accounts(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_mailboxes(
    account_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List mailboxes in an account.
    Args:
        account_id: Account ID. Get this from list_accounts().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().mail_list_mailboxes(account_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def list_messages(
    account_id: int,
    mailbox_db_id: int,
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Sync and list messages in a mailbox.
    Args:
        account_id: Account ID. Get this from list_accounts().
        mailbox_db_id: Mailbox database ID (integer). Get this from list_mailboxes(account_id).
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().mail_list_messages(account_id, mailbox_db_id, get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_message(
    account_id: int,
    message_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single message by its database ID.
    Args:
        account_id: Account ID. Get this from list_accounts().
        message_id: Message database ID. Get this from list_messages(account_id, mailbox_db_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().mail_get_message(account_id, message_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_message_body(
    account_id: int,
    message_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Get the body of a message by its database ID.
    Args:
        account_id: Account ID. Get this from list_accounts().
        message_id: Message database ID. Get this from list_messages(account_id, mailbox_db_id).
    """
    return await get_client().mail_get_message_body(account_id, message_id, get_user_token())


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def send_message(
    account_id: int,
    subject: str,
    body: str,
    to: str,
    cc: str = "",
    bcc: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Send an email message.
    Args:
        account_id: Account ID. Get this from list_accounts().
        subject: Email subject.
        body: Email body.
        to: Recipient email address.
        cc: CC recipient email.
        bcc: BCC recipient email.
    """
    try:
        result = await get_client().mail_send_message(account_id, subject, body, to, cc, bcc, get_user_token(), ALLOW_ALL_AGGREGATE)
        return {"status": "sent", "data": result if isinstance(result, dict) else {}}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def create_email_draft(
    account_id: int,
    subject: str,
    body: str,
    to: str,
    cc: str = "",
    bcc: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a draft email saved to the IMAP Drafts folder.
    Args:
        account_id: Account ID. Get this from list_accounts().
        subject: Email subject.
        body: Email body.
        to: Recipient email address.
        cc: CC recipient email.
        bcc: BCC recipient email.
    """
    result = await get_client().mail_save_draft(account_id, subject, body, to, cc, bcc, get_user_token(), ALLOW_ALL_AGGREGATE)
    return {"id": result.get("id") if isinstance(result, dict) else None, "status": "draft_created"}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def set_message_flags(
    account_id: int,
    message_id: int,
    seen: Optional[bool] = None,
    flagged: Optional[bool] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Set flags on a message (read/unread, starred).
    Args:
        account_id: Account ID. Get this from list_accounts().
        message_id: Message database ID. Get this from list_messages(account_id, mailbox_db_id).
        seen: Mark as seen/unseen.
        flagged: Mark as flagged/unflagged.
    """
    params = SetFlagsParam(message_id=message_id, seen=seen, flagged=flagged)
    result = await get_client().mail_set_message_flags(account_id, message_id, params.seen, params.flagged, get_user_token(), ALLOW_ALL_AGGREGATE)
    return {"status": "ok", "flags": result}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def move_message(
    account_id: int,
    message_id: int,
    mailbox_db_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Move a message to a different mailbox.
    Args:
        account_id: Account ID. Get this from list_accounts().
        message_id: Message database ID to move. Get this from list_messages(account_id, mailbox_db_id).
        mailbox_db_id: Destination mailbox database ID. Get this from list_mailboxes(account_id).
    """
    result = await get_client().mail_move_message(account_id, message_id, mailbox_db_id, get_user_token(), ALLOW_ALL_AGGREGATE)
    return {"status": "moved", "result": result}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def talk_list_rooms(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all Talk conversations available to the current user.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().talk_list_rooms(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def talk_create_room(
    room_type: str,
    room_name: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new Talk conversation room.
    Args:
        room_type: one-to-one, group, or public room.
        room_name: Name for the room.
    """
    room_type_map = {"one-to-one": 1, "group": 2, "public": 3}
    return await get_client().talk_create_room(room_type_map[room_type], room_name, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def talk_delete_room(
    token: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a Talk conversation room.
    Args:
        token: Room token to delete.
    """
    return await get_client().talk_delete_room(token, get_user_token())


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def talk_update_room(
    token: str,
    room_name: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Rename a Talk conversation room.
    Args:
        token: Room token to rename.
        room_name: New name for the room (up to 255 characters).
    """
    return await get_client().talk_update_room(token, room_name, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def talk_set_description(
    token: str,
    description: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Set the description of a Talk conversation room.
    Args:
        token: Room token.
        description: Description text.
    """
    return await get_client().talk_set_description(token, description, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def talk_list_participants(
    token: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List participants in a Talk conversation room.
    Args:
        token: Room token.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().talk_list_participants(token, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def talk_add_participant(
    token: str,
    new_participant: str,
    source: str = "users",
    ctx: Context = None
) -> dict[str, Any]:
    """Add a participant to a Talk conversation room.
    Args:
        token: Room token.
        new_participant: User, group, email, or circle to add.
        source: Source type. Defaults to 'users'. Options: users, groups, emails, circles.
    """
    return await get_client().talk_add_participant(token, new_participant, source, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def talk_remove_participant(
    token: str,
    attendee_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Remove a participant from a Talk conversation room by attendee ID.
    Args:
        token: Room token.
        attendee_id: Attendee ID to remove. Get this from talk_list_participants(token).
    """
    return await get_client().talk_remove_participant(token, attendee_id, get_user_token())


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def send_chat_message(
    token: str,
    message: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Send a chat message to a Talk conversation.
    Args:
        token: Conversation token. Get this from creating a Talk room.
        message: Message text.
    """
    return await get_client().talk_send_message(token, message, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def get_chat_messages(
    token: str,
    limit: int = 50,
    look_into_future: int = 0,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get chat messages from a Talk conversation.
    Args:
        token: Conversation token. Get this from creating a Talk room.
        limit: Maximum results. Defaults to 50.
        look_into_future: Wait for new messages (1) or not (0).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().talk_get_messages(token, get_user_token(), limit, look_into_future, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def delete_chat_message(
    token: str,
    message_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a chat message from a Talk conversation.
    Args:
        token: Conversation token. Get this from creating a Talk room.
        message_id: Message ID to delete. Get this from get_chat_messages(token).
    """
    return await get_client().talk_delete_message(token, message_id, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def table_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all tables.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().tables_list_tables(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def table_get(
    table_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single table by ID.
    Args:
        table_id: Table ID. Get this from table_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().tables_get_table(table_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def table_create(
    title: str,
    description: str,
    emoji: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new table.
    Args:
        title: Table title.
        description: Table description.
        emoji: Table emoji.
    """
    params = CreateTableParam(title=title, description=description, emoji=emoji)
    return await get_client().tables_create_table(params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def table_update(
    table_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    emoji: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a table.
    Args:
        table_id: Table ID. Get this from table_list().
        title: New title.
        description: New description.
        emoji: New emoji.
    """
    params = UpdateTableParam(table_id=table_id, title=title, description=description, emoji=emoji)
    return await get_client().tables_update_table(table_id, params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def table_delete(
    table_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a table by ID.
    Args:
        table_id: Table ID to delete. Get this from table_list().
    """
    return await get_client().tables_delete_table(table_id, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def table_get_schema(
    table_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get the schema of a table.
    Args:
        table_id: Table ID. Get this from table_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().tables_get_schema(table_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def table_list_rows(
    table_id: int,
    limit: int = 50,
    offset: int = 0,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List rows in a table.
    Args:
        table_id: Table ID. Get this from table_list().
        limit: Maximum results. Defaults to 50.
        offset: Pagination offset. Defaults to 0.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().tables_list_rows(table_id, get_user_token(), limit, offset, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"rows": json_to_toon(data)}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def table_create_row(
    table_id: int,
    data: dict[str, Any],
    ctx: Context = None
) -> dict[str, Any]:
    """Insert a new row in a table.
    Args:
        table_id: Table ID. Get this from table_list().
        data: Column values keyed by column title or ID. Use table_get_schema() to discover available columns.
    """
    return await get_client().tables_create_row(table_id, json.dumps(data), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def table_get_row(
    row_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single row from a table.
    Args:
        row_id: Row ID. Get this from table_list_rows(table_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().tables_get_row(row_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def table_update_row(
    row_id: int,
    data: Optional[dict[str, Any]] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update an existing row in a table.
    Args:
        row_id: Row ID. Get this from table_list_rows(table_id).
        data: Column values keyed by column title or ID. Use table_get_schema() to discover available columns.
    """
    return await get_client().tables_update_row(row_id, json.dumps(data) if data is not None else None, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def table_delete_row(
    row_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a row from a table.
    Args:
        row_id: Row ID to delete. Get this from table_list_rows(table_id).
    """
    return await get_client().tables_delete_row(row_id, get_user_token())


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all collectives.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().collectives_list(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_create(
    name: str,
    emoji: str = "",
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new collective.
    Args:
        name: Collective name.
        emoji: Collective emoji.
    """
    params = CreateCollectiveParam(name=name, emoji=emoji)
    return await get_client().collectives_create(params.model_dump(exclude_defaults=True), get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_get_collective(
    id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single collective by ID.
    Args:
        id: Collective ID. Get this from collectives_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().collectives_get_collective(id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_update_collective(
    id: int,
    name: Optional[str] = None,
    emoji: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a collective's properties.
    Args:
        id: Collective ID. Get this from collectives_list().
        name: New name.
        emoji: New emoji.
    """
    payload = {}
    if name is not None:
        payload["name"] = name
    if emoji is not None:
        payload["emoji"] = emoji
    return await get_client().collectives_update_collective(id, payload, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_update_emoji(
    id: int,
    emoji: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a collective's emoji.
    Args:
        id: Collective ID. Get this from collectives_list().
        emoji: New emoji.
    """
    return await get_client().collectives_update_emoji(id, emoji, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_trash(
    id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Soft-delete a collective (move to trash).
    Args:
        id: Collective ID. Get this from collectives_list().
    """
    return await get_client().collectives_trash(id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_restore(
    id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Restore a collective from trash.
    Args:
        id: Collective ID. Get this from collectives_list().
    """
    return await get_client().collectives_restore(id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_delete(
    id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Permanently delete a collective.
    Args:
        id: Collective ID. Get this from collectives_list().
    """
    return await get_client().collectives_delete(id, get_user_token())


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def collectives_list_trashed(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List trashed collectives.
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().collectives_list_trashed(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_list_pages(
    collective_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List pages in a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().collectives_list_pages(collective_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_get_page(
    collective_id: int,
    page_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single page from a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().collectives_get_page(collective_id, page_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def collectives_create_page(
    collective_id: int,
    title: str,
    parent_id: int = 0,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new page in a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        title: Page title.
        parent_id: Parent page ID. Defaults to 0 (root).
    """
    return await get_client().collectives_create_page(collective_id, parent_id,
                                                       {"title": title}, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_trash_page(
    collective_id: int,
    page_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Soft-delete a page (move to trash).
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
    """
    return await get_client().collectives_trash_page(collective_id, page_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_restore_page(
    collective_id: int,
    page_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Restore a page from trash.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
    """
    return await get_client().collectives_restore_page(collective_id, page_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_delete_page(
    collective_id: int,
    page_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Permanently delete a page.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
    """
    result = await get_client().collectives_delete_page(collective_id, page_id, get_user_token())
    if isinstance(result, list):
        return {"success": True, "deleted": True}
    return result


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_move_page(
    collective_id: int,
    page_id: int,
    destination_collective_id: int,
    destination_parent_id: int = 0,
    ctx: Context = None
) -> dict[str, Any]:
    """Move or copy a page to another collective.
    Args:
        collective_id: Source collective ID. Get this from collectives_list().
        page_id: Page ID to move. Get this from collectives_list_pages(collective_id).
        destination_collective_id: Destination collective ID. Get this from collectives_list().
        destination_parent_id: Destination parent page ID.
    """
    return await get_client().collectives_move_page(collective_id, page_id,
                                                     destination_collective_id, destination_parent_id,
                                                     get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_set_page_emoji(
    collective_id: int,
    page_id: int,
    emoji: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Set the emoji for a page.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
        emoji: Emoji to set.
    """
    return await get_client().collectives_set_page_emoji(collective_id, page_id, emoji, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_set_page_full_width(
    collective_id: int,
    page_id: int,
    is_full_width: bool,
    ctx: Context = None
) -> dict[str, Any]:
    """Set the full-width display for a page.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
        is_full_width: Whether the page should be displayed full-width.
    """
    return await get_client().collectives_set_page_full_width(collective_id, page_id, is_full_width, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_update_page(
    collective_id: int,
    page_id: int,
    title: Optional[str] = None,
    emoji: Optional[str] = None,
    is_full_width: Optional[bool] = None,
    parent_id: Optional[int] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a page's properties.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
        title: New title.
        emoji: New emoji.
        is_full_width: Whether page should display full-width.
        parent_id: New parent page ID for moving.
    """
    payload = {}
    if title is not None:
        payload["title"] = title
    if emoji is not None:
        payload["emoji"] = emoji
    if is_full_width is not None:
        payload["fullWidth"] = is_full_width
    if parent_id is not None:
        payload["parentId"] = parent_id
    return await get_client().collectives_update_page(collective_id, page_id, payload, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_search_pages(
    collective_id: int,
    query: str,
    limit: int = 50,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Search pages within a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        query: Search query.
        limit: Maximum results. Defaults to 50.
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().collectives_search_pages(collective_id, query, get_user_token(), limit, include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_list_tags(
    collective_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List tags in a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().collectives_list_tags(collective_id, get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def collectives_get_tag(
    collective_id: int,
    tag_id: int,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a single tag by ID.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        tag_id: Tag ID. Get this from collectives_list_tags(collective_id).
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    return await get_client().collectives_get_tag(collective_id, tag_id, get_user_token(), include_all_fields)


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def collectives_create_tag(
    collective_id: int,
    name: str,
    color: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a tag in a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        name: Tag name.
        color: Tag color.
    """
    params = CreateTagParam(collective_id=collective_id, name=name, color=color)
    return await get_client().collectives_create_tag(collective_id, {"name": name, "color": color}, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_update_tag(
    collective_id: int,
    tag_id: int,
    name: Optional[str] = None,
    color: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a tag in a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        tag_id: Tag ID. Get this from collectives_list_tags(collective_id).
        name: New tag name.
        color: New tag color.
    """
    return await get_client().collectives_update_tag(collective_id, tag_id,
                                                      {"name": name, "color": color} if name or color else {},
                                                      get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_delete_tag(
    collective_id: int,
    tag_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a tag from a collective.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        tag_id: Tag ID. Get this from collectives_list_tags(collective_id).
    """
    result = await get_client().collectives_delete_tag(collective_id, tag_id, get_user_token())
    return {"status": "deleted", "result": result}


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_assign_tag(
    collective_id: int,
    page_id: int,
    tag_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Assign a tag to a page.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
        tag_id: Tag ID. Get this from collectives_list_tags(collective_id).
    """
    return await get_client().collectives_assign_tag(collective_id, page_id, tag_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def collectives_remove_tag(
    collective_id: int,
    page_id: int,
    tag_id: int,
    ctx: Context = None
) -> dict[str, Any]:
    """Remove a tag from a page.
    Args:
        collective_id: Collective ID. Get this from collectives_list().
        page_id: Page ID. Get this from collectives_list_pages(collective_id).
        tag_id: Tag ID. Get this from collectives_list_tags(collective_id).
    """
    return await get_client().collectives_remove_tag(collective_id, page_id, tag_id, get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def circle_list(
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all Circles (Teams).
    Args:
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().circles_list(get_user_token(), include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def circle_get(
    circle_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Get a Circle by ID.
    Args:
        circle_id: Circle ID.
    """
    return await get_client().circles_get(circle_id, get_user_token())


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def circle_create(
    name: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Create a new Circle (Team).
    Args:
        name: Circle name.
    """
    return await get_client().circles_create(name, circle_type=1, api_key=get_user_token(), include_all_fields=ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def circle_update(
    circle_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    ctx: Context = None
) -> dict[str, Any]:
    """Update a Circle's properties.
    Args:
        circle_id: Circle ID.
        name: New name for the circle.
        description: New description.
    """
    return await get_client().circles_update(circle_id, name=name, description=description, api_key=get_user_token(), include_all_fields=ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def circle_delete(
    circle_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Delete a Circle (Team) by ID.
    Args:
        circle_id: Circle ID to delete. Get this from list_circles().
    """
    result = await get_client().circles_delete(circle_id, get_user_token())
    return {"status": "deleted", "result": result}


@mcp.tool(tags={"write", "primary", "nextcloud"})
async def circle_add_member(
    circle_id: str,
    user_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Add a user to a Circle (Team).
    Args:
        circle_id: Circle ID. Get this from circle_list().
        user_id: User ID to add to the circle.
    """
    result = await get_client().circles_add_member(circle_id, user_id, api_key=get_user_token(), include_all_fields=ALLOW_ALL_AGGREGATE)
    return {"success": True, "data": result}


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def circle_remove_member(
    circle_id: str,
    member_id: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Remove a member from a Circle (Team).
    Args:
        circle_id: Circle ID. Get this from circle_list().
        member_id: Member ID to remove. Get this from circle_list_members().
    """
    result = await get_client().circles_remove_member(circle_id, member_id, get_user_token())
    return {"success": True, "data": result}


@mcp.tool(tags={"read", "primary", "nextcloud"})
async def circle_list_members(
    circle_id: str,
    include_all_fields: bool = False,
    ctx: Context = None
) -> dict[str, Any]:
    """List all members of a Circle (Team).
    Args:
        circle_id: Circle ID. Get this from circle_list().
        include_all_fields: Default False (common fields only). Set True for all fields.
    """
    data = await get_client().circles_list_members(circle_id, get_user_token(),
                                                     include_all_fields if ALLOW_ALL_AGGREGATE else False)
    return {"items": json_to_toon(data)}


@mcp.tool(tags={"write", "advanced", "nextcloud"})
async def circle_update_member_level(
    circle_id: str,
    member_id: str,
    level: str,
    ctx: Context = None
) -> dict[str, Any]:
    """Change a member's level in a Circle (Team).
    Args:
        circle_id: Circle ID. Get this from circle_list().
        member_id: Member ID. Get this from circle_list_members().
        level: Member, Moderator, Admin, or Owner.
    """
    level_map = {"member": 1, "moderator": 4, "admin": 8, "owner": 9}
    return await get_client().circles_update_member_level(circle_id, member_id, level_map[level],
                                                           get_user_token(), ALLOW_ALL_AGGREGATE)


@mcp.tool(tags={"read", "advanced", "nextcloud"})
async def check_server_status(ctx: Context) -> dict[str, Any]:
    """Check connectivity to the Nextcloud backend."""
    try:
        data = await get_client().get_capabilities(get_user_token())
        return {"status": "connected", "data": data}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)}


# =============================================================================
# Entry Point
# =============================================================================

def main():
    if not os.getenv("NEXTCLOUD_BASE_URL"):
        print("ERROR: NEXTCLOUD_BASE_URL environment variable is required", file=sys.stderr)
        print("Example: export NEXTCLOUD_BASE_URL=http://nextcloud-web:80", file=sys.stderr)
        sys.exit(1)

    port_env = os.getenv("MCP_SERVER_PORT")
    if not port_env:
        print("ERROR: MCP_SERVER_PORT environment variable is required", file=sys.stderr)
        print("Example: export MCP_SERVER_PORT=6080", file=sys.stderr)
        sys.exit(1)

    host = "0.0.0.0"
    port = int(port_env)
    path = "/mcp"
    if IS_STATEFUL:
        app = mcp.http_app(path=path, json_response=True)
    else:
        app = mcp.http_app(path=path, json_response=True, stateless_http=True)
    app = AuthMiddleware(app)
    print(f"Starting Nextcloud MCP server on http://{host}:{port}{path}")
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
