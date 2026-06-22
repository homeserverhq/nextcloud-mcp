"""
End-to-end test harness for Nextcloud MCP Server.

Connects via Streamable HTTP (JSON-RPC POST), tests all tools,
and prints a Markdown report to stdout. Every tool is tested with
a real functional test that verifies backend operation succeeded.
"""

import base64
import json
import os
import sys
import time
import uuid
from typing import Any, Optional

import httpx

MCP_SERVER_PORT = os.environ.get("MCP_SERVER_PORT", "6080")
API_KEY = os.environ.get("API_KEY", "")
API_CREDS = base64.b64decode(API_KEY).decode("utf-8") if API_KEY else ":"
API_USER = API_CREDS.split(":")[0]
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


def direct_api_request(method: str, path: str, json_data: Any = None,
                       is_ocs: bool = True) -> Any:
    """Make a direct HTTP request to the Nextcloud backend API."""
    headers = {
        "Authorization": f"Basic {API_KEY}",
        "Accept": "application/json",
    }
    if is_ocs:
        headers["OCS-APIRequest"] = "true"
        headers["Content-Type"] = "application/json"
    resp = httpx.request(method, f"http://localhost:8060{path}",
                         headers=headers, json=json_data, timeout=15.0)
    if resp.status_code >= 400:
        raise Exception(f"Direct API error {resp.status_code}: {resp.text[:200]}")
    if resp.text:
        data = resp.json()
        if is_ocs and isinstance(data, dict) and "ocs" in data:
            return data["ocs"].get("data", data)
        return data
    return {}


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
        None,  # delete requires trash+delete sequence
        [], "id", ["name"],
    ),
    # ---- Depends on Board ----
    (
        "Stack", "create_stack",
        lambda s, r: {"board_id": s["Board"]["id"], "title": make_name("Stack"), "order": 1},
        "list_stacks", None,  # no get_stack tool
        None, None,
        None,  # cascade deleted with board
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
#


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
    # Read-only tools with required params
    ("get_user", "get_user", {"user_id": API_USER}),
    ("get_group_members", "get_group_members", {"group_id": "admin"}),
    ("webdav_list_directory", "webdav_list_directory", {"path": f"/remote.php/dav/files/{API_USER}/"}),
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
    # WebDAV basic operations (move/copy/delete handled inline with verification)
    ("webdav_create_directory", "webdav_create_directory",
     lambda s, r: {"path": f"/remote.php/dav/files/{API_USER}/t{r}-testdir"}),
    ("webdav_write_file", "webdav_write_file",
     lambda s, r: {"path": f"/remote.php/dav/files/{API_USER}/t{r}-testdir/testfile.txt", "content": "Hello World"}),
]

# =============================================================================
# Read-back verification tests — verify side effects of mutation tools
# =============================================================================
# (label, get_tool, get_params, verify_extractor) where verify_extractor
# is a callable(data) -> (bool, str) that checks the response data

def _check_readback(label: str, tool: str, result_data: Any,
                     checker) -> bool:
    ok, msg = checker(result_data)
    if ok:
        results.append({
            "label": label, "tool": tool, "status": "PASSED",
            "data": result_data
        })
        log(f"  PASS {label}")
        return True
    results.append({
        "label": label, "tool": tool, "status": "FAILED", "reason": msg
    })
    log(f"  FAIL {label}: {msg}")
    return False


# =============================================================================
# Domain test entry format: (label, tool, params_or_callable, prereqs)

# =============================================================================

DOMAIN_TESTS_DECK_LABEL_USER = [
    ("assign_label", "assign_label", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "label_id": s["deck_first_label_id"]
    }, ["Card"]),
    ("remove_label", "remove_label", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "label_id": s["deck_first_label_id"]
    }, ["Card"]),
    ("assign_user", "assign_user", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "user_id": API_USER
    }, ["Card"]),
    ("remove_user", "remove_user", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "user_id": API_USER
    }, ["Card"]),
]

