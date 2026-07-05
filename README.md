# Nextcloud MCP Multi-Tenant Proxy Server

This repository contains a Model Context Protocol (MCP) server that acts as a secure, multi-tenant proxy between an AI Assistant and the Nextcloud backend API. It exposes **185 MCP tools** covering 22 resource domains with full CRUD, collaboration, and data management capabilities.

## ✨ Features

- **🔑 Identity Passthrough** — Extracts the `Authorization: Bearer <token>` header from incoming HTTP requests and forwards it to the Nextcloud API without server-side authentication.
- **👥 Multi-Tenancy** — Uses Python `contextvars` to maintain thread-safe user identity isolation, ensuring all AI-driven actions are scoped to the authenticated user's permissions.
- **📊 Full Nextcloud Coverage** — 185 tools mapped to Nextcloud API endpoints across 22 resource domains.
- **⚡ TOON Optimization** — Bulk list responses are automatically compressed using TOON (Token-Optimized Object Notation) to reduce token consumption and maximize context window efficiency.
- **🚀 Efficient Gets** — GET responses return only commonly used fields by default. Full objects are available via an `include_all_fields` flag.
- **🧪 Comprehensive Testing** — 280 automated tests covering all tool domains, run via the test runner pipeline.

## 🔧 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXTCLOUD_BASE_URL` | Yes | Docker-internal URL of the Nextcloud API (e.g. `http://nextcloud-web:80`) |
| `MCP_SERVER_PORT` | Yes | Port number the MCP server listens on |
| `ALLOW_ALL_AGGREGATE` | No | When `true`, aggregate listing tools honor the `include_all_fields` parameter. When `false` (default), the parameter is silently forced to `False` for aggregate list operations. |
| `IS_STATEFUL` | No | When `true`, uses stateful Streamable HTTP with session tracking. When `false` (default), uses stateless mode. |

## 📦 Installation & Local Development

1. Ensure you have Python 3.12+ installed.
2. Install dependencies:
    ```bash
    pip install fastmcp httpx pydantic uvicorn toon-mcp-server icalendar vobject
    ```
3. Run the server:
    ```bash
    export NEXTCLOUD_BASE_URL=http://nextcloud-web:80
    export MCP_SERVER_PORT=80
    python -m src.main
    ```

## 🐳 Docker Deployment

Build and run the server using Docker:

```bash
docker build -t nextcloud-mcp:latest .
docker run -d --name nextcloud-mcp \
    -e NEXTCLOUD_BASE_URL="http://nextcloud-web:80" \
    -e MCP_SERVER_PORT=80 \
    nextcloud-mcp:latest
```

The MCP server serves at `http://nextcloud-mcp:80/mcp` (Streamable HTTP).

## ⚠️ Important Notes

- **🔑 Auth** — Uses HTTP Basic Auth. The API key is a base64-encoded `username:app-password` string (e.g. `echo -n "username:app-password" | base64`).
- **📋 `include_all_fields`** — The `include_all_fields` parameter (available on all `get_*` and `list_*` tools) controls whether all available fields are included in responses. Defaults to `False` for performance; set to `True` only when additional fields are needed.
- **🔒 `ALLOW_ALL_AGGREGATE`** — Controls whether aggregate listing tools respect the `include_all_fields` parameter. When set to `false` (default), all aggregate list operations silently return only default fields regardless of the caller's request.
- **⚡ TOON Compression** — All bulk list responses are automatically compressed using TOON (Token-Optimized Object Notation) to reduce token consumption by 30-60%.
- **📝 Required Fields & Defaults** — Each `create_*` tool requires specific key fields (e.g. `name` for notes). All other fields default to empty strings or reasonable values.

## 🛠️ API Tool Mapping

The server implements 185 MCP tools organized into the following categories:

### ✅ Status & Capabilities (2 tools)
- `check_server_status` — Health check endpoint for backend connectivity
- `get_capabilities` — Get server capabilities

### 👥 Users/Groups (4 tools)
- `list_users` — List all users
- `get_user` — Get a user by ID
- `list_groups` — List all groups
- `list_group_members` — Get members of a group

### 🔗 File Sharing (6 tools)
- `list_all_shares` — List all shares
- `list_shares_by_path` — Get shares by file path
- `get_share` — Get a share by ID
- `create_share` — Create a new share
- `update_share` — Update an existing share
- `delete_share` — Delete a share by ID

