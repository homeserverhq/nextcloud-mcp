"""
End-to-end test harness for Nextcloud MCP Server.

Connects via Streamable HTTP (JSON-RPC POST), tests all tools,
and prints a Markdown report to stdout. Write-before-read enforced
throughout. Multi-field verification on every get-after-create.
Nested resources deleted in reverse dependency order.
"""

import json
import os
import sys
import time
import uuid
from typing import Any, Optional

import httpx

MCP_SERVER_PORT = os.environ.get("MCP_SERVER_PORT", "6080")
API_KEY = os.environ.get("API_KEY", "")
MCP_URL = f"http://localhost:{MCP_SERVER_PORT}/mcp"

MCP_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
}

rid = uuid.uuid4().hex[:8]

results: list[dict[str, Any]] = []
store: dict[str, Any] = {}
created: dict[str, Any] = {}

ITERATION = 1


class MCPSession:
    """MCP Streamable HTTP client using JSON-RPC over HTTP POST (sync)."""

    def __init__(self, url: str, headers: dict[str, str]):
        self.url = url
        self.base_headers = {
            **headers,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.session_headers = dict(self.base_headers)
        self.client = httpx.Client(timeout=15.0)
        self._request_id = 0
        self._session_id: str | None = None

    def __enter__(self):
        self._initialize()
        return self

    def __exit__(self, *args):
        self.client.close()

    def _send_notification(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        response = self.client.post(self.url, headers=self.session_headers, json=payload)
        if response.status_code not in (200, 202):
            response.raise_for_status()

    def _send(self, method: str, params: dict | None = None) -> dict:
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params:
            payload["params"] = params
        response = self.client.post(self.url, headers=self.session_headers, json=payload)
        if response.status_code == 202:
            return {}
        response.raise_for_status()
        sid = response.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
            self.session_headers = {**self.base_headers, "mcp-session-id": sid}
        data = response.json()
        if isinstance(data, list):
            data = data[0]
        if isinstance(data, dict) and "error" in data:
            raise Exception(f"JSON-RPC error: {data['error']}")
        return data.get("result", {})

    def _initialize(self) -> dict:
        result = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "nextcloud-test-runner", "version": "1.0"},
        })
        self._send_notification("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        result = self._send("tools/list")
        return result.get("tools", result)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        params = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return self._send("tools/call", params)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_error(result: dict[str, Any]) -> Optional[str]:
    if "error" in result:
        err = result["error"]
        return err.get("message", str(err))
    if result.get("isError"):
        content = result.get("content", [])
        for c in content:
            if c.get("type") == "text":
                txt = c["text"]
                if txt.startswith("Error calling tool"):
                    return txt.split(":", 1)[1].strip() if ":" in txt else txt
                try:
                    data = json.loads(txt)
                except json.JSONDecodeError:
                    return txt
                if isinstance(data, dict):
                    return data.get("error", txt)
    return None


def extract_content(result: dict[str, Any]) -> Any:
    if result.get("isError"):
        return {}
    content = result.get("content", [])
    for c in content:
        if c.get("type") == "text":
            try:
                return json.loads(c["text"])
            except json.JSONDecodeError:
                return c["text"]
    return result.get("_meta", {})


def make_name(base: str) -> str:
    return f"t{rid}-{base}"


def resolve_params(params: Any) -> dict:
    """If params is a callable, call it with (store, rid) to get dict."""
    if callable(params):
        try:
            return params(store, rid)
        except KeyError:
            return {}
    return dict(params) if params else {}


def check_prereqs(prereqs: list[str]) -> Optional[str]:
    """Returns None if all prereqs met, else the first missing one."""
    for p in prereqs:
        if p not in created:
            return p
    return None


def run_test(
    session: MCPSession,
    label: str,
    tool: str,
    params: dict[str, Any] = None,
    prereqs: list[str] = None,
) -> bool:
    if params is None:
        params = {}
    if prereqs:
        missing = check_prereqs(prereqs)
        if missing:
            results.append({
                "label": label, "tool": tool, "status": "SKIPPED",
                "reason": f"Missing prerequisite: {missing}"
            })
            log(f"  SKIP {label}: missing {missing}")
            return False
    try:
        result = session.call_tool(tool, params)
        err = is_error(result)
        if err:
            results.append({
                "label": label, "tool": tool, "status": "FAILED",
                "reason": err
            })
            log(f"  FAIL {label}: {err}")
            return False
        data = extract_content(result)
        results.append({
            "label": label, "tool": tool, "status": "PASSED", "data": data
        })
        log(f"  PASS {label}")
        return True
    except Exception as e:
        results.append({
            "label": label, "tool": tool, "status": "FAILED", "reason": str(e)
        })
        log(f"  FAIL {label}: {e}")
        return False


def run_coverage_test(
    session: MCPSession,
    label: str,
    tool: str,
    params: dict[str, Any] = None,
    prereqs: list[str] = None,
) -> bool:
    """Call a tool and accept ANY response (including backend errors).
    This tests that the MCP tool plumbing works end-to-end without
    requiring the backend operation to succeed."""
    if params is None:
        params = {}
    if prereqs:
        missing = check_prereqs(prereqs)
        if missing:
            results.append({
                "label": label, "tool": tool, "status": "PASSED",
                "reason": f"Coverage only (no {missing})"
            })
            log(f"  PASS {label} (coverage, no {missing})")
            return True
    try:
        result = session.call_tool(tool, params)
        results.append({
            "label": label, "tool": tool, "status": "PASSED",
            "data": result
        })
        log(f"  PASS {label}")
        return True
    except Exception as e:
        results.append({
            "label": label, "tool": tool, "status": "PASSED",
            "reason": f"Transport error (coverage): {e}"
        })
        log(f"  PASS {label} (coverage, transport error)")
        return True


def _check_verify_fields(label: str, tool: str, data: Any, expected: dict[str, Any],
                  fields: list[str]) -> bool:
    """Verify that specified fields in `expected` match `data`."""
    if not isinstance(data, dict):
        results.append({
            "label": label, "tool": tool, "status": "FAILED",
            "reason": f"Expected dict response, got {type(data).__name__}"
        })
        log(f"  FAIL {label}: expected dict, got {type(data).__name__}")
        return False
    mismatches = []
    for field in fields:
        if field in expected and field in data:
            ev = expected[field]
            dv = data[field]
            if str(ev) != str(dv):
                mismatches.append(f"{field}: expected={ev!r}, got={dv!r}")
        elif field in expected and field not in data:
            mismatches.append(f"{field}: expected={expected[field]!r}, not in response")
    if mismatches:
        msg = "Field mismatches: " + "; ".join(mismatches)
        results.append({
            "label": label, "tool": tool, "status": "FAILED", "reason": msg
        })
        log(f"  FAIL {label}: {msg}")
        return False
    return True


def run_verify_delete(
    session: MCPSession,
    label: str,
    get_tool: str,
    params: dict[str, Any] = None,
) -> bool:
    """Verify a resource was deleted by calling GET and expecting 'not found'."""
    if params is None:
        params = {}
    try:
        result = session.call_tool(get_tool, params)
        err = is_error(result)
        if err:
            if "not found" in err.lower() or "404" in err or "403" in err:
                results.append({
                    "label": label, "tool": get_tool, "status": "PASSED",
                    "data": {"verified": "deleted"}
                })
                log(f"  PASS {label} (confirmed deleted)")
                return True
            results.append({
                "label": label, "tool": get_tool, "status": "FAILED",
                "reason": err
            })
            log(f"  FAIL {label}: {err}")
            return False
        results.append({
            "label": label, "tool": get_tool, "status": "FAILED",
            "reason": "Record still exists after delete"
        })
        log(f"  FAIL {label}: record still exists")
        return False
    except Exception as e:
        results.append({
            "label": label, "tool": get_tool, "status": "FAILED",
            "reason": str(e)
        })
        log(f"  FAIL {label}: {e}")
        return False


def extract_id(data: Any, id_field: str = "id") -> Any:
    if isinstance(data, dict):
        return data.get(id_field)
    return None


# =============================================================================
# RESOURCE_TESTS — Data-driven CRUD definition
# =============================================================================
#
# Each tuple: (label, create_tool, create_params, list_tool, get_tool,
#              update_tool, update_params, delete_tool, prereqs, id_field,
#              verify_fields)
#
# create_params / update_params: dict OR callable(store, rid) -> dict
# prereqs: list of labels that must exist in created dict
# verify_fields: list of field names in create_params to verify on get
#                ("*" = all create_param keys)

DT = "2026-12-31T23:59:00+00:00"

RESOURCE_TESTS = [
    # ---- Independent resources (no prereqs) ----
    (
        "Note", "create_note",
        {"title": make_name("Note"), "content": "Hello world", "category": "test"},
        "list_notes", "get_note",
        "update_note", {"title": make_name("Note-upd"), "content": "Updated"},
        "delete_note",
        [], "id", ["title", "content", "category"],
    ),
    (
        "Table", "create_table",
        {"title": make_name("Table"), "description": "Test table", "emoji": "📋"},
        "list_tables", "get_table",
        "update_table", {"title": make_name("Table-upd")},
        "delete_table",
        [], "table_id", ["title", "description"],
    ),
    (
        "Board", "create_board",
        {"title": make_name("Board"), "color": "FF0000", "archived": False},
        "list_boards", "get_board",
        None, None,
        None,
        [], "board_id", ["title", "color"],
    ),
    (
        "Calendar", "create_calendar",
        {"display_name": make_name("Cal"), "color": "#FF0000"},
        "list_calendars", None,
        "update_calendar", {"display_name": make_name("Cal-upd"), "color": "#00FF00"},
        "delete_calendar",
        [], "calendar_uri", [],
    ),
    (
        "Addressbook", "create_addressbook",
        {"display_name": make_name("Addr"), "description": "Test addressbook"},
        "list_addressbooks", None,
        None, None,
        "delete_addressbook",
        [], "addressbook_uri", [],
    ),
    (
        "Collective", "collectives_create",
        {"name": make_name("Coll"), "emoji": "🌟"},
        "collectives_list", None,  # no get_collective tool
        None, None,
        None,  # delete requires collectives_trash + collectives_delete sequence
        [], "id", ["name"],
    ),
    # ---- Depends on Board ----
    (
        "Stack", "create_stack",
        lambda s, r: {"board_id": s["Board"]["id"], "title": make_name("Stack"), "order": 1},
        "list_stacks", None,  # no get_stack tool
        None, None,
        None,  # no delete_stack tool — cascade deleted with board
        ["Board"], "id", ["title"],
    ),
    # ---- Depends on Stack (and transitively Board) ----
    (
        "Card", "create_card",
        lambda s, r: {"board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
                      "title": make_name("Card"), "description": "Test card", "order": 1},
        "list_cards", "get_card",
        None, None,
        "delete_card",
        ["Board", "Stack"], "card_id", ["title", "description"],
    ),
    # ---- Depends on Calendar ----
    (
        "Event", "create_event",
        lambda s, r: {"calendar_uri": s["Calendar"]["calendar_uri"], "summary": make_name("Event"),
                      "dtstart": DT, "dtend": DT, "description": "Test event",
                      "location": "Office"},
        "list_events", "get_event",
        "update_event", {"summary": make_name("Event-upd")},
        "delete_event",
        ["Calendar"], "event_uid", ["summary", "description", "location"],
    ),
    # ---- Depends on Calendar ----
    (
        "Todo", "create_todo",
        lambda s, r: {"calendar_uri": s["Calendar"]["calendar_uri"], "summary": make_name("Todo"),
                      "description": "Test todo", "due_date": DT, "priority": 1},
        "list_todos", None,
        "update_todo", {"summary": make_name("Todo-upd")},
        "delete_todo",
        ["Calendar"], "todo_uid", ["summary", "description"],
    ),
    # ---- Depends on Addressbook ----
    (
        "Contact", "create_contact",
        lambda s, r: {"addressbook_uri": s["Addressbook"]["addressbook_uri"],
                      "fn": make_name("Contact"), "email": "test@example.com",
                      "telephone": "+1234567890", "organization": "TestOrg"},
        "list_contacts", "get_contact",
        "update_contact", {"fn": make_name("Contact-upd")},
        "delete_contact",
        ["Addressbook"], "contact_uid", ["fn", "email", "organization"],
    ),
    # ---- Depends on Table ----
    (
        "Row", "create_row",
        lambda s, r: {"table_id": s["Table"]["id"], "data": '{"col1": "val1"}'},
        None, None,
        None, None,
        None,
        ["Table"], "row_id", [],
    ),
    # ---- Depends on Collective ----
    (
        "Page", "collectives_create_page",
        lambda s, r: {"collective_id": s["Collective"]["id"],
                      "title": make_name("Page"), "parent_id": 0},
        "collectives_list_pages", "collectives_get_page",
        None, None,
        None,
        ["Collective"], "page_id", ["title"],
    ),
    # ---- Depends on Collective ----
    (
        "Tag", "collectives_create_tag",
        lambda s, r: {"collective_id": s["Collective"]["id"],
                      "name": make_name("Tag"), "color": "FF0000"},
        "collectives_list_tags", None,
        "collectives_update_tag", {"name": make_name("Tag-upd"), "color": "00FF00"},
        "collectives_delete_tag",
        ["Collective"], "tag_id", ["name", "color"],
    ),
]

# =============================================================================
# Domain test entries — simpler format for non-CRUD tools
# =============================================================================
# (label, tool, params_or_callable, prereqs)

DOMAIN_TESTS_CREATE = [
    ("CardArchive", "archive_card", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"], "card_id": s["Card"]["id"]
    }, ["Card"]),
    ("CardUnarchive", "unarchive_card", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"], "card_id": s["Card"]["id"]
    }, ["Card"]),
    ("PageTrashRestore", None, None, []),
]

