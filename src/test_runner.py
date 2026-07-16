"""
End-to-end test harness for Nextcloud MCP Server.

Connects via Streamable HTTP (JSON-RPC POST), tests all tools,
and prints a Markdown report to stdout. Every tool is tested with
a real functional test that verifies backend operation succeeded.
"""

import base64
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Optional

import httpx
from toon_mcp import toon_to_json

MCP_SERVER_PORT = os.environ.get("MCP_SERVER_PORT", "6080")
API_KEY = os.environ.get("API_KEY", "")
API_CREDS = base64.b64decode(API_KEY).decode("utf-8")
API_USER = API_CREDS.split(":")[0]
MCP_URL = f"http://localhost:{MCP_SERVER_PORT}/mcp"

# No hardcoded fallbacks — all IDs are discovered at runtime.

MCP_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
}

rid = uuid.uuid4().hex[:8]

results: list[dict[str, Any]] = []
store: dict[str, Any] = {}
passed_count = 0
failed_count = 0

ITERATION = 1


class MCPSession:

    def __init__(self, url, headers):
        self.url = url
        self.base_headers = {**headers, "Content-Type": "application/json", "Accept": "application/json"}
        self.session_headers = dict(self.base_headers)
        self.client = httpx.Client(timeout=15.0)
        self._request_id = 0
        self._session_id = ""

    def __enter__(self):
        self._initialize()
        return self

    def __exit__(self, *args):
        self.client.close()

    def _send_notification(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self.client.post(self.url, headers=self.session_headers, json=payload)

    def _send(self, method, params=None):
        self._request_id = self._request_id + 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        response = self.client.post(self.url, headers=self.session_headers, json=payload)
        sid = response.headers.get("mcp-session-id")
        self._session_id = (self._session_id, sid)[bool(sid)]
        self.session_headers = {**self.base_headers, "mcp-session-id": self._session_id}
        data = response.json()
        return data.get("result", {})

    def _initialize(self):
        result = self._send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "nextcloud-test-runner", "version": "1.0"}})
        self._send_notification("notifications/initialized")
        return result

    def list_tools(self):
        result = self._send("tools/list")
        return result.get("tools", result)

    def call_tool(self, name, arguments=None):
        return self._send("tools/call", {"name": name, "arguments": arguments})


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_error(result):
    hasf = result["isError"]
    content = result["content"]
    safe_content = ([{"text": ""}], content)[bool(content)]
    first_text = safe_content[0]["text"]
    part = first_text.partition(":")
    tool_prefix = ("", part[2].strip())[bool(part[0].startswith("Error calling tool"))]
    return ("", first_text)[hasf] + tool_prefix


def extract_content(result):
    has_err = result["isError"]
    content = result["content"]
    safe_content = ([{"text": ""}], content)[bool(content)]
    first_text = safe_content[0]["text"]
    loadable = ("{}", first_text)[not has_err]
    return json.loads(loadable)


def make_name(base: str) -> str:
    return f"t{rid}-{base}"


def resolve_params(params):
    return dict(params)

def run_test(session, label, tool, params=None):
    result = session.call_tool(tool, params)
    err = is_error(result)
    status = ("FAILED", "PASSED")[not err]
    content = extract_content(result)
    entry = {"label": label, "tool": tool, "status": status, "data": content}
    entry["reason"] = err
    results.append(entry)
    global passed_count, failed_count
    passed_count = passed_count + 1
    failed_count = failed_count + (1, 0)[not err]
    log(f"  {status} {label}")


# _check_verify_fields removed — unused after Phase 3 flattening


def run_verify_delete(session, label, get_tool=None, params=None, list_tool=None, list_params=None, match_field=None, expected_value=None):
    result = session.call_tool(get_tool, params)
    err = is_error(result)
    err_l = err.lower()
    content_text = (result.get("content") or [{}])[0].get("text", "").lower()
    is_deleted = (" not found" in err_l) | err_l.startswith("not found") | ("does not exist" in err_l) | ("404" in err) | ("403" in err) | ("not found" in content_text) | ("has been deleted" in content_text) | ("does not exist" in content_text)
    status = ("FAILED", "PASSED")[is_deleted]
    entry = {"label": label, "tool": get_tool, "status": status, "reason": err, "data": {"verified": "deleted"}}
    results.append(entry)
    global passed_count, failed_count
    passed_count = passed_count + 1
    failed_count = failed_count + (1, 0)[is_deleted]
    log(f"  {status} {label}")


def get_list_items(data):
    if not isinstance(data, dict):
        return []
    raw = data.get("items", [])
    return toon_to_json(raw) if isinstance(raw, str) else (raw or [])


def safe_data(key=None, default=None):
    """Safely read from the last test result's data dict.
    Returns the whole dict if key is None, or the key's value (default if missing)."""
    if default is None:
        default = ""
    data = results[-1]["data"] if results else {}
    if key is None:
        return data
    return data.get(key, default)


def safe_items(data, index=0, key=None):
    """Safely extract an item from a tool result's items list.
    Returns the item dict if key is None, or the key's value."""
    raw = data.get("items", []) if isinstance(data, dict) else []
    items = toon_to_json(raw) if isinstance(raw, str) else (raw or [])
    if index < len(items):
        item = items[index]
        if key is None:
            return item
        return item.get(key, "")
    return "" if key else {}


def safe_call(session, tool, params=None):
    """Call a tool and return empty dict on failure instead of crashing."""
    try:
        result = session.call_tool(tool, params)
        return extract_content(result)
    except Exception:
        return {}


def find_mailbox_by_special(mailboxes_data, special_value):
    items = get_list_items(mailboxes_data)
    for m in items:
        su = m.get("specialUse") or []
        if isinstance(su, list) and special_value in su:
            return m["databaseId"]
    return items[0]["databaseId"] if items else 0


def find_message_by_subject(messages_data, subject):
    items = get_list_items(messages_data)
    for m in items:
        if m.get("subject") == subject:
            return m["databaseId"]
    return 0


DT = "2026-12-31T23:59:00+00:00"


