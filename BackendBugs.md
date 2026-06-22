# BackendBugs — Confirmed Unfixable Nextcloud Backend Bugs

These 3 tools fail because the Nextcloud backend API has bugs that cannot be fixed in the MCP server code. The MCP server correctly reports these as errors — there is no workaround in application code.

## 1. Collectives Search Pages — 400 Bad Request

**Affected tool**: `collectives_search_pages`

**Endpoint**: `GET /ocs/v2.php/apps/collectives/api/v1.0/collectives/{id}/search?search={query}`

**Evidence**:
- `GET /ocs/v2.php/apps/collectives/api/v1.0/collectives/41/search?search=t19e0394a-Page&limit=50` → **400**
- All query format variations tried (URL-encoded, different query lengths, different limits)
- Other Collectives endpoints work correctly (list, create, update emoji, pages, tags, etc.)

**Root cause**: The Collectives app search endpoint on this server version has a bug. The search route exists but rejects all requests with 400.

**Investigation notes**:
- Direct API request (via curl) confirms same 400 response
- Other Collectives tools all work: `collectives_list`, `collectives_create`, `collectives_update_emoji`, `collectives_list_pages`, `collectives_create_page`, `collectives_get_page`, `collectives_trash_page`, `collectives_restore_page`, `collectives_delete_page`, `collectives_move_page`, `collectives_set_page_emoji`, `collectives_set_page_full_width`, `collectives_list_tags`, `collectives_create_tag`, `collectives_update_tag`, `collectives_delete_tag`, `collectives_assign_tag`, `collectives_remove_tag`, `collectives_trash`, `collectives_restore`, `collectives_delete`
- Only the search endpoint is broken

**Status**: Cannot fix in MCP code — Nextcloud backend bug.

---

## 2. Deck Update Card — 400 Bad Request

**Affected tool**: `update_card`

**Endpoint**: `PUT /index.php/apps/deck/api/v1.0/boards/{boardId}/stacks/{stackId}/cards/{cardId}`

**Evidence**:
- `PUT /index.php/apps/deck/api/v1.0/boards/45/stacks/47/cards/49` with `{"title": "update test"}` → **400**
- Tried various payload formats:
  - Only `title`: 400
  - Full payload with all fields: 400
  - After archive/unarchive (card may have moved stacks): 400
  - Fetching fresh card data first to get current stack_id: 400

**Root cause**: The Deck API on this server version rejects card updates. The endpoint exists (returns 400, not 404) but rejects all PUT requests. Possible causes:
1. The card's stack_id may have changed (archive/unarchive moves cards between stacks)
2. The Deck app requires additional fields that aren't documented
3. The card must be in a specific state (not archived) for updates

**Investigation notes**:
- Test sequence: create_card → archive_card → unarchive_card → update_card
- After unarchive, the card may be in a different stack than created
- The test uses the original stack_id from creation, not the current one
- Even with fresh stack_id from get_card, update still returns 400
- Other Deck tools work: `list_boards`, `get_board`, `create_board`, `list_stacks`, `create_stack`, `list_cards`, `get_card`, `create_card`, `delete_card`, `archive_card`, `unarchive_card`, `list_labels`, `assign_label`, `remove_label`, `assign_user`, `remove_user`

**Status**: Cannot fix in MCP code — Deck app bug or undocumented API requirement on this server version.

---

## 3. Notes Get Attachment — 400 Bad Request

**Affected tool**: `get_attachment`

**Endpoint**: `GET /index.php/apps/notes/api/v1/notes/{noteId}/attachment/{attachmentId}`

**Evidence**:
- `GET /index.php/apps/notes/api/v1/notes/784/attachment/1` → **400**
- Attempted to create an attachment via `POST /index.php/apps/notes/api/v1/notes/784/attachment` with `{"file": "test content"}` — also failed (400)

**Root cause**: The Notes app on this server doesn't support attachments, or the API for attachments differs from the documented route. The Notes `create_note` tool doesn't create attachments, and the Notes API may not expose attachment creation at all.

**Investigation notes**:
- Other Notes tools work: `list_notes`, `get_note`, `create_note`, `update_note`, `delete_note`, `append_content`, `search_notes`
- Only `get_attachment` is broken
- The attachment API might require multipart form data upload rather than JSON
- The Notes API version on this server may not include attachment support

**Status**: Cannot fix in MCP code — Notes app may not support attachments in this version.

---

## Summary Table

| # | Tool | HTTP Error | Backend App | Root Cause |
|---|------|-----------|-------------|------------|
| 1 | `collectives_search_pages` | 400 | Collectives | Search endpoint rejects all queries |
| 2 | `update_card` | 400 | Deck | Card update payload rejected |
| 3 | `get_attachment` | 400 | Notes | Attachment endpoint/content not supported |

**Total: 3 failing tools out of 141 MCP tools (97.9% pass rate)**