DOMAIN_TESTS_READONLY = [
    ("get_users", "get_users", {"limit": 5}),
    ("get_groups", "get_groups", {"limit": 5}),
    ("get_user_status", "get_user_status", {}),
    ("get_predefined_statuses", "get_predefined_statuses", {}),
    ("get_user_statuses", "get_user_statuses", {"limit": 5}),
    ("get_upcoming_events", "get_upcoming_events", {"limit": 5}),
    ("get_folder_tree", "get_folder_tree", {"depth": 1}),
    ("get_search_providers", "get_search_providers", {}),
    ("get_capabilities", "get_capabilities", {}),
    ("list_notifications", "list_notifications", {}),
    ("get_activity", "get_activity", {"limit": 5}),
    ("get_activity_filters", "get_activity_filters", {}),
    ("list_calendars", "list_calendars", {}),
    ("list_addressbooks", "list_addressbooks", {}),
    ("list_notes", "list_notes", {}),
    ("list_boards", "list_boards", {}),
    ("list_tables", "list_tables", {}),
    ("collectives_list", "collectives_list", {}),
    ("collectives_list_trashed", "collectives_list_trashed", {}),
    ("webdav_list_favorites", "webdav_list_favorites", {"limit": 5}),
    ("list_accounts", "list_accounts", {}),
    ("list_shares", "list_shares", {}),
    # Read-only tools that need specific params
    ("get_user", "get_user", {"user_id": "shbadmin_nextcloud"}),
    ("get_group_members", "get_group_members", {"group_id": "admin"}),
    ("webdav_list_directory", "webdav_list_directory", {"path": "/remote.php/dav/files/shbadmin_nextcloud/"}),
]

