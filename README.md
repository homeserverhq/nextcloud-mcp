# Nextcloud MCP Multi-Tenant Proxy Server

This repository contains a Model Context Protocol (MCP) server that acts as a secure, multi-tenant proxy between an AI Assistant and the Nextcloud backend API. It exposes **185 MCP tools** covering 22 resource domains with full CRUD, collaboration, and data management capabilities.

## ✨ Features

- **🔑 Identity Passthrough** — Extracts the `Authorization: Bearer <token>` header from incoming HTTP requests and forwards it to the Nextcloud API without server-side authentication.
- **👥 Multi-Tenancy** — Uses Python `contextvars` to maintain thread-safe user identity isolation, ensuring all AI-driven actions are scoped to the authenticated user's permissions.
- **📊 Full Nextcloud Coverage** — 185 tools mapped to Nextcloud API endpoints across 22 resource domains.
- **⚡ TOON Optimization** — Bulk list responses are automatically compressed using TOON (Token-Optimized Object Notation) to reduce token consumption and maximize context window efficiency.
- **🚀 Efficient Gets** — GET responses return only commonly used fields by default. Full objects are available via an `include_all_fields` flag.
- **🧪 Comprehensive Testing** — 270 automated tests covering all tool domains, run via the test runner pipeline.
- **🏷️ Tool Annotations** — All tools expose standard MCP ToolAnnotations hints (readOnlyHint, destructiveHint, idempotentHint, openWorldHint). The tags field carries grouping metadata (basic/primary/advanced + nextcloud).

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
- `list_full_files_folders` — Complete overview of every file and folder in Nextcloud
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
- `get_collection_by_id` — Get a collection by ID
- `search_collections` — Search across collections
- `search_collections_raw` — Search collections with raw query
- `list_collections_by_resource` — Get collections for a resource
- `create_collection` — Create a new collection

### ⏰ File Reminders (3 tools)
- `get_file_reminder` — Get a file reminder
- `set_file_reminder` — Set a file reminder
- `delete_file_reminder` — Delete a file reminder

### 🔍 Search (2 tools)
- `list_search_providers` — List search providers
- `search` — Search across providers

### 🔔 Notifications (4 tools)
- `list_notifications` — List all notifications
- `get_notification` — Get a notification by ID
- `delete_notification` — Delete a notification by ID
- `delete_all_notifications` — Delete all notifications

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
- `list_todos` — List all todos across calendars
- `list_todos_by_calendar` — List todos in a calendar
- `get_todo` — Get a todo by UID
- `create_todo` — Create a new todo
- `update_todo` — Update an existing todo
- `delete_todo` — Delete a todo by UID
- `search_todos` — Search across todos

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
- `list_notes` — List all notes
- `list_notes_by_category` — List notes by category
- `get_note` — Get a note by ID
- `create_note` — Create a new note
- `update_note` — Update an existing note
- `delete_note` — Delete a note by ID
- `append_note_content` — Append content to a note
- `search_notes` — Search notes by content
- `get_note_attachment` — Get a note attachment

### 🎯 Deck (22 tools)
- `list_deck_boards` — List all boards
- `get_deck_board` — Get a board by ID
- `create_deck_board` — Create a new board
- `update_deck_board` — Update an existing board
- `delete_deck_board` — Delete a board by ID
- `list_deck_stacks` — List stacks in a board
- `get_deck_stack` — Get a stack by ID
- `create_deck_stack` — Create a new stack
- `update_deck_stack` — Update an existing stack
- `delete_deck_stack` — Delete a stack by ID
- `list_deck_cards` — List cards in a stack
- `get_deck_card` — Get a card by ID
- `create_deck_card` — Create a new card
- `update_deck_card` — Update an existing card
- `delete_deck_card` — Delete a card by ID
- `archive_deck_card` — Archive a card
- `unarchive_deck_card` — Unarchive a card
- `list_deck_labels` — List labels in a board
- `assign_deck_label` — Assign a label to a card
- `remove_deck_label` — Remove a label from a card
- `assign_deck_user` — Assign a user to a card
- `remove_deck_user` — Remove a user from a card

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
- `list_talk_rooms` — List all Talk conversations
- `create_talk_room` — Create a new Talk conversation room
- `update_talk_room` — Rename a Talk conversation room
- `set_talk_description` — Set the description of a Talk conversation room
- `list_talk_participants` — List participants in a Talk conversation room
- `add_talk_participant` — Add a participant to a Talk conversation room
- `remove_talk_participant` — Remove a participant from a Talk conversation room
- `delete_talk_room` — Delete a Talk conversation room
- `send_chat_message` — Send a chat message
- `list_chat_messages` — Get chat messages from a room
- `delete_chat_message` — Delete a chat message

### 📊 Tables (11 tools)
- `list_tables` — List all tables
- `get_table` — Get a table by ID
- `create_table` — Create a new table
- `update_table` — Update an existing table
- `delete_table` — Delete a table by ID
- `get_table_schema` — Get the schema of a table
- `list_table_rows` — List rows in a table
- `get_table_row` — Get a row by ID
- `create_table_row` — Create a new row
- `update_table_row` — Update an existing row
- `delete_table_row` — Delete a row by ID

### 📚 Collectives (27 tools)
- `list_collectives` — List all collectives
- `get_collective` — Get a collective by ID
- `create_collective` — Create a new collective
- `update_collective` — Update a collective
- `update_collective_emoji` — Update the emoji of a collective
- `trash_collective` — Trash a collective
- `restore_collective` — Restore a trashed collective
- `delete_collective` — Permanently delete a collective
- `list_trashed_collectives` — List trashed collectives
- `list_collective_pages` — List pages in a collective
- `get_collective_page` — Get a page by ID
- `create_collective_page` — Create a new page
- `update_collective_page` — Update a page
- `trash_collective_page` — Trash a page
- `restore_collective_page` — Restore a trashed page
- `delete_collective_page` — Permanently delete a page
- `move_collective_page` — Move a page to a new parent
- `set_collective_page_emoji` — Set the emoji of a page
- `set_collective_page_full_width` — Toggle full-width mode on a page
- `search_collective_pages` — Search pages in a collective
- `list_collective_tags` — List tags in a collective
- `get_collective_tag` — Get a tag by ID
- `create_collective_tag` — Create a new tag
- `update_collective_tag` — Update a tag
- `delete_collective_tag` — Delete a tag by ID
- `assign_collective_tag` — Assign a tag to a page
- `remove_collective_tag` — Remove a tag from a page

### 🔵 Circles (9 tools)
- `list_circles` — List all circles
- `get_circle` — Get a circle by ID
- `create_circle` — Create a new circle
- `update_circle` — Update an existing circle
- `delete_circle` — Delete a circle by ID
- `add_circle_member` — Add a user to a circle
- `remove_circle_member` — Remove a member from a circle
- `list_circle_members` — List members of a circle
- `update_circle_member_level` — Update a member's level in a circle