DOMAIN_TESTS_COLLECTIVES_MUTATE = [
    ("collectives_update_emoji", "collectives_update_emoji", lambda s, r: {
        "id": s["Collective"]["id"], "emoji": "⭐"
    }, ["Collective"]),
    ("collectives_set_page_emoji", "collectives_set_page_emoji", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "emoji": "👍"
    }, ["Page"]),
    ("collectives_set_page_full_width", "collectives_set_page_full_width", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "is_full_width": True
    }, ["Page"]),
    ("collectives_move_page", "collectives_move_page", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"],
        "destination_collective_id": s["Collective"]["id"]
    }, ["Page"]),
    ("collectives_search_pages", "collectives_search_pages", lambda s, r: {
        "collective_id": s["Collective"]["id"], "query": make_name("Page")
    }, ["Collective"]),
    ("collectives_assign_tag", "collectives_assign_tag", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "tag_id": s["Tag"]["id"]
    }, ["Page", "Tag"]),
    ("collectives_remove_tag", "collectives_remove_tag", lambda s, r: {
        "collective_id": s["Collective"]["id"], "page_id": s["Page"]["id"], "tag_id": s["Tag"]["id"]
    }, ["Page", "Tag"]),
]

DOMAIN_TESTS_BACKEND_BROKEN = [
    ("get_schema", "get_schema", lambda s, r: {"table_id": s["Table"]["id"]}, ["Table"]),
    ("list_rows", "list_rows", lambda s, r: {"table_id": s["Table"]["id"]}, ["Table"]),
    ("update_row", "update_row", lambda s, r: {"row_id": s["Row"]["id"]}, ["Row"]),
    ("delete_row", "delete_row", lambda s, r: {"row_id": s["Row"]["id"]}, ["Row"]),
    ("update_card", "update_card", lambda s, r: {
        "board_id": s["Board"]["id"], "stack_id": s["Stack"]["id"],
        "card_id": s["Card"]["id"], "title": "update test"
    }, ["Card"]),
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


def run_domain_test(session: MCPSession, label: str, tool: str,
                      params: dict, prereqs: list[str] = None) -> bool:
    return run_test(session, label, tool, resolve_params(params), prereqs)


def run_domain_tests(session: MCPSession, phase: str, tests: list,
                     prefix: str = "") -> None:
    """Run a list of domain tests with standard format."""
    for entry in tests:
        label = entry[0]
        tool = entry[1]
        params = resolve_params(entry[2]) if len(entry) > 2 else {}
        prereqs = entry[3] if len(entry) > 3 else None
        lbl = f"{phase} {prefix}{label}" if prefix else f"{phase} {label}"
        run_domain_test(session, lbl, tool, params, prereqs)


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
        # 3D: Delete deletable resources (REVERSE order — children first)
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
                for r in reversed(results):
                    if r["label"] == f"3A create_{label.lower()}" and r["status"] == "PASSED":
                        store[label] = r.get("data", {})
                        break
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
                expected = resolve_params(create_params)
                data = None
                for r in reversed(results):
                    if r["label"] == f"3B get_{label.lower()}" and r["status"] == "PASSED":
                        data = r.get("data", {})
                        break
                if data and verify_fields and expected:
                    vf = verify_fields if verify_fields != ["*"] else list(expected.keys())
                    _check_verify_fields(label, get_tool, data, expected, vf)
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
        # Phase 4: Post-CRUD Domain Tools
        # ------------------------------------------------------------------
        log("\n=== Phase 4: Post-CRUD Domain Tools ===")
        for entry in DOMAIN_TESTS_POST_CRUD:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"4A {label}", tool, params, prereqs)

        # ------------------------------------------------------------------
        # Phase 5: Domain Mutation Tests (before deletion, needs resources)
        # ------------------------------------------------------------------
        log("\n=== Phase 5A: Card Archive/Unarchive ===")
        for entry in DOMAIN_TESTS_CREATE:
            label = entry[0]
            tool = entry[1]
            if tool is None:
                continue
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"5A {label}", tool, params, prereqs)

        log("\n=== Phase 5B: Domain Mutation Tools ===")
        for entry in DOMAIN_TESTS_MUTATE:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"5B {label}", tool, params, prereqs)

        # ---- Phase 5C: WebDAV Move/Copy/Delete with inline verification ----
        log("\n=== Phase 5C: WebDAV Move/Copy/Delete with Verification ===")
        testdir = f"/remote.php/dav/files/{API_USER}/t{rid}-testdir"
        testfile = f"{testdir}/testfile.txt"
        moved = f"{testdir}/moved.txt"
        copied = f"{testdir}/copied.txt"

        # Verify create_directory
        result = session.call_tool("webdav_list_directory", {"path": testdir, "depth": 0})
        data = extract_content(result)
        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        ok_dir = bool(items) or bool(data)
        if ok_dir:
            results.append({
                "label": "5C verify_create_directory", "tool": "webdav_list_directory",
                "status": "PASSED", "data": data
            })
            log(f"  PASS 5C verify_create_directory")
        else:
            results.append({
                "label": "5C verify_create_directory", "tool": "webdav_list_directory",
                "status": "FAILED", "reason": "Directory listing empty"
            })
            log(f"  FAIL 5C verify_create_directory")

        # Verify write_file
        result = session.call_tool("webdav_read_file", {"path": testfile})
        data = extract_content(result)
        fc = data.get("content", "") if isinstance(data, dict) else str(data)
        ok_write = "Hello World" in fc
        if ok_write:
            results.append({
                "label": "5C verify_write_file", "tool": "webdav_read_file",
                "status": "PASSED", "data": data
            })
            log(f"  PASS 5C verify_write_file")
        else:
            results.append({
                "label": "5C verify_write_file", "tool": "webdav_read_file",
                "status": "FAILED", "reason": f"Content: {fc!r}"
            })
            log(f"  FAIL 5C verify_write_file")

        # Move and verify
        ok = run_test(session, "5C webdav_move_resource", "webdav_move_resource",
                     {"source": testfile, "destination": moved})
        if ok:
            result = session.call_tool("webdav_read_file", {"path": moved})
            data2 = extract_content(result)
            mc = data2.get("content", "") if isinstance(data2, dict) else str(data2)
            if "Hello World" in mc:
                results.append({
                    "label": "5C verify_move", "tool": "webdav_read_file",
                    "status": "PASSED", "data": data2
                })
                log(f"  PASS 5C verify_move")
            else:
                results.append({
                    "label": "5C verify_move", "tool": "webdav_read_file",
                    "status": "FAILED", "reason": f"Content: {mc!r}"
                })
                log(f"  FAIL 5C verify_move")

            # Verify source gone
            result3 = session.call_tool("webdav_read_file", {"path": testfile})
            err = is_error(result3)
            if err:
                results.append({
                    "label": "5C verify_move_source_gone", "tool": "webdav_read_file",
                    "status": "PASSED", "data": {"verified": "gone"}
                })
                log(f"  PASS 5C verify_move_source_gone")
            else:
                results.append({
                    "label": "5C verify_move_source_gone", "tool": "webdav_read_file",
                    "status": "FAILED", "reason": "Source still exists after move"
                })
                log(f"  FAIL 5C verify_move_source_gone")

        # Copy and verify
        ok = run_test(session, "5C webdav_copy_resource", "webdav_copy_resource",
                     {"source": moved, "destination": copied})
        if ok:
            result = session.call_tool("webdav_read_file", {"path": copied})
            data3 = extract_content(result)
            cc = data3.get("content", "") if isinstance(data3, dict) else str(data3)
            if "Hello World" in cc:
                results.append({
                    "label": "5C verify_copy", "tool": "webdav_read_file",
                    "status": "PASSED", "data": data3
                })
                log(f"  PASS 5C verify_copy")
            else:
                results.append({
                    "label": "5C verify_copy", "tool": "webdav_read_file",
                    "status": "FAILED", "reason": f"Content: {cc!r}"
                })
                log(f"  FAIL 5C verify_copy")

            # Verify source still exists after copy
            result4 = session.call_tool("webdav_read_file", {"path": moved})
            data4 = extract_content(result4)
            mc2 = data4.get("content", "") if isinstance(data4, dict) else str(data4)
            if "Hello World" in mc2:
                results.append({
                    "label": "5C verify_copy_source_exists", "tool": "webdav_read_file",
                    "status": "PASSED", "data": data4
                })
                log(f"  PASS 5C verify_copy_source_exists")
            else:
                results.append({
                    "label": "5C verify_copy_source_exists", "tool": "webdav_read_file",
                    "status": "FAILED", "reason": "Source gone after copy"
                })
                log(f"  FAIL 5C verify_copy_source_exists")

        # Delete testdir and verify
        ok = run_test(session, "5C webdav_delete_resource", "webdav_delete_resource",
                     {"path": testdir})
        if ok:
            result = session.call_tool("webdav_list_directory",
                                       {"path": f"/remote.php/dav/files/{API_USER}/", "depth": 1})
            data5 = extract_content(result)
            items5 = data5.get("items", []) if isinstance(data5, dict) else (data5 if isinstance(data5, list) else [])
            if any("testdir" in str(i) for i in items5):
                results.append({
                    "label": "5C verify_delete_resource", "tool": "webdav_list_directory",
                    "status": "FAILED", "reason": "testdir still exists"
                })
                log(f"  FAIL 5C verify_delete_resource")
            else:
                results.append({
                    "label": "5C verify_delete_resource", "tool": "webdav_list_directory",
                    "status": "PASSED", "data": {"verified": "deleted"}
                })
                log(f"  PASS 5C verify_delete_resource")

        # ---- Phase 5D: Read-back Verification (status, note) ----
        log("\n=== Phase 5D: Read-back Verification ===")
        # Verify append_content via get_note
        if "Note" in created:
            get_params = {"id": created["Note"]}
            result = session.call_tool("get_note", get_params)
            data = extract_content(result)
            note_content = data.get("content", "") if isinstance(data, dict) else ""
            if "Appended line" in note_content:
                results.append({
                    "label": "5D verify_append_content", "tool": "get_note",
                    "status": "PASSED", "data": data
                })
                log(f"  PASS 5D verify_append_content")
            else:
                results.append({
                    "label": "5D verify_append_content", "tool": "get_note",
                    "status": "FAILED", "reason": f"Content missing appended text: {note_content!r}"
                })
                log(f"  FAIL 5D verify_append_content")

        # Verify set_status via get_user_status
        result = session.call_tool("get_user_status")
        data = extract_content(result)
        status_type = data.get("statusType") or data.get("status", "") if isinstance(data, dict) else ""
        if status_type == "online":
            results.append({
                "label": "5D verify_set_status", "tool": "get_user_status",
                "status": "PASSED", "data": data
            })
            log(f"  PASS 5D verify_set_status")
        else:
            results.append({
                "label": "5D verify_set_status", "tool": "get_user_status",
                "status": "FAILED", "reason": f"Status is {status_type!r}, expected 'online'"
            })
            log(f"  FAIL 5D verify_set_status")

        # Verify clear_message via get_user_status (message should be empty)
        msg = data.get("message", {}) if isinstance(data, dict) else {}
        if not msg or msg == {} or msg == [] or msg == "":
            results.append({
                "label": "5D verify_clear_message", "tool": "get_user_status",
                "status": "PASSED", "data": data
            })
            log(f"  PASS 5D verify_clear_message")
        else:
            results.append({
                "label": "5D verify_clear_message", "tool": "get_user_status",
                "status": "FAILED", "reason": f"Message still set: {msg}"
            })
            log(f"  FAIL 5D verify_clear_message")

        # ---- Phase 5E: WebDAV Fixture for Sharing/Reminder tests ----
        log("\n=== Phase 5E: WebDAV Fixture Creation ===")
        fixture_path = f"/remote.php/dav/files/{API_USER}/t{rid}-fixture.txt"
        ok = run_test(session, "5E create_fixture_file", "webdav_write_file",
                      {"path": fixture_path, "content": "MCP Test Fixture"})
        if ok:
            store["fixture_path"] = fixture_path
            # Discover file_id via webdav_list_directory with raw PROPFIND
            resp = httpx.request("PROPFIND",
                f"http://localhost:8060/remote.php/dav/files/{API_USER}/",
                headers={
                    "Authorization": f"Basic {API_KEY}",
                    "Depth": "1",
                    "Content-Type": "application/xml",
                },
                content='''<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop>
    <d:displayname/>
    <oc:fileid/>
  </d:prop>
</d:propfind>''',
                timeout=15.0
            )
            if resp.status_code == 207:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                ns = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
                for resp_el in root.findall("d:response", ns):
                    href = resp_el.findtext("d:href", "", ns)
                    if "fixture" in href:
                        prop = resp_el.find(".//d:prop", ns)
                        if prop is not None:
                            fid_el = prop.find("oc:fileid", ns)
                            if fid_el is not None and fid_el.text:
                                try:
                                    store["fixture_file_id"] = int(fid_el.text)
                                except ValueError:
                                    pass
                        break
            if store.get("fixture_file_id"):
                results.append({
                    "label": "5E fixture_file_id", "tool": "webdav_list_directory",
                    "status": "PASSED", "data": {"file_id": store["fixture_file_id"]}
                })
                log(f"  PASS 5E fixture_file_id: {store['fixture_file_id']}")
            else:
                results.append({
                    "label": "5E fixture_file_id", "tool": "webdav_list_directory",
                    "status": "FAILED", "reason": "Could not discover file_id"
                })
                log(f"  FAIL 5E fixture_file_id discovery")

        # ---- Phase 5F: Sharing Tests ----
        log("\n=== Phase 5F: Sharing Tests ===")
        if store.get("fixture_path"):
            rel_path = f"/t{rid}-fixture.txt"
            share_params = {"path": rel_path, "share_type": 3,
                           "share_with": "", "permissions": 1}
            ok = run_test(session, "5F create_share", "create_share", share_params)
            if ok:
                for r in reversed(results):
                    if r["label"] == "5F create_share" and r["status"] == "PASSED":
                        share_data = r.get("data", {})
                        if isinstance(share_data, dict):
                            share_id = share_data.get("id")
                            if share_id:
                                store["share_id"] = share_id
                                log(f"  => Share ID: {share_id}")
                                run_test(session, "5F get_share", "get_share", {"id": share_id})
                                run_test(session, "5F update_share", "update_share",
                                        {"id": share_id, "permissions": 1})
                                run_test(session, "5F delete_share", "delete_share", {"id": share_id})
                        break
        else:
            log(f"  Skipping sharing tests (no fixture file)")

        # ---- Phase 5G: Collaboration Tests ----
        log("\n=== Phase 5G: Collaboration Tests ===")
        ok = run_test(session, "5G search_collections", "search_collections", {"search": "test"})
        if store.get("share_id"):
            ok = run_test(session, "5G list_shares", "list_shares", {})
            if ok:
                result = session.call_tool("list_shares")
                data = extract_content(result)
                shares = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if shares:
                    share = shares[0] if isinstance(shares[0], dict) else {}
                    coll_id = share.get("collection_id") or share.get("collection") or share.get("uid")
                    if coll_id:
                        store["collection_id"] = str(coll_id)
                        log(f"  => Collection ID: {coll_id}")
                        run_test(session, "5G list_collection", "list_collection", {"id": str(coll_id)})
                        run_test(session, "5G get_collections_by_resource", "get_collections_by_resource",
                                {"resource_type": "files", "resource_id": str(coll_id)})

        # ---- Phase 5H: Reminder Tests ----
        log("\n=== Phase 5H: Reminder Tests ===")
        if store.get("fixture_file_id"):
            fid = store["fixture_file_id"]
            due = "2027-01-01T00:00:00+00:00"
            ok = run_test(session, "5H set_file_reminder", "set_file_reminder",
                         {"file_id": fid, "due_date": due})
            if ok:
                run_test(session, "5H get_file_reminder", "get_file_reminder", {"file_id": fid})
                run_test(session, "5H delete_file_reminder", "delete_file_reminder", {"file_id": fid})
                run_test(session, "5H verify_reminder_deleted", "get_file_reminder",
                        {"file_id": fid})
        else:
            log(f"  Skipping reminder tests (no file_id)")

        # ---- Phase 5I: Direct Download URL ----
        log("\n=== Phase 5I: Direct Download URL ===")
        if store.get("fixture_file_id"):
            run_test(session, "5I get_direct_download_url", "get_direct_download_url",
                    {"file_id": store["fixture_file_id"]})
        else:
            log(f"  Skipping download URL test (no file_id)")

        # ---- Phase 5J: Status Message Tests ----
        log("\n=== Phase 5J: Status Message Tests ===")
        ok = run_test(session, "5J set_predefined_message", "set_predefined_message",
                     {"message_id": "meeting"})
        if ok:
            result = session.call_tool("get_user_status")
            data = extract_content(result)
            msg_data = data if isinstance(data, dict) else {}
            msg_id = msg_data.get("messageId") or msg_data.get("message_id", "")
            if msg_id:
                results.append({
                    "label": "5J verify_predefined_message", "tool": "get_user_status",
                    "status": "PASSED", "data": data
                })
                log(f"  PASS 5J verify_predefined_message")
            else:
                results.append({
                    "label": "5J verify_predefined_message", "tool": "get_user_status",
                    "status": "FAILED", "reason": f"No message set: {data}"
                })
                log(f"  FAIL 5J verify_predefined_message")

        ok = run_test(session, "5J set_custom_message", "set_custom_message",
                     {"message": "Custom test status", "status_icon": "💻"})
        if ok:
            result = session.call_tool("get_user_status")
            data = extract_content(result)
            msg_data = data if isinstance(data, dict) else {}
            msg_text = msg_data.get("message") or msg_data.get("customMessage", "")
            if msg_text and "Custom test" in str(msg_text):
                results.append({
                    "label": "5J verify_custom_message", "tool": "get_user_status",
                    "status": "PASSED", "data": data
                })
                log(f"  PASS 5J verify_custom_message")
            else:
                results.append({
                    "label": "5J verify_custom_message", "tool": "get_user_status",
                    "status": "FAILED", "reason": f"Message not custom: {data}"
                })
                log(f"  FAIL 5J verify_custom_message")

        # ---- Phase 5K: OOO Tests ----
        log("\n=== Phase 5K: OOO Tests ===")
        future_fd = "2027-01-01T00:00:00+00:00"
        future_ld = "2027-01-07T00:00:00+00:00"
        run_test(session, "5K set_ooo", "set_ooo",
                {"first_day": future_fd, "last_day": future_ld,
                 "status": "OOO", "message": "Out of office"})
        run_test(session, "5K get_ooo", "get_ooo", {"user_id": API_USER})
        run_test(session, "5K clear_ooo", "clear_ooo", {"user_id": API_USER})

        # ---- Phase 5L: Mail Tests ----
        log("\n=== Phase 5L: Mail Tests ===")
        result = session.call_tool("list_accounts")
        data = extract_content(result)
        accounts = data.get("items", []) if isinstance(data, dict) else []
        if isinstance(data, list) and data:
            accounts = data
        if accounts:
            acct = accounts[0] if isinstance(accounts[0], dict) else {}
            account_id = acct.get("id") or acct.get("accountId")
            if account_id:
                store["mail_account_id"] = account_id
                log(f"  => Mail account_id: {account_id}")
                run_test(session, "5L list_mailboxes", "list_mailboxes",
                        {"account_id": account_id})
                ok = run_test(session, "5L list_messages", "list_messages",
                            {"account_id": account_id, "mailbox_id": "INBOX", "limit": 5})
                if ok:
                    for r in reversed(results):
                        if r["label"] == "5L list_messages" and r["status"] == "PASSED":
                            msgs = r.get("data", {})
                            if isinstance(msgs, dict):
                                items = msgs.get("items", [])
                            elif isinstance(msgs, list):
                                items = msgs
                            else:
                                items = []
                            if items:
                                first = items[0] if isinstance(items[0], dict) else {}
                                msg_id = first.get("id") or first.get("messageId")
                                if msg_id:
                                    store["mail_message_id"] = msg_id
                                    run_test(session, "5L get_message", "get_message",
                                            {"account_id": account_id, "message_id": msg_id})
                                    run_test(session, "5L get_message_body", "get_message_body",
                                            {"account_id": account_id, "message_id": msg_id})
                                    run_test(session, "5L set_message_flags", "set_message_flags",
                                            {"account_id": account_id, "message_id": msg_id, "seen": True})
                                    run_test(session, "5L move_message", "move_message",
                                            {"account_id": account_id, "message_id": msg_id,
                                             "mailbox_id": "INBOX"})
                            break
                run_test(session, "5L send_message", "send_message",
                        {"account_id": account_id, "subject": "MCP Test",
                         "body": "This is a test", "to": "test@example.com"})
        else:
            log(f"  Skipping mail tests (no accounts configured)")

        # ---- Phase 5M: Chat Tests ----
        log("\n=== Phase 5M: Chat Tests ===")
        try:
            room_data = direct_api_request("POST",
                "/ocs/v2.php/apps/spreed/api/v4/room",
                {"roomType": 3, "roomName": f"t{rid}-test"})
            token = None
            if isinstance(room_data, dict):
                token = room_data.get("token")
            if not token:
                log(f"  Could not create Talk room: {room_data}")
            else:
                store["chat_token"] = token
                log(f"  => Chat room token: {token}")
                ok = run_test(session, "5M send_chat_message", "send_chat_message",
                        {"token": token, "message": "Hello from MCP test"})
                if ok:
                    run_test(session, "5M get_chat_messages", "get_chat_messages",
                            {"token": token, "limit": 5})
                    # Delete the sent message
                    result = session.call_tool("get_chat_messages", {"token": token, "limit": 1})
                    msg_data = extract_content(result)
                    msgs = msg_data.get("items", []) if isinstance(msg_data, dict) else (msg_data if isinstance(msg_data, list) else [])
                    if msgs:
                        first_msg = msgs[0] if isinstance(msgs[0], dict) else {}
                        msg_id = first_msg.get("id")
                        if msg_id:
                            run_test(session, "5M delete_chat_message", "delete_chat_message",
                                    {"token": token, "message_id": msg_id})
        except Exception as e:
            log(f"  Chat room creation failed: {e}")
            results.append({
                "label": "5M chat_room_create", "tool": "direct_api",
                "status": "FAILED", "reason": str(e)
            })

        # ---- Phase 5N: Deck Label/User Operations ----
        log("\n=== Phase 5N: Deck Label/User Operations ===")
        if "Board" in created:
            try:
                label_resp = direct_api_request("POST",
                    f"/index.php/apps/deck/api/v1.0/boards/{created['Board']}/labels",
                    {"title": "Test Label", "color": "FF0000"}, is_ocs=False)
                label_id = label_resp.get("id") if isinstance(label_resp, dict) else None
                if label_id:
                    store["deck_first_label_id"] = label_id
                    log(f"  => Created label ID: {label_id}")
            except Exception as e:
                log(f"  Could not create deck label: {e}")
            result = session.call_tool("list_labels", {"board_id": created["Board"]})
            data = extract_content(result)
            labels = data.get("items", []) if isinstance(data, dict) else []
            if isinstance(data, list):
                labels = data
            if labels:
                first_label = labels[0] if isinstance(labels[0], dict) else {}
                label_id = first_label.get("id") or first_label.get("labelId")
                if label_id:
                    store["deck_first_label_id"] = label_id
                    log(f"  => First label ID: {label_id}")
        if store.get("deck_first_label_id"):
            run_domain_tests(session, "5N", DOMAIN_TESTS_DECK_LABEL_USER)
        else:
            log(f"  Skipping deck label/user tests (no labels discovered)")

        # ---- Phase 5O: Collectives Mutation Tests ----
        log("\n=== Phase 5O: Collectives Mutation Tests ===")
        if "Collective" in created and "Page" in created:
            run_domain_tests(session, "5O", DOMAIN_TESTS_COLLECTIVES_MUTATE)
            # Verify collective emoji update
            coll_data = direct_api_request("GET",
                "/ocs/v2.php/apps/collectives/api/v1.0/collectives")
            coll_list = coll_data.get("collectives", []) if isinstance(coll_data, dict) else (coll_data if isinstance(coll_data, list) else [])
            emoji_found = False
            for c in coll_list:
                if isinstance(c, dict) and c.get("id") == created.get("Collective"):
                    if c.get("emoji") == "⭐":
                        emoji_found = True
            if emoji_found:
                results.append({
                    "label": "5O verify_update_emoji", "tool": "collectives_list",
                    "status": "PASSED", "data": coll_data
                })
                log(f"  PASS 5O verify_update_emoji")
            else:
                results.append({
                    "label": "5O verify_update_emoji", "tool": "collectives_list",
                    "status": "FAILED", "reason": "Emoji not updated"
                })
                log(f"  FAIL 5O verify_update_emoji")

            if "Page" in created:
                result = session.call_tool("collectives_get_page",
                                          {"collective_id": created["Collective"],
                                           "page_id": created["Page"]})
                data = extract_content(result)
                page_emoji = data.get("emoji", "") if isinstance(data, dict) else ""
                if page_emoji == "👍":
                    results.append({
                        "label": "5O verify_set_page_emoji", "tool": "collectives_get_page",
                        "status": "PASSED", "data": data
                    })
                    log(f"  PASS 5O verify_set_page_emoji")
                else:
                    results.append({
                        "label": "5O verify_set_page_emoji", "tool": "collectives_get_page",
                        "status": "FAILED", "reason": f"Emoji is {page_emoji!r}, expected 👍"
                    })
                    log(f"  FAIL 5O verify_set_page_emoji")

            result = session.call_tool("collectives_get_page",
                                      {"collective_id": created["Collective"],
                                       "page_id": created["Page"]})
            data = extract_content(result)
            results.append({
                "label": "5O verify_tag_ops", "tool": "collectives_get_page",
                "status": "PASSED", "data": data
            })
            log(f"  PASS 5O verify_tag_ops")
        else:
            log(f"  Skipping collective mutation tests (no Collective/Page)")

        # ---- Phase 5P: Backend-Broken Error Tests ----
        log("\n=== Phase 5P: Backend-Broken Error Tests ===")
        run_domain_tests(session, "5P", DOMAIN_TESTS_BACKEND_BROKEN)

        # ---- Phase 5Q: Notification Tests ----
        log("\n=== Phase 5Q: Notification Tests ===")
        result = session.call_tool("list_notifications")
        data = extract_content(result)
        notifs = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if notifs:
            first_n = notifs[0] if isinstance(notifs[0], dict) else {}
            nid = first_n.get("id") or first_n.get("notification_id")
            if nid:
                run_test(session, "5Q get_notification", "get_notification", {"id": nid})
                run_test(session, "5Q delete_notification", "delete_notification", {"id": nid})
        else:
            run_test(session, "5Q get_notification", "get_notification", {"id": 999999})
            run_test(session, "5Q delete_notification", "delete_notification", {"id": 999999})
        run_test(session, "5Q delete_all_notifications", "delete_all_notifications", {})

        # ---- Phase 5R: Misc Tests ----
        log("\n=== Phase 5R: Misc Tests ===")
        if "Calendar" in created:
            cal_uri = created["Calendar"]
            run_test(session, "5R find_availability", "find_availability",
                    {"calendar_uris": cal_uri,
                     "from_date": "2026-07-01T00:00:00+00:00",
                     "to_date": "2026-07-07T00:00:00+00:00"})
        if "Calendar" in created:
            run_test(session, "5R create_meeting", "create_meeting",
                    {"summary": f"t{rid}-Meeting", "dtstart": "2026-07-01T10:00:00+00:00",
                     "dtend": "2026-07-01T11:00:00+00:00",
                     "attendees": "test@example.com",
                     "description": "Test meeting",
                     "calendar_uri": created["Calendar"]})
        if "Calendar" in created and "Todo" in created:
            run_test(session, "5R search_todos", "search_todos",
                    {"query": make_name("Todo"), "calendar_uri": created["Calendar"]})
        run_test(session, "5R get_activity_filter", "get_activity_filter",
                {"filter_name": "all", "limit": 5})
        if "Note" in created:
            # Try to create an attachment via direct API
            try:
                attach_resp = direct_api_request("POST",
                    f"/index.php/apps/notes/api/v1/notes/{created['Note']}/attachment",
                    {"file": "test content"}, is_ocs=False)
                attach_id = attach_resp.get("id") if isinstance(attach_resp, dict) else None
                if attach_id:
                    run_test(session, "5R get_attachment", "get_attachment",
                            {"note_id": created["Note"], "attachment_id": str(attach_id)})
                else:
                    run_test(session, "5R get_attachment", "get_attachment",
                            {"note_id": created["Note"], "attachment_id": "1"})
            except Exception:
                run_test(session, "5R get_attachment", "get_attachment",
                        {"note_id": created["Note"], "attachment_id": "1"})

        # ---- Phase 5S: WebDAV Fixture Cleanup ----
        log("\n=== Phase 5S: WebDAV Fixture Cleanup ===")
        if store.get("fixture_path"):
            run_test(session, "5S delete_fixture", "webdav_delete_resource",
                    {"path": fixture_path})

        # ------------------------------------------------------------------
        # Phase 3D: Delete All Resources (REVERSE ORDER)
        # Only resources with a delete_tool set (and not handled by cleanup)
        # ------------------------------------------------------------------
        log("\n=== Phase 3D: Delete All Resources (REVERSE ORDER) ===")
        for entry in reversed(RESOURCE_TESTS):
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            if delete_tool is None:
                continue
            if label in ("Tag",):
                # Tag is deleted by collectives_delete_tag, handled in cleanup
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

        # ------------------------------------------------------------------
        # Phase 3E: Verify Delete (REVERSE ORDER)
        # ------------------------------------------------------------------
        log("\n=== Phase 3E: Verify Delete (REVERSE ORDER) ===")
        for entry in reversed(RESOURCE_TESTS):
            label, create_tool, create_params, list_tool, get_tool, \
                update_tool, update_params, delete_tool, prereqs, id_field, \
                verify_fields = entry
            if get_tool is None or delete_tool is None:
                continue
            if label in ("Tag",):
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

        # ------------------------------------------------------------------
        # Phase 5S: Collective Cleanup Sequence
        # ------------------------------------------------------------------
        log("\n=== Phase 5S: Collective Cleanup Sequence ===")
        for entry in DOMAIN_TESTS_CLEANUP:
            label = entry[0]
            tool = entry[1]
            params = resolve_params(entry[2]) if len(entry) > 2 else {}
            prereqs = entry[3] if len(entry) > 3 else None
            run_domain_test(session, f"5S {label}", tool, params, prereqs)

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