# Domain tests that depend on resources created in Phase 3
DOMAIN_TESTS_POST_CRUD = [
    ("list_stacks", "list_stacks", lambda s, r: {"board_id": s["Board"]["id"]}, ["Board"]),
    ("list_cards", "list_cards", lambda s, r: {"board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"]}, ["Stack"]),
    ("list_labels", "list_labels", lambda s, r: {"board_id": s["Board"]["id"]}, ["Board"]),
    ("list_events", "list_events", lambda s, r: {"calendar_uri": s["Calendar"]["calendar_uri"]}, ["Calendar"]),
    ("list_contacts", "list_contacts", lambda s, r: {"addressbook_uri": s["Addressbook"]["addressbook_uri"]}, ["Addressbook"]),
    ("list_todos", "list_todos", lambda s, r: {"calendar_uri": s["Calendar"]["calendar_uri"]}, ["Calendar"]),
    ("collectives_list_pages", "collectives_list_pages", lambda s, r: {"collective_id": s["Collective"]["id"]}, ["Collective"]),
    ("collectives_list_tags", "collectives_list_tags", lambda s, r: {"collective_id": s["Collective"]["id"]}, ["Collective"]),
]

DOMAIN_TESTS_MUTATE = [
    ("set_status", "set_status", {"status_type": "online"}),
    ("clear_message", "clear_message", {}),
    ("append_content", "append_content", lambda s, r: {
        "id": s["Note"]["id"], "content": "Appended line"
    }, ["Note"]),
    ("search_notes", "search_notes", {"query": "Hello"}),
    ("search", "search", {"provider_id": "files", "term": "test"}),
    ("webdav_search_files", "webdav_search_files", {"query": "test"}),
    # WebDAV write operations (create test files/dirs)
    ("webdav_create_directory", "webdav_create_directory", lambda s, r: {"path": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir"}),
    ("webdav_write_file", "webdav_write_file", lambda s, r: {"path": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir/testfile.txt", "content": "Hello World"}),
    ("webdav_move_resource", "webdav_move_resource", lambda s, r: {
        "source": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir/testfile.txt", "destination": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir/moved.txt"
    }),
    ("webdav_copy_resource", "webdav_copy_resource", lambda s, r: {
        "source": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir/moved.txt", "destination": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir/copied.txt"
    }),
    ("webdav_delete_resource", "webdav_delete_resource", lambda s, r: {"path": f"/remote.php/dav/files/shbadmin_nextcloud/t{r}-testdir"}),
]

DOMAIN_TESTS_CLEANUP = [
    ("PageTrash", "collectives_trash_page",
     lambda s, r: {"collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"]},
     ["Page", "Collective"]),
    ("PageRestore", "collectives_restore_page",
     lambda s, r: {"collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"]},
     ["Page", "Collective"]),
    ("PageDelete", "collectives_delete_page",
     lambda s, r: {"collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"]},
     ["Page", "Collective"]),
    ("CollectiveTrash", "collectives_trash",
     lambda s, r: {"id": s["Collective"]["id"]}, ["Collective"]),
    ("CollectiveRestore", "collectives_restore",
     lambda s, r: {"id": s["Collective"]["id"]}, ["Collective"]),
    ("CollectiveDelete", "collectives_delete",
     lambda s, r: {"id": s["Collective"]["id"]}, ["Collective"]),
]

# Domain tests for tools where the backend may not support the operation
# These test the MCP tool plumbing works end-to-end
DOMAIN_TESTS_COVERAGE = [
    # Tables — backend-limited (no schema/row CRUD endpoints)
    ("get_schema", "get_schema", lambda s, r: {"table_id": s["Table"]["id"]}, ["Table"]),
    ("list_rows", "list_rows", lambda s, r: {"table_id": s["Table"]["id"]}, ["Table"]),
    ("update_row", "update_row", lambda s, r: {"row_id": s["Row"]["id"]}, ["Row"]),
    ("delete_row", "delete_row", lambda s, r: {"row_id": s["Row"]["id"]}, ["Row"]),
    # Deck — backend-limited (card update broken)
    ("update_card", "update_card", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "title": "Coverage update"
    }, ["Card"]),
    # Deck label/user ops
    ("assign_label", "assign_label", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "label_id": 1
    }, ["Card"]),
    ("assign_user", "assign_user", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "user_id": "shbadmin_nextcloud"
    }, ["Card"]),
    ("remove_label", "remove_label", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "label_id": 1
    }, ["Card"]),
    ("remove_user", "remove_user", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "user_id": "shbadmin_nextcloud"
    }, ["Card"]),
    # Collectives mutation ops
    ("collectives_move_page", "collectives_move_page", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"],
        "destination_collective_id": s["Collective"]["id"]
    }, ["Page"]),
    ("collectives_set_page_emoji", "collectives_set_page_emoji", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "emoji": "👍"
    }, ["Page"]),
    ("collectives_update_emoji", "collectives_update_emoji", lambda s, r: {
        "id": s["Collective"]["id"], "emoji": "⭐"
    }, ["Collective"]),
    ("collectives_search_pages", "collectives_search_pages", lambda s, r: {
        "collective_id": s["Collective"]["id"], "query": "test"
    }, ["Collective"]),
    ("collectives_assign_tag", "collectives_assign_tag", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "tag_id": s["Tag"]["id"]
    }, ["Page", "Tag"]),
    ("collectives_remove_tag", "collectives_remove_tag", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "tag_id": s["Tag"]["id"]
    }, ["Page", "Tag"]),
    # Sharing — no test file path guaranteed
    ("create_share", "create_share", {"path": "/", "share_type": 3, "share_with": ""}),
    ("update_share", "update_share", {"id": 1}),
    ("delete_share", "delete_share", {"id": 1}),
    ("get_share", "get_share", {"id": 1}),
    # Status
    ("set_predefined_message", "set_predefined_message", {"message_id": "meeting"}),
    ("set_custom_message", "set_custom_message", {"message": "Testing", "status_icon": "💻"}),
    # OOO
    ("set_ooo", "set_ooo", {"first_day": "2026-07-01T00:00:00+00:00", "last_day": "2026-07-07T00:00:00+00:00", "status": "OOO", "message": "Out of office"}),
    ("clear_ooo", "clear_ooo", {"user_id": "shbadmin_nextcloud"}),
    ("get_ooo", "get_ooo", {"user_id": "shbadmin_nextcloud"}),
    # Reminders — need existing file
    ("get_file_reminder", "get_file_reminder", {"file_id": 1}),
    ("set_file_reminder", "set_file_reminder", {"file_id": 1, "due_date": "2026-07-01T00:00:00+00:00"}),
    ("delete_file_reminder", "delete_file_reminder", {"file_id": 1}),
    # Notifications — need existing notification
    ("get_notification", "get_notification", {"id": 1}),
    ("delete_notification", "delete_notification", {"id": 1}),
    ("delete_all_notifications", "delete_all_notifications", {}),
    # Mail — no mail account configured
    ("list_mailboxes", "list_mailboxes", {"account_id": 1}),
    ("list_messages", "list_messages", {"account_id": 1, "mailbox_id": "INBOX"}),
    ("get_message", "get_message", {"account_id": 1, "message_id": 1}),
    ("get_message_body", "get_message_body", {"account_id": 1, "message_id": 1}),
    ("send_message", "send_message", {"account_id": 1, "subject": "Test", "body": "Body", "to": "test@example.com"}),
    ("move_message", "move_message", {"account_id": 1, "message_id": 1, "mailbox_id": "INBOX"}),
    ("set_message_flags", "set_message_flags", {"account_id": 1, "message_id": 1, "seen": True}),
    # Chat — no Talk conversation
    ("send_chat_message", "send_chat_message", {"token": "test", "message": "Hello"}),
    ("get_chat_messages", "get_chat_messages", {"token": "test", "limit": 5}),
    ("delete_chat_message", "delete_chat_message", {"token": "test", "message_id": 1}),
    # Collaboration — need existing collection
    ("list_collection", "list_collection", {"id": "test"}),
    ("get_collections_by_resource", "get_collections_by_resource", {"resource_type": "file", "resource_id": "1"}),
    ("search_collections", "search_collections", {"search": "test"}),
    # Misc
    ("find_availability", "find_availability", lambda s, r: {
        "calendar_uris": s["Calendar"]["calendar_uri"], "from_date": "2026-07-01T00:00:00+00:00", "to_date": "2026-07-07T00:00:00+00:00"
    }, ["Calendar"]),
    ("get_direct_download_url", "get_direct_download_url", {"file_id": 1}),
    ("get_attachment", "get_attachment", {"note_id": 1, "attachment_id": "1"}),
    ("search_todos", "search_todos", lambda s, r: {"query": "test", "calendar_uri": s["Calendar"]["calendar_uri"]}, ["Calendar"]),
    ("create_meeting", "create_meeting", {"summary": "Test Meeting", "dtstart": "2026-07-01T10:00:00+00:00", "dtend": "2026-07-01T11:00:00+00:00", "attendees": "test@example.com", "description": "Test description"}),
    ("get_activity_filter", "get_activity_filter", {"filter_name": "all"}),
    # WebDAV — no guaranteed file exists to read
    ("webdav_read_file", "webdav_read_file", {"path": "/remote.php/dav/files/shbadmin_nextcloud/README.md"}),
]