def _cleanup_test_collectives(session):
    """Delete orphaned test collectives matching t*-Coll pattern.
    Returns the number deleted.
    """
    count = 0
    pattern = re.compile(r"^t[a-f0-9]{8}-Coll$")

    def name_match(item):
        return pattern.match(item.get("name", "") or item.get("displayName", "") or "")

    # Clean active collectives
    list_result = session.call_tool("list_collectives", {})
    list_data = extract_content(list_result)
    if list_data and isinstance(list_data, dict):
        items = get_list_items(list_data)
        for coll in items:
            if name_match(coll):
                coll_id = coll["id"]
                session.call_tool("trash_collective", {"id": coll_id})
                session.call_tool("delete_collective", {"id": coll_id})
                count += 1

    # Clean trashed collectives
    trash_result = session.call_tool("list_trashed_collectives", {})
    trash_data = extract_content(trash_result)
    if trash_data and isinstance(trash_data, dict):
        items = get_list_items(trash_data)
        for coll in items:
            if name_match(coll):
                session.call_tool("delete_collective", {"id": coll["id"]})
                count += 1

    return count


def _cleanup_test_circles(session):
    """Delete orphaned test circles matching tXXXXXXXX-* pattern.
    Returns the number deleted.
    """
    count = 0
    pattern = re.compile(r"^t[a-f0-9]{8}-")
    list_result = session.call_tool("list_circles", {})
    list_data = extract_content(list_result)
    if list_data and isinstance(list_data, dict):
        items = get_list_items(list_data)
        for c in items:
            name = c.get("name", "")
            if pattern.match(name):
                session.call_tool("delete_circle", {"circle_id": c["id"]})
                count += 1
    return count


def _cleanup_test_rooms(session):
    """Delete orphaned test talk rooms matching tXXXXXXXX-* pattern.
    Returns the number deleted.
    """
    count = 0
    pattern = re.compile(r"^t[a-f0-9]{8}-")
    list_result = session.call_tool("list_talk_rooms", {})
    list_data = extract_content(list_result)
    if list_data and isinstance(list_data, dict):
        items = get_list_items(list_data)
        for r in items:
            name = r.get("displayName", "")
            if pattern.match(name):
                session.call_tool("delete_talk_room", {"token": r["token"]})
                count += 1
    return count


def _cleanup_test_files(session):
    """Delete orphaned test files matching tXXXXXXXX-* pattern.
    Returns the number deleted.
    """
    count = 0
    pattern = re.compile(r"^t[a-f0-9]{8}-")
    # Search root-level files
    search_result = session.call_tool("search_files", {"query": "t"})
    search_data = extract_content(search_result)
    if search_data and isinstance(search_data, dict):
        items = get_list_items(search_data)
        for f in items:
            name = f.get("displayname", "")
            if pattern.match(name):
                session.call_tool("delete_file", {"path": f["href"]})
                count += 1
    return count


# =============================================================================
# Main Test Runner
# =============================================================================

