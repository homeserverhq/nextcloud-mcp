# Nextcloud MCP Multitenant Proxy Server

This repository contains a Model Context Protocol (MCP) server that acts
as a secure, multi-tenant proxy between an AI Assistant and the
Nextcloud backend API. It exposes **136 MCP tools** covering
21 resource domains with full CRUD, search, and data management.

## Features

- **Identity Passthrough** — Extracts the `Authorization: Bearer <token>`
  header from incoming HTTP requests and forwards it to the Nextcloud
  API without server-side authentication.
- **Multi-Tenancy** — Uses Python `contextvars` to maintain thread-safe
  user identity isolation.
- **Full Nextcloud Coverage** — 136 tools mapped to Nextcloud
  API endpoints across 21 resource domains.
- **TOON Optimization** — Bulk list responses are automatically compressed
  using TOON (Token-Optimized Object Notation).
- **Efficient Gets** — GET responses return only commonly used fields by
  default. Full objects are available via an `include_all_fields` flag.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXTCLOUD_BASE_URL` | Yes | Docker-internal URL of the Nextcloud API (e.g. `http://nextcloud-web:80`) |
| `MCP_SERVER_PORT` | Yes | Port number the MCP server listens on |

## Installation & Local Development

1. Ensure you have Python 3.12+ installed.
2. Install dependencies:
    ```bash
    pip install fastmcp httpx pydantic uvicorn toon-mcp-server icalendar vobject
    ```
3. Run the server:
    ```bash
    export NEXTCLOUD_BASE_URL=http://localhost:8060
    export MCP_SERVER_PORT=6080
    python -m src.main
    ```

## Docker Deployment

```bash
docker build -t nextcloud-mcp:latest .
docker run -d --name nextcloud-mcp \
    -e NEXTCLOUD_BASE_URL="http://nextcloud-web:80" \
    -e MCP_SERVER_PORT=80 \
    -p 6080:80 \
    nextcloud-mcp:latest
```

The MCP server serves at `http://nextcloud-mcp:80/mcp` (Streamable HTTP).

## Important Notes

- **Auth**: Uses HTTP Basic Auth. The API key is a base64-encoded `username:app-password` string.
- **`include_all_fields`** — The `include_all_fields` parameter (available on all `get_*` and `list_*` tools) controls whether all available fields are included in responses. Defaults to `False` for performance.
- **TOON Compression** — All bulk list responses are automatically compressed using TOON (Token-Optimized Object Notation).
- **Required Fields & Defaults** — Each `create_*` tool requires specific key fields. All other fields default to empty strings or reasonable values.

## API Tool Mapping

The server implements 136 MCP tools organized into the following categories:

### Users/Groups (4 tools)
- `get_users`, `get_user`, `get_groups`, `get_group_members`

### File Sharing (5 tools)
- `list_shares`, `get_share`, `create_share`, `update_share`, `delete_share`

### User Status (7 tools)
- `get_user_status`, `set_status`, `set_predefined_message`, `set_custom_message`, `clear_message`, `get_predefined_statuses`, `get_user_statuses`

### DAV Events/OOO (5 tools)
- `get_ooo`, `set_ooo`, `clear_ooo`, `get_upcoming_events`, `get_direct_download_url`

### Folder Tree (1 tool)
- `get_folder_tree`

### Collections (3 tools)
- `list_collection`, `search_collections`, `get_collections_by_resource`

### Files Reminders (3 tools)
- `get_file_reminder`, `set_file_reminder`, `delete_file_reminder`

### Search (2 tools)
- `get_search_providers`, `search`

### Capabilities (1 tool)
- `get_capabilities`

### WebDAV Files (9 tools)
- `webdav_list_directory`, `webdav_read_file`, `webdav_write_file`, `webdav_create_directory`, `webdav_delete_resource`, `webdav_move_resource`, `webdav_copy_resource`, `webdav_search_files`, `webdav_list_favorites`

### Notifications (4 tools)
- `list_notifications`, `get_notification`, `delete_notification`, `delete_all_notifications`

### Activity (3 tools)
- `get_activity`, `get_activity_filters`, `get_activity_filter`

### Calendar (11 tools)
- `list_calendars`, `create_calendar`, `update_calendar`, `delete_calendar`, `list_events`, `get_event`, `create_event`, `update_event`, `delete_event`, `create_meeting`, `find_availability`

### Todos (5 tools)
- `list_todos`, `create_todo`, `update_todo`, `delete_todo`, `search_todos`

### Contacts (8 tools)
- `list_addressbooks`, `create_addressbook`, `delete_addressbook`, `list_contacts`, `get_contact`, `create_contact`, `update_contact`, `delete_contact`

### Notes (8 tools)
- `list_notes`, `get_note`, `create_note`, `update_note`, `delete_note`, `append_content`, `search_notes`, `get_attachment`

### Deck (17 tools)
- `list_boards`, `get_board`, `create_board`, `list_stacks`, `create_stack`, `list_cards`, `get_card`, `create_card`, `update_card`, `delete_card`, `archive_card`, `unarchive_card`, `list_labels`, `assign_label`, `remove_label`, `assign_user`, `remove_user`

### Mail (8 tools)
- `list_accounts`, `list_mailboxes`, `list_messages`, `get_message`, `get_message_body`, `send_message`, `set_message_flags`, `move_message`

### Talk (3 tools)
- `send_chat_message`, `get_chat_messages`, `delete_chat_message`

### Tables (10 tools)
- `list_tables`, `get_table`, `create_table`, `update_table`, `delete_table`, `get_schema`, `list_rows`, `create_row`, `update_row`, `delete_row`

### Collectives (22 tools)
- `collectives_list`, `collectives_create`, `collectives_update_emoji`, `collectives_trash`, `collectives_restore`, `collectives_delete`, `collectives_list_trashed`, `collectives_list_pages`, `collectives_get_page`, `collectives_create_page`, `collectives_trash_page`, `collectives_restore_page`, `collectives_delete_page`, `collectives_move_page`, `collectives_set_page_emoji`, `collectives_search_pages`, `collectives_list_tags`, `collectives_create_tag`, `collectives_update_tag`, `collectives_delete_tag`, `collectives_assign_tag`, `collectives_remove_tag`