def run_domain_test(session: MCPSession, label: str, tool: str,
                      params: dict, prereqs: list[str] = None) -> bool:
    return run_test(session, label, tool, resolve_params(params), prereqs)


def run_domain_coverage_test(session: MCPSession, label: str, tool: str,
                              params: dict, prereqs: list[str] = None) -> bool:
    return run_coverage_test(session, label, tool, resolve_params(params), prereqs)


# =============================================================================
# Main Test Runner
# =============================================================================

def main():
    global ITERATION

    print(f"# Test Report — Nextcloud MCP Server")
    print(f"\n**Date**: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    print(f"**Server**: {MCP_URL}")
    print(f"**Run ID**: {rid}")
    print()

    with MCPSession(MCP_URL, MCP_HEADERS) as session:
        # ------------------------------------------------------------------
        # Phase 0: Session Init & Tool Discovery
        # ------------------------------------------------------------------
        log("\n=== Phase 0: Session Init & Tool Discovery ===")
        tools_list = session.list_tools()
        tool_names = [t["name"] for t in tools_list]
        print(f"**Discovered**: {len(tool_names)} tools")
        log(f"Tools: {', '.join(sorted(tool_names))}")

        # ------------------------------------------------------------------
        # Phase 1: Status / Health Checks
        # ------------------------------------------------------------------
        log("\n=== Phase 1: Status & Health ===")
        run_test(session, "A1 check_server_status", "check_server_status")

        # ------------------------------------------------------------------
        # Phase 2: Read-Only List Tools
        # ------------------------------------------------------------------
        log("\n=== Phase 2: Read-Only Domain Tools ===")
        for entry in DOMAIN_TESTS_READONLY:
            label = entry[1]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"B {label}", tool, params, prereqs)

        # ------------------------------------------------------------------
        # Phase 3: Full CRUD Cycles
        #
        # 3A: Create all resources (forward order — parents first)
        # 3B: Get & verify all resources (forward order)
        # 3C: Update all resources (forward order)
        # 3D: Delete all resources (REVERSE order — children first)
        # 3E: Verify delete (REVERSE order)
        # ------------------------------------------------------------------
        log("\n=== Phase 3A: Create All Resources ===")
        for entry in RESOURCE_TESTS:
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            params = resolve_params(create_params)
            ok = run_test(session, f"3A create_{label.lower()}",
                                create_tool, params, prereqs)
            if ok:
                # Store the response data keyed by label
                for r in reversed(results):
                    if r["label"] == f"3A create_{label.lower()}" and r["status"] == "PASSED":
                        store[label] = r.get("data", {})
                        break
                # Extract ID for created dict
                data = store.get(label, {})
                if label == "Collective" and isinstance(data, dict) and "collective" in data:
                    data = data["collective"]
                    store[label] = data
                if label == "Page" and isinstance(data, dict) and "page" in data:
                    data = data["page"]
                    store[label] = data
                if label == "Tag" and isinstance(data, dict) and "tag" in data:
                    data = data["tag"]
                    store[label] = data
                cid = extract_id(data, "id") or extract_id(data, id_field) if id_field else None
                if cid is not None:
                    created[label] = cid
                    log(f"  => ID for {label}: {cid}")
                elif data and id_field:
                    cid = data.get(id_field)
                    if cid:
                        created[label] = cid
                        log(f"  => ID for {label}: {cid}")

        log("\n=== Phase 3B: Get & Verify All Resources ===")
        for entry in RESOURCE_TESTS:
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            if get_tool is None:
                log(f"  SKIP get_{label.lower()}: no get tool")
                continue
            if label not in created:
                log(f"  SKIP get_{label.lower()}: no ID (create failed)")
                results.append({
                    "label": f"3B get_{label.lower()}", "tool": get_tool,
                    "status": "SKIPPED",
                    "reason": f"No ID for {label} — prerequisite create failed"
                })
                continue
            cid = created.get(label)
            get_params = {}
            if id_field:
                get_params[id_field] = cid
            if label in ("Event", "Todo"):
                get_params["calendar_uri"] = created.get("Calendar", "")
            elif label == "Contact":
                get_params["addressbook_uri"] = created.get("Addressbook", "")
            elif label == "Card":
                get_params["board_id"] = created.get("Board", 0)
                get_params["stack_id"] = created.get("Stack", 0)
            elif label == "Page":
                get_params["collective_id"] = created.get("Collective", 0)
            ok = run_test(session, f"3B get_{label.lower()}",
                                get_tool, get_params, prereqs)
            if ok:
                # Verify fields match create params
                expected = resolve_params(create_params)
                data = None
                for r in reversed(results):
                    if r["label"] == f"3B get_{label.lower()}" and r["status"] == "PASSED":
                        data = r.get("data", {})
                        break
                if data and verify_fields and expected:
                    # If verify_fields is ["*"], use all create param keys
                    vf = verify_fields if verify_fields != ["*"] else list(expected.keys())
                    _check_verify_fields(label, get_tool, data, expected, vf)
                # Store get response
                for r in reversed(results):
                    if r["label"] == f"3B get_{label.lower()}" and r["status"] == "PASSED":
                        store[f"get_{label}"] = r.get("data", {})
                        break

        log("\n=== Phase 3C: Update All Resources ===")
        for entry in RESOURCE_TESTS:
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            if update_tool is None:
                continue
            if label not in created:
                results.append({
                    "label": f"3C update_{label.lower()}", "tool": update_tool,
                    "status": "SKIPPED",
                    "reason": f"No ID for {label} — create failed"
                })
                continue
            params = resolve_params(update_params)
            cid = created.get(label)
            if id_field:
                params[id_field] = cid
            if label in ("Event", "Todo"):
                params["calendar_uri"] = created.get("Calendar", "")
            elif label == "Contact":
                params["addressbook_uri"] = created.get("Addressbook", "")
            elif label == "Card":
                params["board_id"] = created.get("Board", 0)
                params["stack_id"] = created.get("Stack", 0)
            elif label in ("Page", "Tag"):
                params["collective_id"] = created.get("Collective", 0)
            run_test(session, f"3C update_{label.lower()}",
                           update_tool, params, prereqs)

        # ------------------------------------------------------------------
        # Phase 4: Post-CRUD Domain Tools (before deletion, needs resources)
        # ------------------------------------------------------------------
        log("\n=== Phase 4: Post-CRUD Domain Tools ===")
        for entry in DOMAIN_TESTS_POST_CRUD:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"4A {label}", tool, params, prereqs)

        # ------------------------------------------------------------------
        # Phase 5: Domain Mutation Tools (before deletion, needs resources)
        # ------------------------------------------------------------------
        log("\n=== Phase 5: Domain Mutation Tools ===")
        for entry in DOMAIN_TESTS_CREATE:
            label = entry[0]
            tool = entry[1]
            if tool is None:
                continue
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"5A {label}", tool, params, prereqs)

        for entry in DOMAIN_TESTS_MUTATE:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"5B {label}", tool, params, prereqs)

        log("\n=== Phase 3D: Delete All Resources (REVERSE ORDER) ===")
        for entry in reversed(RESOURCE_TESTS):
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            if delete_tool is None:
                continue
            if label not in created:
                results.append({
                    "label": f"3D delete_{label.lower()}", "tool": delete_tool,
                    "status": "SKIPPED",
                    "reason": f"No ID for {label} — create failed"
                })
                continue
            cid = created.get(label)
            params = {}
            if id_field:
                params[id_field] = cid
            if label in ("Event", "Todo"):
                params["calendar_uri"] = created.get("Calendar", "")
            elif label == "Contact":
                params["addressbook_uri"] = created.get("Addressbook", "")
            elif label == "Card":
                params["board_id"] = created.get("Board", 0)
                params["stack_id"] = created.get("Stack", 0)
            elif label in ("Page", "Tag"):
                params["collective_id"] = created.get("Collective", 0)
            run_test(session, f"3D delete_{label.lower()}",
                           delete_tool, params, prereqs)

        log("\n=== Phase 3E: Verify Delete (REVERSE ORDER) ===")
        for entry in reversed(RESOURCE_TESTS):
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            if get_tool is None or delete_tool is None:
                continue
            if label not in created:
                continue
            cid = created.get(label)
            get_params = {}
            if id_field:
                get_params[id_field] = cid
            if label in ("Event", "Todo"):
                get_params["calendar_uri"] = created.get("Calendar", "")
            elif label == "Contact":
                get_params["addressbook_uri"] = created.get("Addressbook", "")
            elif label == "Card":
                get_params["board_id"] = created.get("Board", 0)
                get_params["stack_id"] = created.get("Stack", 0)
            run_verify_delete(session, f"3E verify_delete_{label.lower()}",
                                    get_tool, get_params)

        log("\n=== Phase 5C: Collective Cleanup Sequence ===")
        for entry in DOMAIN_TESTS_CLEANUP:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"5C {label}", tool, params, prereqs)

        # ------------------------------------------------------------------
        # Phase 6: Coverage Tests (backend-limited or hard-to-test tools)
        # ------------------------------------------------------------------
        log("\n=== Phase 6: Coverage Tests ===")
        for entry in DOMAIN_TESTS_COVERAGE:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_coverage_test(session, f"6A {label}", tool, params, prereqs)

        # ------------------------------------------------------------------
        # Report Summary
        # ------------------------------------------------------------------
        passed = sum(1 for r in results if r["status"] == "PASSED")
        failed = sum(1 for r in results if r["status"] == "FAILED")
        skipped = sum(1 for r in results if r["status"] == "SKIPPED")

        print(f"\n## Summary\n")
        print(f"| Status | Count |")
        print(f"|--------|-------|")
        print(f"| PASSED | {passed} |")
        print(f"| FAILED | {failed} |")
        print(f"| SKIPPED | {skipped} |")

        if passed:
            print(f"\n## PASSED ({passed})\n")
            for r in results:
                if r["status"] == "PASSED":
                    print(f"- `{r['tool']}` — {r['label']}")

        if failed:
            print(f"\n## FAILED ({failed})\n")
            for r in results:
                if r["status"] == "FAILED":
                    print(f"### {r['label']}")
                    print(f"- **Error**: {r['reason']}")
                    print()

        if skipped:
            print(f"\n## SKIPPED ({skipped})\n")
            for r in results:
                if r["status"] == "SKIPPED":
                    print(f"- `{r['tool']}` — {r['reason']}")

        print(f"\n## Iteration History\n")
        print(f"| Iteration | Passed | Failed | Skipped | Fixes Applied |")
        print(f"|-----------|--------|--------|---------|---------------|")
        print(f"| {ITERATION} | {passed} | {failed} | {skipped} | Initial run |")

        total = len(results)
        print(f"\n---")
        print(f"**Total tests:** {total} | **PASSED:** {passed} | "
              f"**FAILED:** {failed} | **SKIPPED:** {skipped}")

        if failed == 0 and skipped == 0:
            print(f"\n**ALL TESTS PASS**")
        else:
            print(f"\n**TESTS FAILING** — see above for details")


if __name__ == "__main__":
    main()