### 🟢 User Status (7 tools)
- `get_user_status` — Get the current user's status
- `set_status` — Set the current user's status
- `set_predefined_message` — Set a predefined status message
- `set_custom_message` — Set a custom status message
- `clear_message` — Clear the current status message
- `list_predefined_statuses` — List predefined statuses
- `list_user_statuses` — List all user statuses

### 📅 OOO/Events (5 tools)
- `get_ooo` — Get out-of-office settings for a user
- `set_ooo` — Set out-of-office settings
- `clear_ooo` — Clear out-of-office settings
- `list_upcoming_events` — Get upcoming events from calendars
- `get_direct_download_url` — Get a direct download URL for a file

### 📁 File/Folder Operations (14 tools)
- `get_folder_tree` — Get the folder tree structure
- `write_file` — Create or overwrite a file
- `read_file` — Read file content
- `search_files` — Search files by name
- `move_file` — Move or rename a file
- `copy_file` — Copy a file
- `delete_file` — Delete a file (errors if it's a folder)
- `create_folder` — Create a directory
- `show_folder` — Show contents of a folder
- `move_folder` — Move or rename a folder
- `copy_folder` — Copy a folder
- `delete_folder` — Delete a folder (errors if it's a file)
- `move_files` — Move multiple files into a target directory
- `list_favorites` — List favorite files

### 🗂️ Collections (5 tools)
- `collection_get_by_id` — Get a collection by ID
- `collection_search` — Search across collections
- `collection_search_raw` — Search collections with raw query
- `list_collections_by_resource` — Get collections for a resource
- `collection_create` — Create a new collection

### ⏰ File Reminders (3 tools)
- `get_file_reminder` — Get a file reminder
- `set_file_reminder` — Set a file reminder
- `delete_file_reminder` — Delete a file reminder

### 🔍 Search (2 tools)
- `list_search_providers` — List search providers
- `search` — Search across providers

### 🔔 Notifications (4 tools)
- `notification_list` — List all notifications
- `notification_get` — Get a notification by ID
- `notification_delete` — Delete a notification by ID
- `notification_delete_all` — Delete all notifications

### 📊 Activity (3 tools)
- `list_activities` — Get activity stream
- `list_activity_filters` — List activity filters
- `list_activity_filtered` — Get activity by filter

### 📅 Calendar (13 tools)
- `list_all_calendars` — List all calendars
- `get_calendar` — Get a calendar by URI
- `create_calendar` — Create a new calendar
- `update_calendar` — Update an existing calendar
- `delete_calendar` — Delete a calendar by URI
- `list_all_events` — List all events across calendars
- `list_events_by_calendar` — List events in a calendar
- `get_event` — Get an event by UID
- `create_event` — Create a new event
- `update_event` — Update an existing event
- `delete_event` — Delete an event by UID
- `create_meeting` — Create a meeting with attendees
- `find_availability` — Find available time slots

### ✅ Todos (7 tools)
- `todo_list` — List all todos across calendars
- `list_todos_by_calendar` — List todos in a calendar
- `todo_get` — Get a todo by UID
- `todo_create` — Create a new todo
- `todo_update` — Update an existing todo
- `todo_delete` — Delete a todo by UID
- `todo_search` — Search across todos

### 👤 Contacts (11 tools)
- `list_all_addressbooks` — List all address books
- `get_addressbook` — Get an address book by URI
- `create_addressbook` — Create a new address book
- `update_addressbook` — Update an existing address book
- `delete_addressbook` — Delete an address book by URI
- `list_all_contacts` — List all contacts across address books
- `list_contacts_by_addressbook` — List contacts in an address book
- `get_contact` — Get a contact by UID
- `create_contact` — Create a new contact
- `update_contact` — Update an existing contact
- `delete_contact` — Delete a contact by UID

### 📝 Notes (9 tools)
- `note_list` — List all notes
- `list_notes_by_category` — List notes by category
- `note_get` — Get a note by ID
- `note_create` — Create a new note
- `note_update` — Update an existing note
- `note_delete` — Delete a note by ID
- `note_append_content` — Append content to a note
- `note_search` — Search notes by content
- `note_get_attachment` — Get a note attachment

### 🎯 Deck (22 tools)
- `deck_list` — List all boards
- `deck_get_board` — Get a board by ID
- `deck_create_board` — Create a new board
- `deck_update_board` — Update an existing board
- `deck_delete_board` — Delete a board by ID
- `deck_list_stacks` — List stacks in a board
- `deck_get_stack` — Get a stack by ID
- `deck_create_stack` — Create a new stack
- `deck_update_stack` — Update an existing stack
- `deck_delete_stack` — Delete a stack by ID
- `deck_list_cards` — List cards in a stack
- `deck_get_card` — Get a card by ID
- `deck_create_card` — Create a new card
- `deck_update_card` — Update an existing card
- `deck_delete_card` — Delete a card by ID
- `deck_archive_card` — Archive a card
- `deck_unarchive_card` — Unarchive a card
- `deck_list_labels` — List labels in a board
- `deck_assign_label` — Assign a label to a card
- `deck_remove_label` — Remove a label from a card
- `deck_assign_user` — Assign a user to a card
- `deck_remove_user` — Remove a user from a card

### 📧 Mail (8 tools)
- `list_all_accounts` — List all mail accounts
- `list_mailboxes` — List mailboxes in an account
- `list_messages` — List messages in a mailbox
- `get_message` — Get a message by ID
- `get_message_body` — Get the body of a message
- `send_message` — Send a new message
- `set_message_flags` — Set message flags
- `move_message` — Move a message to another mailbox

### 💬 Talk (11 tools)
- `talk_list_rooms` — List all Talk conversations
- `talk_create_room` — Create a new Talk conversation room
- `talk_update_room` — Rename a Talk conversation room
- `talk_set_description` — Set the description of a Talk conversation room
- `talk_list_participants` — List participants in a Talk conversation room
- `talk_add_participant` — Add a participant to a Talk conversation room
- `talk_remove_participant` — Remove a participant from a Talk conversation room
- `talk_delete_room` — Delete a Talk conversation room
- `send_chat_message` — Send a chat message
- `list_chat_messages` — Get chat messages from a room
- `delete_chat_message` — Delete a chat message

### 📊 Tables (11 tools)
- `table_list` — List all tables
- `table_get` — Get a table by ID
- `table_create` — Create a new table
- `table_update` — Update an existing table
- `table_delete` — Delete a table by ID
- `table_get_schema` — Get the schema of a table
- `table_list_rows` — List rows in a table
- `table_get_row` — Get a row by ID
- `table_create_row` — Create a new row
- `table_update_row` — Update an existing row
- `table_delete_row` — Delete a row by ID

### 📚 Collectives (27 tools)
- `collectives_list` — List all collectives
- `collectives_get_collective` — Get a collective by ID
- `collectives_create` — Create a new collective
- `collectives_update_collective` — Update a collective
- `collectives_update_emoji` — Update the emoji of a collective
- `collectives_trash` — Trash a collective
- `collectives_restore` — Restore a trashed collective
- `collectives_delete` — Permanently delete a collective
- `collectives_list_trashed` — List trashed collectives
- `collectives_list_pages` — List pages in a collective
- `collectives_get_page` — Get a page by ID
- `collectives_create_page` — Create a new page
- `collectives_update_page` — Update a page
- `collectives_trash_page` — Trash a page
- `collectives_restore_page` — Restore a trashed page
- `collectives_delete_page` — Permanently delete a page
- `collectives_move_page` — Move a page to a new parent
- `collectives_set_page_emoji` — Set the emoji of a page
- `collectives_set_page_full_width` — Toggle full-width mode on a page
- `collectives_search_pages` — Search pages in a collective
- `collectives_list_tags` — List tags in a collective
- `collectives_get_tag` — Get a tag by ID
- `collectives_create_tag` — Create a new tag
- `collectives_update_tag` — Update a tag
- `collectives_delete_tag` — Delete a tag by ID
- `collectives_assign_tag` — Assign a tag to a page
- `collectives_remove_tag` — Remove a tag from a page

### 🔵 Circles (9 tools)
- `circle_list` — List all circles
- `circle_get` — Get a circle by ID
- `circle_create` — Create a new circle
- `circle_update` — Update an existing circle
- `circle_delete` — Delete a circle by ID
- `circle_add_member` — Add a user to a circle
- `circle_remove_member` — Remove a member from a circle
- `circle_list_members` — List members of a circle
- `circle_update_member_level` — Update a member's level in a circle