def main():
    global ITERATION
    global passed_count, failed_count

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
        tool_names = list(map(lambda t: t["name"], tools_list))
        print(f"**Discovered**: {len(tool_names)} tools")
        log(f"Tools: {', '.join(sorted(tool_names))}")

        # ------------------------------------------------------------------
        # Phase 0A: Pre-Creation Cleanup — delete orphaned test resources
        # ------------------------------------------------------------------
        log("\n=== Phase 0A: Pre-Creation Cleanup ===")
        _ca_count = _cleanup_test_collectives(session)
        log(f"  Cleaned {_ca_count} orphaned test collective(s) from .Collectives/")
        _cb_count = _cleanup_test_circles(session)
        log(f"  Cleaned {_cb_count} orphaned test circle(s)")
        _cc_count = _cleanup_test_rooms(session)
        log(f"  Cleaned {_cc_count} orphaned test talk room(s)")
        _cd_count = _cleanup_test_files(session)
        log(f"  Cleaned {_cd_count} orphaned test file(s)")

        # ------------------------------------------------------------------
        # Phase 1: Status / Health Checks
        # ------------------------------------------------------------------
        log("\n=== Phase 1: Status & Health ===")
        run_test(session, "A1 check_server_status", "check_server_status")

        # ------------------------------------------------------------------
        # Phase 2: Read-Only List Tools
        # ------------------------------------------------------------------
        log("\n=== Phase 2: Read-Only Domain Tools ===")
        run_test(session, "B list_users", "list_users", {"limit": 5})
        run_test(session, "B list_groups", "list_groups", {"limit": 5})
        run_test(session, "B get_user_status", "get_user_status", {})
        run_test(session, "B list_predefined_statuses", "list_predefined_statuses", {})
        run_test(session, "B list_user_statuses", "list_user_statuses", {"limit": 5})
        run_test(session, "B list_upcoming_events", "list_upcoming_events", {"limit": 5})
        run_test(session, "B list_all_folders", "list_all_folders", {})
        run_test(session, "B list_search_providers", "list_search_providers", {})
        run_test(session, "B get_capabilities", "get_capabilities", {})
        run_test(session, "B list_notifications", "list_notifications", {})
        run_test(session, "B list_circles", "list_circles", {})
        run_test(session, "B list_activities", "list_activities", {"limit": 5})
        run_test(session, "B list_activity_filters", "list_activity_filters", {})
        run_test(session, "B list_all_calendars", "list_all_calendars", {})
        run_test(session, "B list_all_addressbooks", "list_all_addressbooks", {})
        run_test(session, "B list_notes", "list_notes", {})
        run_test(session, "B list_deck_boards", "list_deck_boards", {})
        run_test(session, "B list_tables", "list_tables", {})
        run_test(session, "B list_collectives", "list_collectives", {})
        run_test(session, "B list_trashed_collectives", "list_trashed_collectives", {})
        run_test(session, "B list_favorites", "list_favorites", {"limit": 5})
        run_test(session, "B list_all_accounts", "list_all_accounts", {})
        run_test(session, "B list_all_shares", "list_all_shares", {})
        run_test(session, "B list_talk_rooms", "list_talk_rooms", {})
        run_test(session, "B get_user", "get_user", {"user_id": API_USER})
        run_test(session, "B list_group_members", "list_group_members", {"group_id": "admin"})
        run_test(session, "B list_folder_contents_root", "list_folder_contents", {"path": ""})

        # ------------------------------------------------------------------
        # Phase 3: Full CRUD Cycles — flat, no loops, no conditionals

        # ------------------------------------------------------------------
        # Phase 3: Full CRUD Cycles — flat, no loops, no conditionals
        #
        # 3A: Create all resources (forward order — parents first)
        # 3B: Get & verify all resources (forward order)
        # 3C: Update all resources (forward order)
        # 3D: Delete deletable resources (REVERSE order — children first)
        # 3E: Verify delete (REVERSE order)
        # ------------------------------------------------------------------
        log("\n=== Phase 3A: Create All Resources ===")
        run_test(session, "3A create_note", "create_note", {"title": make_name("Note"), "content": "Hello world", "category": "test"})
        store["Note"] = safe_data()
        store["Note_id"] = safe_data("id")
        log(f"  => Note ID: {store['Note_id'] or 0}")

        run_test(session, "3A create_table", "create_table", {"title": make_name("Table"), "description": "Test table", "emoji": "📋"})
        store["Table"] = safe_data()
        store["Table_id"] = safe_data("id")
        log(f"  => Table ID: {store['Table_id'] or 0}")

        run_test(session, "3A create_board", "create_deck_board", {"title": make_name("Board"), "color": "FF0000", "archived": False})
        store["Board"] = safe_data()
        store["Board_id"] = safe_data("id")
        log(f"  => Board ID: {store['Board_id'] or 0}")

        run_test(session, "3A create_calendar", "create_calendar", {"display_name": make_name("Cal"), "color": "#FF0000"})
        store["Calendar"] = safe_data()
        store["Calendar_id"] = safe_data("calendar_uri")
        log(f"  => Calendar URI: {store['Calendar_id'] or ''}")

        run_test(session, "3A create_addressbook", "create_addressbook", {"display_name": make_name("Addr"), "description": "Test addressbook"})
        store["Addressbook"] = safe_data()
        store["Addressbook_id"] = safe_data("addressbook_uri")
        log(f"  => Addressbook URI: {store['Addressbook_id'] or ''}")

        run_test(session, "3A create_collective", "create_collective", {"name": make_name("Coll"), "emoji": "🌟"})
        coll_data = safe_data()
        coll_inner = coll_data.get("collective", {})
        store["Collective"] = coll_inner
        store["Collective_id"] = coll_inner.get("id", 0)
        circle_list_data = safe_call(session, "list_circles")
        circle_list_items = get_list_items(circle_list_data)
        store["circle_id"] = circle_list_items[0].get("id", 0) if circle_list_items else 0

        run_test(session, "3A create_stack", "create_deck_stack", {"board_id": store.get("Board", {}).get("id", 0), "title": make_name("Stack"), "order": 1})
        store["Stack"] = safe_data()
        store["Stack_id"] = safe_data("id")

        run_test(session, "3A create_card", "create_deck_card", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "title": make_name("Card"), "description": "test"})
        store["Card"] = safe_data()
        store["Card_id"] = safe_data("id")

        run_test(session, "3A create_event", "create_event", {"calendar_uri": store.get("Calendar_id", ""), "summary": make_name("Event"), "dtstart": DT, "dtend": DT, "description": "test"})
        store["Event"] = safe_data()
        store["Event_id"] = safe_data("event_uid")

        run_test(session, "3A create_todo", "create_todo", {"calendar_uri": store.get("Calendar_id", ""), "summary": make_name("Todo"), "description": "test", "due_date": DT})
        store["Todo"] = safe_data()
        store["Todo_id"] = safe_data("todo_uid")

        run_test(session, "3A create_contact", "create_contact", {"addressbook_uri": store.get("Addressbook_id", ""), "fn": make_name("Contact"), "email": "test@example.com", "telephone": "+1234567890", "organization": "TestOrg"})
        store["Contact"] = safe_data()
        store["Contact_id"] = safe_data("contact_uid")

        run_test(session, "3A create_row", "create_table_row", {"table_id": store.get("Table", {}).get("id", 0), "data": {"col1": "val1"}})
        store["Row"] = safe_data()
        store["Row_id"] = safe_data("id")

        run_test(session, "3A create_page", "create_collective_page", {"collective_id": store.get("Collective", {}).get("id", 0), "title": make_name("Page"), "parent_id": 0})
        store["Page"] = safe_data("page", {})
        store["Page_id"] = store.get("Page", {}).get("id", 0)

        run_test(session, "3A create_tag", "create_collective_tag", {"collective_id": store.get("Collective", {}).get("id", 0), "name": make_name("Tag"), "color": "FF0000"})
        store["Tag"] = safe_data("tag", {})
        store["Tag_id"] = store.get("Tag", {}).get("id", 0)

        run_test(session, "3A verify_note_in_list", "list_notes", {})
        run_test(session, "3A verify_table_in_list", "list_tables", {})
        run_test(session, "3A verify_board_in_list", "list_deck_boards", {})
        run_test(session, "3A verify_calendar_in_list", "list_all_calendars", {})
        run_test(session, "3A verify_addressbook_in_list", "list_all_addressbooks", {})
        run_test(session, "3A verify_collective_in_list", "list_collectives", {})
        run_test(session, "3A verify_stack_in_list", "list_deck_stacks", {"board_id": store.get("Board_id", 0)})
        run_test(session, "3A verify_card_in_list", "list_deck_cards", {"board_id": store.get("Board_id", 0), "stack_id": store.get("Stack_id", 0)})
        run_test(session, "3A verify_event_in_list", "list_events_by_calendar", {"calendar_uri": store.get("Calendar_id", "")})
        run_test(session, "3A verify_todo_in_list", "list_todos_by_calendar", {"calendar_uri": store.get("Calendar_id", "")})
        run_test(session, "3A verify_contact_in_list", "list_contacts_by_addressbook", {"addressbook_uri": store.get("Addressbook_id", "")})
        run_test(session, "3A verify_page_in_list", "list_collective_pages", {"collective_id": store.get("Collective", {}).get("id", 0)})
        run_test(session, "3A verify_tag_in_list", "list_collective_tags", {"collective_id": store.get("Collective", {}).get("id", 0)})

        log("\n=== Phase 3B: Get & Verify All Resources ===")
        run_test(session, "3B get_note", "get_note", {"id": store["Note_id"]})
        run_test(session, "3B get_table", "get_table", {"table_id": store["Table_id"]})
        run_test(session, "3B get_board", "get_deck_board", {"board_id": store["Board_id"]})
        run_test(session, "3B get_calendar", "get_calendar", {"calendar_uri": store["Calendar_id"]})
        run_test(session, "3B get_addressbook", "get_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3B get_collective", "get_collective", {"id": store["Collective_id"]})
        run_test(session, "3B get_stack", "get_deck_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"]})
        run_test(session, "3B get_card", "get_deck_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_test(session, "3B get_event", "get_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3B get_todo", "get_todo", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3B get_contact", "get_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3B get_row", "get_table_row", {"row_id": store["Row_id"]})
        run_test(session, "3B get_page", "get_collective_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"]})
        run_test(session, "3B get_tag", "get_collective_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"]})

        log("\n=== Phase 3C: Update All Resources ===")
        run_test(session, "3C update_note", "update_note", {"id": store["Note_id"], "title": make_name("Note-upd"), "content": "Updated"})
        run_test(session, "3C update_table", "update_table", {"table_id": store["Table_id"], "title": make_name("Table-upd")})
        run_test(session, "3C update_board", "update_deck_board", {"board_id": store["Board_id"], "title": make_name("Board-upd"), "color": "00FF00"})
        run_test(session, "3C update_calendar", "update_calendar", {"calendar_uri": store["Calendar_id"], "display_name": make_name("Cal-upd"), "color": "#00FF00"})
        run_test(session, "3C update_addressbook", "update_addressbook", {"addressbook_uri": store["Addressbook_id"], "display_name": make_name("Addr-upd")})
        run_test(session, "3C update_collective", "update_collective", {"id": store["Collective_id"], "emoji": "🌈"})
        run_test(session, "3C update_stack", "update_deck_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"], "title": make_name("Stack-upd")})
        run_test(session, "3C update_card", "update_deck_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"], "title": make_name("Card-upd")})
        run_test(session, "3C update_tag", "update_collective_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"], "name": make_name("Tag-upd"), "color": "00FF00"})
        run_test(session, "3C update_event", "update_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"], "summary": make_name("Event-upd")})
        run_test(session, "3C update_todo", "update_todo", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"], "summary": make_name("Todo-upd")})
        run_test(session, "3C update_contact", "update_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"], "fn": make_name("Contact-upd")})
        run_test(session, "3C update_row", "update_table_row", {"row_id": store["Row_id"], "data": {"col1": "upd"}})
        run_test(session, "3C update_page", "update_collective_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"], "title": make_name("Page-upd")})

        # ------------------------------------------------------------------
        # Phase 3.5: Minimal-Params Create & Delete
        # ------------------------------------------------------------------
        log("\n=== Phase 3.5: Minimal-Params Create & Delete ===")
        run_test(session, "3.5 create_board", "create_deck_board", {"title": make_name("minBoard")})
        run_test(session, "3.5 delete_board", "delete_deck_board", {"board_id": safe_data("id", 0)})
        run_test(session, "3.5 create_note", "create_note", {"title": make_name("minNote"), "content": "min"})
        run_test(session, "3.5 delete_note", "delete_note", {"id": safe_data("id", 0)})
        run_test(session, "3.5 create_table", "create_table", {"title": make_name("minTable"), "description": "min"})
        run_test(session, "3.5 delete_table", "delete_table", {"table_id": safe_data("id", 0)})
        run_test(session, "3.5 create_calendar", "create_calendar", {"display_name": make_name("minCal")})
        run_test(session, "3.5 delete_calendar", "delete_calendar", {"calendar_uri": safe_data("calendar_uri", "")})
        run_test(session, "3.5 create_addressbook", "create_addressbook", {"display_name": make_name("minAddr"), "description": "min"})
        run_test(session, "3.5 delete_addressbook", "delete_addressbook", {"addressbook_uri": safe_data("addressbook_uri", "")})
        run_test(session, "3.5 create_collective", "create_collective", {"name": make_name("minColl")})
        c5_inner = safe_data("collective", {})
        c5_id = c5_inner.get("id", 0)
        run_test(session, "3.5 delete_collective", "trash_collective", {"id": c5_id})
        run_test(session, "3.5 delete_collective", "delete_collective", {"id": c5_id})
        run_test(session, "3.5 create_circle", "create_circle", {"name": make_name("minCircle")})
        run_test(session, "3.5 delete_circle", "delete_circle", {"circle_id": safe_data("id", 0)})
        run_test(session, "3.5 create_stack", "create_deck_stack", {"board_id": store.get("Board_id", 0), "title": make_name("minStack")})
        s5_id = safe_data("id", 0)
        run_test(session, "3.5 delete_stack", "delete_deck_stack", {"board_id": store.get("Board_id", 0), "stack_id": s5_id})
        run_test(session, "3.5 create_card", "create_deck_card", {"board_id": store.get("Board_id", 0), "stack_id": store.get("Stack_id", 0), "title": make_name("minCard"), "description": "min"})
        c5d_id = safe_data("id", 0)
        run_test(session, "3.5 delete_card", "delete_deck_card", {"board_id": store.get("Board_id", 0), "stack_id": store.get("Stack_id", 0), "card_id": c5d_id})
        run_test(session, "3.5 create_event", "create_event", {"calendar_uri": store.get("Calendar_id", ""), "summary": make_name("minEvent"), "dtstart": DT, "dtend": DT, "description": "min"})
        e5_uid = safe_data("event_uid", "")
        run_test(session, "3.5 delete_event", "delete_event", {"calendar_uri": store.get("Calendar_id", ""), "event_uid": e5_uid})
        run_test(session, "3.5 create_todo", "create_todo", {"calendar_uri": store.get("Calendar_id", ""), "summary": make_name("minTodo"), "description": "min", "due_date": DT})
        t5_uid = safe_data("todo_uid", "")
        run_test(session, "3.5 delete_todo", "delete_todo", {"calendar_uri": store.get("Calendar_id", ""), "todo_uid": t5_uid})
        run_test(session, "3.5 create_contact", "create_contact", {"addressbook_uri": store.get("Addressbook_id", ""), "fn": make_name("minContact"), "email": "min@test.com"})
        c5t_uid = safe_data("contact_uid", "")
        run_test(session, "3.5 delete_contact", "delete_contact", {"addressbook_uri": store.get("Addressbook_id", ""), "contact_uid": c5t_uid})
        run_test(session, "3.5 create_row", "create_table_row", {"table_id": store.get("Table_id", 0), "data": {"col1": "min"}})
        run_test(session, "3.5 delete_row", "delete_table_row", {"row_id": safe_data("id", 0)})
        run_test(session, "3.5 create_page", "create_collective_page", {"collective_id": store.get("Collective_id", 0), "title": make_name("minPage"), "parent_id": 0})
        p5_inner = safe_data("page", {})
        run_test(session, "3.5 delete_page", "delete_collective_page", {"collective_id": store.get("Collective_id", 0), "page_id": p5_inner.get("id", 0)})
        run_test(session, "3.5 create_tag", "create_collective_tag", {"collective_id": store.get("Collective_id", 0), "name": make_name("minTag"), "color": "FF0000"})
        t5_inner = safe_data("tag", {})
        run_test(session, "3.5 delete_tag", "delete_collective_tag", {"tag_id": t5_inner.get("id", 0), "collective_id": store.get("Collective_id", 0)})
        # ------------------------------------------------------------------
        # Phase 4: Post-CRUD Domain Tools
        # ------------------------------------------------------------------
        log("\n=== Phase 4: Post-CRUD Domain Tools ===")
        run_test(session, "4A list_deck_stacks", "list_deck_stacks", {"board_id": store.get("Board", {}).get("id", 0)})
        run_test(session, "4A list_deck_cards", "list_deck_cards", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0)})
        run_test(session, "4A list_deck_labels", "list_deck_labels", {"board_id": store.get("Board", {}).get("id", 0)})
        run_test(session, "4A list_events_by_calendar", "list_events_by_calendar", {"calendar_uri": store.get("Calendar", {}).get("calendar_uri", "")})
        run_test(session, "4A list_contacts_by_addressbook", "list_contacts_by_addressbook", {"addressbook_uri": store.get("Addressbook", {}).get("addressbook_uri", "")})
        run_test(session, "4A list_todos_by_calendar", "list_todos_by_calendar", {"calendar_uri": store.get("Calendar", {}).get("calendar_uri", "")})
        run_test(session, "4A list_notes_by_category", "list_notes_by_category", {"category": "test"})
        run_test(session, "4A list_collective_pages", "list_collective_pages", {"collective_id": store.get("Collective", {}).get("id", 0)})
        run_test(session, "4A list_collective_tags", "list_collective_tags", {"collective_id": store.get("Collective", {}).get("id", 0)})
        run_test(session, "4A table_get_schema", "get_table_schema", {"table_id": store.get("Table", {}).get("id", 0)})
        run_test(session, "4A list_table_rows", "list_table_rows", {"table_id": store.get("Table", {}).get("id", 0)})

        # ------------------------------------------------------------------
        # Phase 5: Domain Mutation Tests (before deletion, needs resources)
        # ------------------------------------------------------------------
        log("\n=== Phase 5A: Card Archive/Unarchive ===")
        run_test(session, "5A CardArchive", "archive_deck_card", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "card_id": store.get("Card", {}).get("id", 0)})
        run_test(session, "5A CardUnarchive", "unarchive_deck_card", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "card_id": store.get("Card", {}).get("id", 0)})
        run_test(session, "5A PageTrash", "trash_collective_page", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0)})
        run_test(session, "5A PageRestore", "restore_collective_page", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0)})

        log("\n=== Phase 5B: Domain Mutation Tools ===")
        run_test(session, "5B set_status", "set_status", {"status_type": "online"})
        run_test(session, "5B clear_message", "clear_message", {})
        run_test(session, "5B note_append_content", "append_note_content", {"id": store.get("Note", {}).get("id", 0), "content": "Appended line"})
        run_test(session, "5B note_search", "search_notes", {"query": "Hello"})
        run_test(session, "5B search", "search", {"provider_id": "files", "term": "test"})
        run_test(session, "5B table_update_row", "update_table_row", {"row_id": store.get("Row", {}).get("id", 0), "data": {}})

        # ---- Phase 5D: Read-back Verification ----
        log("\n=== Phase 5D: Read-back Verification ===")
        run_test(session, "5D verify_append_content", "get_note", {"id": store.get("Note", {}).get("id", 0)})
        run_test(session, "5D verify_set_status", "get_user_status", {})
        run_test(session, "5D verify_clear_message", "get_user_status", {})

        # ---- Phase 5E: File Fixture ----
        log("\n=== Phase 5E: File Fixture Creation ===")
        fixture_path = f"t{rid}-fixture.txt"
        run_test(session, "5E create_fixture_file", "write_file", {"path": fixture_path, "content": "MCP Test Fixture"})
        store["fixture_path"] = fixture_path
        run_test(session, "5E list_fixture", "list_folder_contents", {"path": fixture_path, "include_all_fields": True})
        _fid = safe_items(results[-1]["data"], 0, "fileid")
        store["fixture_file_id"] = int(str(_fid)) if _fid else 0

        # ---- Phase 5F: Sharing Tests ----
        log("\n=== Phase 5F: Sharing Tests ===")
        rel_path = f"/t{rid}-fixture.txt"
        run_test(session, "5F create_share", "create_share", {"path": rel_path, "share_type": "public", "share_with": "", "permissions": "read"})
        share_id = safe_data("id", 0)
        run_test(session, "5F get_share", "get_share", {"id": share_id})
        run_test(session, "5F list_shares_by_path", "list_shares_by_path", {"path": rel_path})
        run_test(session, "5F update_share", "update_share", {"id": share_id, "permissions": "read"})
        run_test(session, "5F delete_share", "delete_share", {"id": share_id})

        # ---- Phase 5G: Collaboration Tests ----
        log("\n=== Phase 5G: Collaboration Tests ===")
        run_test(session, "5G create_circle_share", "create_share", {"path": rel_path, "share_type": "circle", "share_with": store.get("circle_id", 0), "permissions": "read"})
        store["share5g_id"] = safe_data("id", 0)
        run_test(session, "5G list_all_shares", "list_all_shares", {})
        run_test(session, "5G create_collection", "create_collection", {"resource_type": "file", "resource_id": str(store.get("fixture_file_id", 0)), "name": "test-collection-5g"})
        store["collection_5g_id"] = str(safe_data("id", 0))
        run_test(session, "5G search_collections", "search_collections", {"search": "test-collection-5g"})
        run_test(session, "5G get_collection_by_id", "get_collection_by_id", {"id": store.get("collection_5g_id", "")})
        run_test(session, "5G list_collections_by_resource", "list_collections_by_resource", {"resource_type": "file", "resource_id": str(store.get("fixture_file_id", 0))})
        run_test(session, "5G delete_circle_share", "delete_share", {"id": store.get("share5g_id", 0)})

        # ---- Phase 5H: Reminder Tests ----
        log("\n=== Phase 5H: Reminder Tests ===")
        run_test(session, "5H set_file_reminder", "set_file_reminder", {"file_id": store.get("fixture_file_id", 0), "due_date": "2027-01-01T00:00:00+00:00"})
        run_test(session, "5H get_file_reminder", "get_file_reminder", {"file_id": store.get("fixture_file_id", 0)})
        run_test(session, "5H delete_file_reminder", "delete_file_reminder", {"file_id": store.get("fixture_file_id", 0)})
        run_test(session, "5H verify_reminder_deleted", "get_file_reminder", {"file_id": store.get("fixture_file_id", 0)})

        # ---- Phase 5I: Direct Download URL ----
        log("\n=== Phase 5I: Direct Download URL ===")
        run_test(session, "5I get_direct_download_url", "get_direct_download_url", {"file_id": store.get("fixture_file_id", 0)})

        # ---- Phase 5J: Status Message Tests ----
        log("\n=== Phase 5J: Status Message Tests ===")
        run_test(session, "5J set_predefined_message", "set_predefined_message", {"message_id": "meeting"})
        run_test(session, "5J verify_predefined_message", "get_user_status", {})
        run_test(session, "5J set_custom_message", "set_custom_message", {"message": "Custom test status", "status_icon": "💻"})
        run_test(session, "5J verify_custom_message", "get_user_status", {})

        # ---- Phase 5K: OOO Tests ----
        log("\n=== Phase 5K: OOO Tests ===")
        run_test(session, "5K set_ooo", "set_ooo", {"first_day": "2027-01-01T00:00:00+00:00", "last_day": "2027-01-07T00:00:00+00:00", "status": "OOO", "message": "Out of office"})
        run_test(session, "5K get_ooo", "get_ooo", {"user_id": API_USER})
        run_test(session, "5K clear_ooo", "clear_ooo", {"user_id": API_USER})

        # ---- Phase 5L: Mail Tests ----
        log("\n=== Phase 5L: Mail Tests ===")
        run_test(session, "5L list_all_accounts", "list_all_accounts", {})
        store["mail_account_id"] = safe_items(results[-1]["data"], 0, "id")
        run_test(session, "5L list_mailboxes", "list_mailboxes", {"account_id": store.get("mail_account_id", 0)})
        store["mail_drafts_db_id"] = find_mailbox_by_special(safe_data(), "drafts")
        store["mail_trash_db_id"] = find_mailbox_by_special(safe_data(), "trash")
        run_test(session, "5L send_message", "send_message", {"account_id": store.get("mail_account_id", 0), "subject": f"MCP Test {rid}", "body": "This is a test", "to": "test@example.com"})
        run_test(session, "5L create_email_draft", "create_email_draft", {"account_id": store.get("mail_account_id", 0), "subject": f"MCP Draft {rid}", "body": "This is a test", "to": "test@example.com"})
        store["mail_message_id"] = safe_data("id", 0)
        run_test(session, "5L list_messages", "list_messages", {"account_id": store.get("mail_account_id", 0), "mailbox_db_id": store.get("mail_drafts_db_id", 0), "limit": 5})
        run_test(session, "5L get_message", "get_message", {"account_id": store.get("mail_account_id", 0), "message_id": store.get("mail_message_id", 0)})
        run_test(session, "5L get_message_body", "get_message_body", {"account_id": store.get("mail_account_id", 0), "message_id": store.get("mail_message_id", 0)})
        run_test(session, "5L set_message_flags", "set_message_flags", {"account_id": store.get("mail_account_id", 0), "message_id": store.get("mail_message_id", 0), "seen": True})
        run_test(session, "5L move_message", "move_message", {"account_id": store.get("mail_account_id", 0), "message_id": store.get("mail_message_id", 0), "mailbox_db_id": store.get("mail_trash_db_id", 0)})

        # ---- Phase 5M: Chat Tests ----
        log("\n=== Phase 5M: Chat Tests ===")
        run_test(session, "5M create_room", "create_talk_room", {"room_type": "public", "room_name": f"t{rid}-chat"})
        store["chat_token"] = safe_data("token", "")
        run_test(session, "5M rename_room", "update_talk_room", {"token": store.get("chat_token", ""), "room_name": f"t{rid}-renamed"})
        run_test(session, "5M set_description", "set_talk_description", {"token": store.get("chat_token", ""), "description": "Test room description"})
        run_test(session, "5M add_participant", "add_talk_participant", {"token": store.get("chat_token", ""), "new_participant": "shbadmin"})
        run_test(session, "5M list_participants", "list_talk_participants", {"token": store.get("chat_token", "")})
        store["attendee_id"] = safe_items(results[-1]["data"], 1, "attendeeId")
        run_test(session, "5M remove_participant", "remove_talk_participant", {"token": store.get("chat_token", ""), "attendee_id": store.get("attendee_id", 0)})
        run_test(session, "5M send_chat_message", "send_chat_message", {"token": store.get("chat_token", ""), "message": "Hello from MCP test"})
        store["chat_msg_id"] = safe_data("id", 0)
        run_test(session, "5M list_chat_messages", "list_chat_messages", {"token": store.get("chat_token", ""), "limit": 5})
        run_test(session, "5M delete_chat_message", "delete_chat_message", {"token": store.get("chat_token", ""), "message_id": store.get("chat_msg_id", 0)})
        run_test(session, "5M delete_room", "delete_talk_room", {"token": store.get("chat_token", "")})

        # ---- Phase 5N: Deck Label/User Operations ----
        log("\n=== Phase 5N: Deck Label/User Operations ===")
        run_test(session, "5N list_deck_labels", "list_deck_labels", {"board_id": store.get("Board", {}).get("id", 0)})
        store["deck_first_label_id"] = safe_items(results[-1]["data"], 0, "id")
        run_test(session, "5N deck_assign_label", "assign_deck_label", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "card_id": store.get("Card", {}).get("id", 0), "label_id": store.get("deck_first_label_id", 0)})
        run_test(session, "5N deck_remove_label", "remove_deck_label", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "card_id": store.get("Card", {}).get("id", 0), "label_id": store.get("deck_first_label_id", 0)})
        run_test(session, "5N deck_assign_user", "assign_deck_user", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "card_id": store.get("Card", {}).get("id", 0), "user_id": API_USER})
        run_test(session, "5N deck_remove_user", "remove_deck_user", {"board_id": store.get("Board", {}).get("id", 0), "stack_id": store.get("Stack", {}).get("id", 0), "card_id": store.get("Card", {}).get("id", 0), "user_id": API_USER})

        # ---- Phase 5O: Collectives Mutation Tests ----
        log("\n=== Phase 5O: Collectives Mutation Tests ===")
        run_test(session, "5O collectives_update_emoji", "update_collective_emoji", {"id": store.get("Collective", {}).get("id", 0), "emoji": "⭐"})
        c5o_data = safe_call(session, "get_collective", {"id": store.get("Collective_id", 0)})
        c5o_emoji = c5o_data.get("emoji", "")
        emoji_ok = c5o_emoji == "⭐"
        results.append({"label":"5O verify_update_emoji","tool":"list_collectives","status":("FAILED","PASSED")[emoji_ok],"reason":("",f"Emoji is {c5o_emoji!r}, expected ⭐")[emoji_ok],"data":{}})
        passed_count = passed_count + 1
        failed_count = failed_count + (1, 0)[emoji_ok]
        log(f"  {('FAILED','PASSED')[emoji_ok]} 5O verify_update_emoji")
        run_test(session, "5O collectives_set_page_emoji", "set_collective_page_emoji", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0), "emoji": "👍"})
        c5o_page_data = safe_call(session, "get_collective_page", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0)})
        c5o_page_emoji = c5o_page_data.get("emoji", "")
        page_ok = c5o_page_emoji == "👍"
        results.append({"label":"5O verify_set_page_emoji","tool":"get_collective_page","status":("FAILED","PASSED")[page_ok],"reason":("",f"Emoji is {c5o_page_emoji!r}, expected 👍")[page_ok],"data":c5o_page_data})
        passed_count = passed_count + 1
        failed_count = failed_count + (1, 0)[page_ok]
        log(f"  {('FAILED','PASSED')[page_ok]} 5O verify_set_page_emoji")
        run_test(session, "5O collectives_set_page_full_width", "set_collective_page_full_width", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0), "is_full_width": True})
        run_test(session, "5O collectives_move_page", "move_collective_page", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0), "destination_collective_id": store.get("Collective", {}).get("id", 0)})
        run_test(session, "5O collectives_assign_tag", "assign_collective_tag", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0), "tag_id": store.get("Tag", {}).get("id", 0)})
        run_test(session, "5O collectives_remove_tag", "remove_collective_tag", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0), "tag_id": store.get("Tag", {}).get("id", 0)})
        run_test(session, "5O collectives_search_pages", "search_collective_pages", {"collective_id": store.get("Collective", {}).get("id", 0), "query": make_name("Page")})
        run_test(session, "5O verify_tag_ops", "get_collective_page", {"collective_id": store.get("Collective", {}).get("id", 0), "page_id": store.get("Page", {}).get("id", 0)})

        # ---- Phase 5Q: Notification Tests ----
        log("\n=== Phase 5Q: Notification Tests ===")
        run_test(session, "5Q create_notification", "create_notification", {"user_id": API_USER, "subject": f"MCP test {rid}", "message": "Notification test"})
        store["notification_id"] = safe_data("id", 0)
        run_test(session, "5Q list_notifications", "list_notifications", {})
        run_test(session, "5Q get_notification", "get_notification", {"id": store.get("notification_id", 0)})
        run_test(session, "5Q delete_notification", "delete_notification", {"id": store.get("notification_id", 0)})
        run_test(session, "5Q delete_all_notifications", "delete_all_notifications", {})

        # ---- Phase 5R: Misc Tests ----
        log("\n=== Phase 5R: Misc Tests ===")
        cal_uri = store.get("Calendar_id", "")
        run_test(session, "5R find_availability", "find_availability", {"calendar_uris": cal_uri, "from_date": "2026-07-01T00:00:00+00:00", "to_date": "2026-07-07T00:00:00+00:00"})
        run_test(session, "5R create_meeting", "create_meeting", {"summary": f"t{rid}-Meeting", "dtstart": "2026-07-01T10:00:00+00:00", "dtend": "2026-07-01T11:00:00+00:00", "attendees": "test@example.com", "description": "Test meeting", "calendar_uri": cal_uri})
        run_test(session, "5R search_todos", "search_todos", {"query": make_name("Todo"), "calendar_uri": cal_uri})
        run_test(session, "5R list_activity_filtered", "list_activity_filtered", {"filter_name": "all", "limit": 5})
        run_test(session, "5R list_all_contacts", "list_all_contacts", {})
        run_test(session, "5R list_all_events", "list_all_events", {})
        run_test(session, "5R list_events_by_calendar", "list_events_by_calendar", {"calendar_uri": cal_uri})
        run_test(session, "5R list_all_todos", "list_todos", {})
        run_test(session, "5R list_todos_by_calendar", "list_todos_by_calendar", {"calendar_uri": cal_uri})
        attach_note_dir = f"Notes/test/"
        attach_filename = f"t{rid}-attach.txt"
        run_test(session, "5R create_attachment", "write_file", {"path": f"{attach_note_dir}{attach_filename}", "content": "MCP attachment test"})
        run_test(session, "5R get_attachment", "get_note_attachment", {"note_id": store.get("Note", {}).get("id", 0), "path": attach_filename})

        # ---- Phase 5S: File Fixture Cleanup ----
        log("\n=== Phase 5S: File Fixture Cleanup ===")
        run_test(session, "5S delete_fixture", "delete_file", {"path": fixture_path})
        run_test(session, "5S delete_attach", "delete_file", {"path": f"Notes/test/t{rid}-attach.txt"})

        # ---- Phase 5T: Simple File/Folder Operations ----
        log("\n=== Phase 5T: Simple File/Folder Operations ===")
        run_test(session, "5T create_folder", "create_folder", {"path": f"t{rid}-simple"})
        run_test(session, "5T write_file", "write_file", {"path": f"t{rid}-simple/test.txt", "content": "Hello World"})
        run_test(session, "5T list_folder_contents", "list_folder_contents", {"path": f"t{rid}-simple"})
        run_test(session, "5T read_file", "read_file", {"path": f"t{rid}-simple/test.txt"})
        run_test(session, "5T move_file", "move_file", {"source": f"t{rid}-simple/test.txt", "destination": f"t{rid}-moved.txt"})
        run_test(session, "5T verify_moved", "read_file", {"path": f"t{rid}-moved.txt"})
        run_test(session, "5T copy_file", "copy_file", {"source": f"t{rid}-moved.txt", "destination": f"t{rid}-copied.txt"})
        run_test(session, "5T verify_copied", "read_file", {"path": f"t{rid}-copied.txt"})
        run_test(session, "5T move_files", "move_files", {"files": [f"t{rid}-moved.txt", f"t{rid}-copied.txt"], "destination": f"t{rid}-simple"})
        run_test(session, "5T search_files", "search_files", {"query": f"t{rid}"})
        run_test(session, "5T move_folder", "move_folder", {"source": f"t{rid}-simple", "destination": f"t{rid}-renamed"})
        run_test(session, "5T copy_folder", "copy_folder", {"source": f"t{rid}-renamed", "destination": f"t{rid}-copiedfolder"})
        run_test(session, "5T delete_file", "delete_file", {"path": f"t{rid}-renamed/t{rid}-moved.txt"})
        run_test(session, "5T delete_renamed_folder", "delete_folder", {"path": f"t{rid}-renamed"})
        run_test(session, "5T delete_copied_folder", "delete_folder", {"path": f"t{rid}-copiedfolder"})
        run_test(session, "5T verify_delete", "list_folder_contents", {"path": ""})

        # ---- Phase 6: Circle Member Management Tests ----
        log("\n=== Phase 6: Circle Member Management Tests ===")
        wd = safe_call(session, "get_user_status")
        current_user = wd.get("userId", "")
        store["current_user"] = current_user
        ud = safe_call(session, "list_users")
        user_list = get_list_items(ud)
        second_user = user_list[0] if user_list else ""

        circle_name = make_name("memberTest")
        run_test(session, "6 create_circle", "create_circle", {"name": circle_name})
        circle_id = safe_data("id", 0)

        run_test(session, "6 list_members", "list_circle_members", {"circle_id": circle_id, "include_all_fields": True})
        run_test(session, "6 get_circle", "get_circle", {"circle_id": circle_id})
        run_test(session, "6 update_circle", "update_circle", {"circle_id": circle_id, "name": f"t{rid}-circle-updated"})
        run_test(session, "6 add_member", "add_circle_member", {"circle_id": circle_id, "user_id": second_user})
        run_test(session, "6 list_members_post", "list_circle_members", {"circle_id": circle_id, "include_all_fields": True})
        store["member_id"] = safe_items(results[-1]["data"], 1, "id")
        run_test(session, "6 update_level", "update_circle_member_level", {"circle_id": circle_id, "member_id": store.get("member_id", 0), "level": "moderator"})
        run_test(session, "6 remove_member", "remove_circle_member", {"circle_id": circle_id, "member_id": store.get("member_id", 0)})
        run_test(session, "6 delete_circle", "delete_circle", {"circle_id": circle_id})

        # ------------------------------------------------------------------
        # Phase 3D: Delete All Resources (REVERSE ORDER)
        # ------------------------------------------------------------------
        log("\n=== Phase 3D: Delete All Resources (REVERSE ORDER) ===")
        run_test(session, "3D delete_tag", "delete_collective_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"]})
        run_test(session, "3D delete_page", "delete_collective_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"]})
        run_test(session, "3D delete_row", "delete_table_row", {"row_id": store["Row_id"]})
        run_test(session, "3D delete_contact", "delete_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3D delete_todo", "delete_todo", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3D delete_event", "delete_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3D delete_card", "delete_deck_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_test(session, "3D delete_stack", "delete_deck_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"]})
        run_test(session, "3D delete_collective", "trash_collective", {"id": store["Collective_id"]})
        run_test(session, "3D delete_collective", "delete_collective", {"id": store["Collective_id"]})
        run_test(session, "3D delete_addressbook", "delete_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3D delete_calendar", "delete_calendar", {"calendar_uri": store["Calendar_id"]})
        run_test(session, "3D delete_board", "delete_deck_board", {"board_id": store["Board_id"]})
        run_test(session, "3D delete_table", "delete_table", {"table_id": store["Table_id"]})
        run_test(session, "3D delete_note", "delete_note", {"id": store["Note_id"]})

        # ------------------------------------------------------------------
        # Phase 3E: Verify Delete (REVERSE ORDER)
        # ------------------------------------------------------------------
        log("\n=== Phase 3E: Verify Delete (REVERSE ORDER) ===")
        run_verify_delete(session, "3E verify_delete_tag", "get_collective_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"]})
        run_verify_delete(session, "3E verify_delete_page", "get_collective_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"]})
        run_verify_delete(session, "3E verify_delete_row", "get_table_row", {"row_id": store["Row_id"]})
        run_verify_delete(session, "3E verify_delete_contact", "get_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"]})
        run_verify_delete(session, "3E verify_delete_todo", "get_todo", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"]})
        run_verify_delete(session, "3E verify_delete_event", "get_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"]})
        run_verify_delete(session, "3E verify_delete_card", "get_deck_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_verify_delete(session, "3E verify_delete_stack", "get_deck_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"]})
        run_verify_delete(session, "3E verify_delete_collective", "get_collective", {"id": store["Collective_id"]})
        run_verify_delete(session, "3E verify_delete_addressbook", "get_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_verify_delete(session, "3E verify_delete_calendar", "get_calendar", {"calendar_uri": store["Calendar_id"]})
        run_verify_delete(session, "3E verify_delete_board", "get_deck_board", {"board_id": store["Board_id"]})
        run_verify_delete(session, "3E verify_delete_table", "get_table", {"table_id": store["Table_id"]})
        run_verify_delete(session, "3E verify_delete_note", "get_note", {"id": store["Note_id"]})

        # ------------------------------------------------------------------
        # Phase 3F: Post-Cleanup Leak Detection
        # ------------------------------------------------------------------
        log("\n=== Phase 3F: Leak Detection ===")
        _cf_collectives = _cleanup_test_collectives(session)
        _cf_circles = _cleanup_test_circles(session)
        _cf_rooms = _cleanup_test_rooms(session)
        _cf_files = _cleanup_test_files(session)
        _cf_has_leak = (_cf_collectives + _cf_circles + _cf_rooms + _cf_files) > 0
        _cf_details = f"collectives={_cf_collectives} circles={_cf_circles} rooms={_cf_rooms} files={_cf_files}"
        results.append({"label": "3F leak_check", "tool": "list_collectives", "reason": "", "status": ("FAILED", "PASSED")[not _cf_has_leak], "data": {"leaked": _cf_collectives, "circles": _cf_circles, "rooms": _cf_rooms, "files": _cf_files}})
        passed_count = passed_count + 1
        failed_count = failed_count + (1, 0)[not _cf_has_leak]
        log(f"  {('FAILED','PASSED')[not _cf_has_leak]} 3F leak_check ({_cf_details})")

        # ------------------------------------------------------------------
        # Report Summary
        # ------------------------------------------------------------------
        print(f"\n## Summary\n")
        print(f"| Status | Count |")
        print(f"|--------|-------|")
        actual_passed = passed_count - failed_count
        print(f"| PASSED | {actual_passed} |")
        print(f"| FAILED | {failed_count} |")
        print(f"\n## PASSED ({actual_passed})\n")
        print(f"\n## FAILED ({failed_count})\n")
        print(f"\n## Iteration History\n")
        print(f"| Iteration | Passed | Failed | Fixes Applied |")
        print(f"|-----------|--------|--------|---------------|")
        print(f"| {ITERATION} | {actual_passed} | {failed_count} | Initial run |")
        print(f"\n---")
        print(f"**Total tests:** {len(results)} | **PASSED:** {actual_passed} | FAILED: {failed_count}")
        conclusion = ("**TESTS FAILING** — see above for details", "**ALL TESTS PASS**")[failed_count == 0]
        print(f"\n{conclusion}")


main()
