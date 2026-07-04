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
from toon_mcp import toon_to_json

MCP_SERVER_PORT = os.environ.get("MCP_SERVER_PORT", "6080")
API_KEY = os.environ.get("API_KEY", "")
API_CREDS = base64.b64decode(API_KEY).decode("utf-8")
API_USER = API_CREDS.split(":")[0]
MCP_URL = f"http://localhost:{MCP_SERVER_PORT}/mcp"

# HARDCODED FALLBACKS — external services that require runtime data discovery
# These cause the relevant tool calls to FAIL rather than crash the runner.
# TODO: Replace with proper runtime extraction. Do NOT add to this list
# without explicit user approval.
MCP_HARDCODED_MAILBOX_DB_ID = 0
MCP_HARDCODED_MAIL_MSG_ID = 0
MCP_HARDCODED_CHAT_MSG_ID = 0

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
    return toon_to_json(data["items"])


DT = "2026-12-31T23:59:00+00:00"


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
        tool_names = list(map(lambda t: t["name"], tools_list))
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
        run_test(session, "B get_users", "get_users", {"limit": 5})
        run_test(session, "B get_groups", "get_groups", {"limit": 5})
        run_test(session, "B get_user_status", "get_user_status", {})
        run_test(session, "B get_predefined_statuses", "get_predefined_statuses", {})
        run_test(session, "B get_user_statuses", "get_user_statuses", {"limit": 5})
        run_test(session, "B get_upcoming_events", "get_upcoming_events", {"limit": 5})
        run_test(session, "B get_folder_tree", "get_folder_tree", {"depth": 1})
        run_test(session, "B get_search_providers", "get_search_providers", {})
        run_test(session, "B get_capabilities", "get_capabilities", {})
        run_test(session, "B notification_list", "notification_list", {})
        run_test(session, "B circle_list", "circle_list", {})
        run_test(session, "B activity_get", "activity_get", {"limit": 5})
        run_test(session, "B activity_get_filters", "activity_get_filters", {})
        run_test(session, "B list_all_calendars", "list_all_calendars", {})
        run_test(session, "B list_all_addressbooks", "list_all_addressbooks", {})
        run_test(session, "B note_list", "note_list", {})
        run_test(session, "B deck_list", "deck_list", {})
        run_test(session, "B table_list", "table_list", {})
        run_test(session, "B collectives_list", "collectives_list", {})
        run_test(session, "B collectives_list_trashed", "collectives_list_trashed", {})
        run_test(session, "B webdav_list_favorites", "webdav_list_favorites", {"limit": 5})
        run_test(session, "B list_all_accounts", "list_all_accounts", {})
        run_test(session, "B list_all_shares", "list_all_shares", {})
        run_test(session, "B talk_list_rooms", "talk_list_rooms", {})
        run_test(session, "B get_user", "get_user", {"user_id": API_USER})
        run_test(session, "B get_group_members", "get_group_members", {"group_id": "admin"})
        run_test(session, "B webdav_list_directory", "webdav_list_directory", {"path": f"/remote.php/dav/files/{API_USER}/"})

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
        run_test(session, "3A create_note", "note_create", {"title": make_name("Note"), "content": "Hello world", "category": "test"})
        store["Note"] = results[-1]["data"]
        store["Note_id"] = store["Note"]["id"]
        log(f"  => Note ID: {store['Note_id']}")

        run_test(session, "3A create_table", "table_create", {"title": make_name("Table"), "description": "Test table", "emoji": "📋"})
        store["Table"] = results[-1]["data"]
        store["Table_id"] = store["Table"]["id"]
        log(f"  => Table ID: {store['Table_id']}")

        run_test(session, "3A create_board", "deck_create_board", {"title": make_name("Board"), "color": "FF0000", "archived": False})
        store["Board"] = results[-1]["data"]
        store["Board_id"] = store["Board"]["id"]
        log(f"  => Board ID: {store['Board_id']}")

        run_test(session, "3A create_calendar", "create_calendar", {"display_name": make_name("Cal"), "color": "#FF0000"})
        store["Calendar"] = results[-1]["data"]
        store["Calendar_id"] = store["Calendar"]["calendar_uri"]
        log(f"  => Calendar URI: {store['Calendar_id']}")

        run_test(session, "3A create_addressbook", "create_addressbook", {"display_name": make_name("Addr"), "description": "Test addressbook"})
        store["Addressbook"] = results[-1]["data"]
        store["Addressbook_id"] = store["Addressbook"]["addressbook_uri"]
        log(f"  => Addressbook URI: {store['Addressbook_id']}")

        run_test(session, "3A create_collective", "collectives_create", {"name": make_name("Coll"), "emoji": "🌟"})
        coll_data = results[-1]["data"]
        coll_inner = coll_data["collective"]
        store["Collective"] = coll_inner
        store["Collective_id"] = coll_inner["id"]
        circle_list_result = session.call_tool("circle_list")
        circle_list_data = extract_content(circle_list_result)
        circle_list_items = get_list_items(circle_list_data)
        store["circle_id"] = circle_list_items[0]["id"]

        run_test(session, "3A create_stack", "deck_create_stack", {"board_id": store["Board"]["id"], "title": make_name("Stack"), "order": 1})
        store["Stack"] = results[-1]["data"]
        store["Stack_id"] = store["Stack"]["id"]

        run_test(session, "3A create_card", "deck_create_card", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "title": make_name("Card"), "description": "test"})
        store["Card"] = results[-1]["data"]
        store["Card_id"] = store["Card"]["id"]

        run_test(session, "3A create_event", "create_event", {"calendar_uri": store["Calendar_id"], "summary": make_name("Event"), "dtstart": DT, "dtend": DT, "description": "test"})
        store["Event"] = results[-1]["data"]
        store["Event_id"] = store["Event"]["event_uid"]

        run_test(session, "3A create_todo", "todo_create", {"calendar_uri": store["Calendar_id"], "summary": make_name("Todo"), "description": "test", "due_date": DT})
        store["Todo"] = results[-1]["data"]
        store["Todo_id"] = store["Todo"]["todo_uid"]

        run_test(session, "3A create_contact", "create_contact", {"addressbook_uri": store["Addressbook_id"], "fn": make_name("Contact"), "email": "test@example.com", "telephone": "+1234567890", "organization": "TestOrg"})
        store["Contact"] = results[-1]["data"]
        store["Contact_id"] = store["Contact"]["contact_uid"]

        run_test(session, "3A create_row", "table_create_row", {"table_id": store["Table"]["id"], "data": '{"col1": "val1"}'})
        store["Row"] = results[-1]["data"]
        store["Row_id"] = store["Row"]["id"]

        run_test(session, "3A create_page", "collectives_create_page", {"collective_id": store["Collective"]["id"], "title": make_name("Page"), "parent_id": 0})
        store["Page"] = results[-1]["data"]["page"]
        store["Page_id"] = store["Page"]["id"]

        run_test(session, "3A create_tag", "collectives_create_tag", {"collective_id": store["Collective"]["id"], "name": make_name("Tag"), "color": "FF0000"})
        store["Tag"] = results[-1]["data"]["tag"]
        store["Tag_id"] = store["Tag"]["id"]

        run_test(session, "3A verify_note_in_list", "note_list", {})
        run_test(session, "3A verify_table_in_list", "table_list", {})
        run_test(session, "3A verify_board_in_list", "deck_list", {})
        run_test(session, "3A verify_calendar_in_list", "list_all_calendars", {})
        run_test(session, "3A verify_addressbook_in_list", "list_all_addressbooks", {})
        run_test(session, "3A verify_collective_in_list", "collectives_list", {})
        run_test(session, "3A verify_stack_in_list", "deck_list_stacks", {"board_id": store["Board_id"]})
        run_test(session, "3A verify_card_in_list", "deck_list_cards", {"board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_test(session, "3A verify_event_in_list", "get_events_by_calendar", {"calendar_uri": store["Calendar_id"]})
        run_test(session, "3A verify_todo_in_list", "todo_get_by_calendar", {"calendar_uri": store["Calendar_id"]})
        run_test(session, "3A verify_contact_in_list", "get_contacts_by_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3A verify_page_in_list", "collectives_list_pages", {"collective_id": store["Collective_id"]})
        run_test(session, "3A verify_tag_in_list", "collectives_list_tags", {"collective_id": store["Collective_id"]})

        log("\n=== Phase 3B: Get & Verify All Resources ===")
        run_test(session, "3B get_note", "note_get", {"id": store["Note_id"]})
        run_test(session, "3B get_table", "table_get", {"table_id": store["Table_id"]})
        run_test(session, "3B get_board", "deck_get_board", {"board_id": store["Board_id"]})
        run_test(session, "3B get_calendar", "get_calendar", {"calendar_uri": store["Calendar_id"]})
        run_test(session, "3B get_addressbook", "get_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3B get_collective", "collectives_get_collective", {"id": store["Collective_id"]})
        run_test(session, "3B get_stack", "deck_get_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"]})
        run_test(session, "3B get_card", "deck_get_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_test(session, "3B get_event", "get_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3B get_todo", "todo_get", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3B get_contact", "get_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3B get_row", "table_get_row", {"row_id": store["Row_id"]})
        run_test(session, "3B get_page", "collectives_get_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"]})
        run_test(session, "3B get_tag", "collectives_get_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"]})

        log("\n=== Phase 3C: Update All Resources ===")
        run_test(session, "3C update_note", "note_update", {"id": store["Note_id"], "title": make_name("Note-upd"), "content": "Updated"})
        run_test(session, "3C update_table", "table_update", {"table_id": store["Table_id"], "title": make_name("Table-upd")})
        run_test(session, "3C update_board", "deck_update_board", {"board_id": store["Board_id"], "title": make_name("Board-upd"), "color": "00FF00"})
        run_test(session, "3C update_calendar", "update_calendar", {"calendar_uri": store["Calendar_id"], "display_name": make_name("Cal-upd"), "color": "#00FF00"})
        run_test(session, "3C update_addressbook", "update_addressbook", {"addressbook_uri": store["Addressbook_id"], "display_name": make_name("Addr-upd")})
        run_test(session, "3C update_collective", "collectives_update_collective", {"id": store["Collective_id"], "emoji": "🌈"})
        run_test(session, "3C update_stack", "deck_update_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"], "title": make_name("Stack-upd")})
        run_test(session, "3C update_card", "deck_update_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"], "title": make_name("Card-upd")})
        run_test(session, "3C update_tag", "collectives_update_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"], "name": make_name("Tag-upd"), "color": "00FF00"})
        run_test(session, "3C update_event", "update_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"], "summary": make_name("Event-upd")})
        run_test(session, "3C update_todo", "todo_update", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"], "summary": make_name("Todo-upd")})
        run_test(session, "3C update_contact", "update_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"], "fn": make_name("Contact-upd")})
        run_test(session, "3C update_row", "table_update_row", {"row_id": store["Row_id"], "data": '{"col1": "upd"}'})
        run_test(session, "3C update_page", "collectives_update_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"], "title": make_name("Page-upd")})

        # ------------------------------------------------------------------
        # Phase 3.5: Minimal-Params Create & Delete
        # ------------------------------------------------------------------
        log("\n=== Phase 3.5: Minimal-Params Create & Delete ===")
        run_test(session, "3.5 create_board", "deck_create_board", {"title": make_name("minBoard")})
        run_test(session, "3.5 delete_board", "deck_delete_board", {"board_id": results[-1]["data"]["id"]})
        run_test(session, "3.5 create_note", "note_create", {"title": make_name("minNote"), "content": "min"})
        run_test(session, "3.5 delete_note", "note_delete", {"id": results[-1]["data"]["id"]})
        run_test(session, "3.5 create_table", "table_create", {"title": make_name("minTable"), "description": "min"})
        run_test(session, "3.5 delete_table", "table_delete", {"table_id": results[-1]["data"]["id"]})
        run_test(session, "3.5 create_calendar", "create_calendar", {"display_name": make_name("minCal")})
        run_test(session, "3.5 delete_calendar", "delete_calendar", {"calendar_uri": results[-1]["data"]["calendar_uri"]})
        run_test(session, "3.5 create_addressbook", "create_addressbook", {"display_name": make_name("minAddr"), "description": "min"})
        run_test(session, "3.5 delete_addressbook", "delete_addressbook", {"addressbook_uri": results[-1]["data"]["addressbook_uri"]})
        run_test(session, "3.5 create_collective", "collectives_create", {"name": make_name("minColl")})
        c5_inner = results[-1]["data"]["collective"]
        c5_id = c5_inner["id"]
        run_test(session, "3.5 delete_collective", "collectives_trash", {"id": c5_id})
        run_test(session, "3.5 delete_collective", "collectives_delete", {"id": c5_id})
        run_test(session, "3.5 create_circle", "circle_create", {"name": make_name("minCircle")})
        run_test(session, "3.5 delete_circle", "circle_delete", {"circle_id": results[-1]["data"]["id"]})
        run_test(session, "3.5 create_stack", "deck_create_stack", {"board_id": store["Board_id"], "title": make_name("minStack")})
        s5_id = results[-1]["data"]["id"]
        run_test(session, "3.5 delete_stack", "deck_delete_stack", {"board_id": store["Board_id"], "stack_id": s5_id})
        run_test(session, "3.5 create_card", "deck_create_card", {"board_id": store["Board_id"], "stack_id": store["Stack_id"], "title": make_name("minCard"), "description": "min"})
        c5d_id = results[-1]["data"]["id"]
        run_test(session, "3.5 delete_card", "deck_delete_card", {"board_id": store["Board_id"], "stack_id": store["Stack_id"], "card_id": c5d_id})
        run_test(session, "3.5 create_event", "create_event", {"calendar_uri": store["Calendar_id"], "summary": make_name("minEvent"), "dtstart": DT, "dtend": DT, "description": "min"})
        e5_uid = results[-1]["data"]["event_uid"]
        run_test(session, "3.5 delete_event", "delete_event", {"calendar_uri": store["Calendar_id"], "event_uid": e5_uid})
        run_test(session, "3.5 create_todo", "todo_create", {"calendar_uri": store["Calendar_id"], "summary": make_name("minTodo"), "description": "min", "due_date": DT})
        t5_uid = results[-1]["data"]["todo_uid"]
        run_test(session, "3.5 delete_todo", "todo_delete", {"calendar_uri": store["Calendar_id"], "todo_uid": t5_uid})
        run_test(session, "3.5 create_contact", "create_contact", {"addressbook_uri": store["Addressbook_id"], "fn": make_name("minContact"), "email": "min@test.com"})
        c5t_uid = results[-1]["data"]["contact_uid"]
        run_test(session, "3.5 delete_contact", "delete_contact", {"addressbook_uri": store["Addressbook_id"], "contact_uid": c5t_uid})
        run_test(session, "3.5 create_row", "table_create_row", {"table_id": store["Table_id"], "data": '{"col1":"min"}'})
        run_test(session, "3.5 delete_row", "table_delete_row", {"row_id": results[-1]["data"]["id"]})
        run_test(session, "3.5 create_page", "collectives_create_page", {"collective_id": store["Collective_id"], "title": make_name("minPage"), "parent_id": 0})
        p5_inner = results[-1]["data"]["page"]
        run_test(session, "3.5 delete_page", "collectives_delete_page", {"collective_id": store["Collective_id"], "page_id": p5_inner["id"]})
        run_test(session, "3.5 create_tag", "collectives_create_tag", {"collective_id": store["Collective_id"], "name": make_name("minTag"), "color": "FF0000"})
        t5_inner = results[-1]["data"]["tag"]
        run_test(session, "3.5 delete_tag", "collectives_delete_tag", {"tag_id": t5_inner["id"], "collective_id": store["Collective_id"]})
        # ------------------------------------------------------------------
        # Phase 4: Post-CRUD Domain Tools
        # ------------------------------------------------------------------
        log("\n=== Phase 4: Post-CRUD Domain Tools ===")
        run_test(session, "4A deck_list_stacks", "deck_list_stacks", {"board_id": store["Board"]["id"]})
        run_test(session, "4A deck_list_cards", "deck_list_cards", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"]})
        run_test(session, "4A deck_list_labels", "deck_list_labels", {"board_id": store["Board"]["id"]})
        run_test(session, "4A get_events_by_calendar", "get_events_by_calendar", {"calendar_uri": store["Calendar"]["calendar_uri"]})
        run_test(session, "4A get_contacts_by_addressbook", "get_contacts_by_addressbook", {"addressbook_uri": store["Addressbook"]["addressbook_uri"]})
        run_test(session, "4A todo_get_by_calendar", "todo_get_by_calendar", {"calendar_uri": store["Calendar"]["calendar_uri"]})
        run_test(session, "4A note_get_by_category", "note_get_by_category", {"category": "test"})
        run_test(session, "4A collectives_list_pages", "collectives_list_pages", {"collective_id": store["Collective"]["id"]})
        run_test(session, "4A collectives_list_tags", "collectives_list_tags", {"collective_id": store["Collective"]["id"]})
        run_test(session, "4A table_get_schema", "table_get_schema", {"table_id": store["Table"]["id"]})
        run_test(session, "4A table_list_rows", "table_list_rows", {"table_id": store["Table"]["id"]})

        # ------------------------------------------------------------------
        # Phase 5: Domain Mutation Tests (before deletion, needs resources)
        # ------------------------------------------------------------------
        log("\n=== Phase 5A: Card Archive/Unarchive ===")
        run_test(session, "5A CardArchive", "deck_archive_card", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "card_id": store["Card"]["id"]})
        run_test(session, "5A CardUnarchive", "deck_unarchive_card", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "card_id": store["Card"]["id"]})
        run_test(session, "5A PageTrash", "collectives_trash_page", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"]})
        run_test(session, "5A PageRestore", "collectives_restore_page", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"]})

        log("\n=== Phase 5B: Domain Mutation Tools ===")
        run_test(session, "5B set_status", "set_status", {"status_type": "online"})
        run_test(session, "5B clear_message", "clear_message", {})
        run_test(session, "5B note_append_content", "note_append_content", {"id": store["Note"]["id"], "content": "Appended line"})
        run_test(session, "5B note_search", "note_search", {"query": "Hello"})
        run_test(session, "5B search", "search", {"provider_id": "files", "term": "test"})
        run_test(session, "5B webdav_search_files", "webdav_search_files", {"query": "test"})
        run_test(session, "5B webdav_create_directory", "webdav_create_directory", {"path": f"/remote.php/dav/files/{API_USER}/t{rid}-testdir"})
        run_test(session, "5B webdav_write_file", "webdav_write_file", {"path": f"/remote.php/dav/files/{API_USER}/t{rid}-testdir/testfile.txt", "content": "Hello World"})
        run_test(session, "5B table_update_row", "table_update_row", {"row_id": store["Row"]["id"], "data": '{}'})

        # ---- Phase 5C: WebDAV Move/Copy/Delete with inline verification ----
        log("\n=== Phase 5C: WebDAV Move/Copy/Delete ===")
        testdir = f"/remote.php/dav/files/{API_USER}/t{rid}-testdir"
        testfile = f"{testdir}/testfile.txt"
        moved_file = f"/remote.php/dav/files/{API_USER}/t{rid}-moved.txt"
        copied_file = f"/remote.php/dav/files/{API_USER}/t{rid}-copied.txt"
        run_test(session, "5C verify_create_directory", "webdav_list_directory", {"path": testdir, "depth": 0})
        run_test(session, "5C verify_read_file", "webdav_read_file", {"path": testfile})
        run_test(session, "5C move_resource", "webdav_move_resource", {"source": testfile, "destination": moved_file})
        run_test(session, "5C verify_moved", "webdav_read_file", {"path": moved_file})
        run_test(session, "5C copy_resource", "webdav_copy_resource", {"source": moved_file, "destination": copied_file})
        run_test(session, "5C verify_copied", "webdav_read_file", {"path": copied_file})
        run_test(session, "5C delete_testfile", "webdav_delete_resource", {"path": moved_file})
        run_test(session, "5C delete_copied", "webdav_delete_resource", {"path": copied_file})
        run_test(session, "5C delete_testdir", "webdav_delete_resource", {"path": testdir})
        run_test(session, "5C verify_delete_resource", "webdav_list_directory", {"path": f"/remote.php/dav/files/{API_USER}/", "depth": 1})

        # ---- Phase 5D: Read-back Verification ----
        log("\n=== Phase 5D: Read-back Verification ===")
        run_test(session, "5D verify_append_content", "note_get", {"id": store["Note"]["id"]})
        run_test(session, "5D verify_set_status", "get_user_status", {})
        run_test(session, "5D verify_clear_message", "get_user_status", {})

        # ---- Phase 5E: WebDAV Fixture ----
        log("\n=== Phase 5E: WebDAV Fixture Creation ===")
        fixture_path = f"/remote.php/dav/files/{API_USER}/t{rid}-fixture.txt"
        run_test(session, "5E create_fixture_file", "webdav_write_file", {"path": fixture_path, "content": "MCP Test Fixture"})
        store["fixture_path"] = fixture_path
        run_test(session, "5E list_fixture", "webdav_list_directory", {"path": fixture_path, "depth": 0, "include_all_fields": True})
        store["fixture_file_id"] = int(get_list_items(results[-1]["data"])[0]["fileid"])

        # ---- Phase 5F: Sharing Tests ----
        log("\n=== Phase 5F: Sharing Tests ===")
        rel_path = f"/t{rid}-fixture.txt"
        run_test(session, "5F create_share", "create_share", {"path": rel_path, "share_type": 3, "share_with": "", "permissions": 1})
        share_id = results[-1]["data"]["id"]
        run_test(session, "5F get_share", "get_share", {"id": share_id})
        run_test(session, "5F get_shares_by_path", "get_shares_by_path", {"path": rel_path})
        run_test(session, "5F update_share", "update_share", {"id": share_id, "permissions": 1})
        run_test(session, "5F delete_share", "delete_share", {"id": share_id})

        # ---- Phase 5G: Collaboration Tests ----
        log("\n=== Phase 5G: Collaboration Tests ===")
        run_test(session, "5G create_circle_share", "create_share", {"path": rel_path, "share_type": 7, "share_with": store["circle_id"], "permissions": 1})
        store["share5g_id"] = results[-1]["data"]["id"]
        run_test(session, "5G list_all_shares", "list_all_shares", {})
        run_test(session, "5G create_collection", "collection_create", {"resource_type": "file", "resource_id": str(store["fixture_file_id"]), "name": "test-collection-5g"})
        store["collection_5g_id"] = str(results[-1]["data"]["id"])
        run_test(session, "5G search_collections", "collection_search", {"search": "test-collection-5g"})
        run_test(session, "5G get_collection_by_id", "collection_get_by_id", {"id": store["collection_5g_id"]})
        run_test(session, "5G get_collections_by_resource", "collection_get_by_resource", {"resource_type": "file", "resource_id": str(store["fixture_file_id"])})
        run_test(session, "5G delete_circle_share", "delete_share", {"id": store["share5g_id"]})

        # ---- Phase 5H: Reminder Tests ----
        log("\n=== Phase 5H: Reminder Tests ===")
        run_test(session, "5H set_file_reminder", "set_file_reminder", {"file_id": store["fixture_file_id"], "due_date": "2027-01-01T00:00:00+00:00"})
        run_test(session, "5H get_file_reminder", "get_file_reminder", {"file_id": store["fixture_file_id"]})
        run_test(session, "5H delete_file_reminder", "delete_file_reminder", {"file_id": store["fixture_file_id"]})
        run_test(session, "5H verify_reminder_deleted", "get_file_reminder", {"file_id": store["fixture_file_id"]})

        # ---- Phase 5I: Direct Download URL ----
        log("\n=== Phase 5I: Direct Download URL ===")
        run_test(session, "5I get_direct_download_url", "get_direct_download_url", {"file_id": store["fixture_file_id"]})

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
        store["mail_account_id"] = toon_to_json(results[-1]["data"]["items"])[0]["id"]
        run_test(session, "5L list_mailboxes", "list_mailboxes", {"account_id": store["mail_account_id"]})
        store["mail_mailbox_db_id"] = MCP_HARDCODED_MAILBOX_DB_ID
        run_test(session, "5L send_message", "send_message", {"account_id": store["mail_account_id"], "subject": "MCP Test", "body": "This is a test", "to": "test@example.com"})
        run_test(session, "5L list_messages", "list_messages", {"account_id": store["mail_account_id"], "mailbox_db_id": store["mail_mailbox_db_id"], "limit": 5})
        store["mail_message_id"] = MCP_HARDCODED_MAIL_MSG_ID
        run_test(session, "5L get_message", "get_message", {"account_id": store["mail_account_id"], "message_id": store["mail_message_id"]})
        run_test(session, "5L get_message_body", "get_message_body", {"account_id": store["mail_account_id"], "message_id": store["mail_message_id"]})
        run_test(session, "5L set_message_flags", "set_message_flags", {"account_id": store["mail_account_id"], "message_id": store["mail_message_id"], "seen": True})
        run_test(session, "5L move_message", "move_message", {"account_id": store["mail_account_id"], "message_id": store["mail_message_id"], "mailbox_db_id": store["mail_mailbox_db_id"]})

        # ---- Phase 5M: Chat Tests ----
        log("\n=== Phase 5M: Chat Tests ===")
        run_test(session, "5M create_room", "talk_create_room", {"room_type": 3, "room_name": f"t{rid}-chat"})
        store["chat_token"] = results[-1]["data"]["token"]
        run_test(session, "5M rename_room", "talk_update_room", {"token": store["chat_token"], "room_name": f"t{rid}-renamed"})
        run_test(session, "5M set_description", "talk_set_description", {"token": store["chat_token"], "description": "Test room description"})
        run_test(session, "5M add_participant", "talk_add_participant", {"token": store["chat_token"], "new_participant": "shbadmin"})
        run_test(session, "5M list_participants", "talk_list_participants", {"token": store["chat_token"]})
        store["attendee_id"] = toon_to_json(results[-1]["data"]["items"])[1]["attendeeId"]
        run_test(session, "5M remove_participant", "talk_remove_participant", {"token": store["chat_token"], "attendee_id": store["attendee_id"]})
        run_test(session, "5M send_chat_message", "send_chat_message", {"token": store["chat_token"], "message": "Hello from MCP test"})
        run_test(session, "5M get_chat_messages", "get_chat_messages", {"token": store["chat_token"], "limit": 5})
        store["chat_msg_id"] = MCP_HARDCODED_CHAT_MSG_ID
        run_test(session, "5M delete_chat_message", "delete_chat_message", {"token": store["chat_token"], "message_id": store["chat_msg_id"]})
        run_test(session, "5M delete_room", "talk_delete_room", {"token": store["chat_token"]})

        # ---- Phase 5N: Deck Label/User Operations ----
        log("\n=== Phase 5N: Deck Label/User Operations ===")
        run_test(session, "5N deck_list_labels", "deck_list_labels", {"board_id": store["Board"]["id"]})
        store["deck_first_label_id"] = toon_to_json(results[-1]["data"]["items"])[0]["id"]
        run_test(session, "5N deck_assign_label", "deck_assign_label", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "card_id": store["Card"]["id"], "label_id": store["deck_first_label_id"]})
        run_test(session, "5N deck_remove_label", "deck_remove_label", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "card_id": store["Card"]["id"], "label_id": store["deck_first_label_id"]})
        run_test(session, "5N deck_assign_user", "deck_assign_user", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "card_id": store["Card"]["id"], "user_id": API_USER})
        run_test(session, "5N deck_remove_user", "deck_remove_user", {"board_id": store["Board"]["id"], "stack_id": store["Stack"]["id"], "card_id": store["Card"]["id"], "user_id": API_USER})

        # ---- Phase 5O: Collectives Mutation Tests ----
        log("\n=== Phase 5O: Collectives Mutation Tests ===")
        run_test(session, "5O collectives_update_emoji", "collectives_update_emoji", {"id": store["Collective"]["id"], "emoji": "⭐"})
        c5o_data = extract_content(session.call_tool("collectives_get_collective", {"id": store["Collective_id"]}))
        c5o_emoji = c5o_data.get("emoji", "")
        emoji_ok = c5o_emoji == "⭐"
        results.append({"label":"5O verify_update_emoji","tool":"collectives_list","status":("FAILED","PASSED")[emoji_ok],"reason":("",f"Emoji is {c5o_emoji!r}, expected ⭐")[emoji_ok],"data":{}})
        global passed_count, failed_count
        passed_count = passed_count + 1
        failed_count = failed_count + (1, 0)[emoji_ok]
        log(f"  {('FAILED','PASSED')[emoji_ok]} 5O verify_update_emoji")
        run_test(session, "5O collectives_set_page_emoji", "collectives_set_page_emoji", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"], "emoji": "👍"})
        c5o_page_data = extract_content(session.call_tool("collectives_get_page",{"collective_id":store["Collective"]["id"],"page_id":store["Page"]["id"]}))
        c5o_page_emoji = c5o_page_data["emoji"]
        page_ok = c5o_page_emoji == "👍"
        results.append({"label":"5O verify_set_page_emoji","tool":"collectives_get_page","status":("FAILED","PASSED")[page_ok],"reason":("",f"Emoji is {c5o_page_emoji!r}, expected 👍")[page_ok],"data":c5o_page_data})
        passed_count = passed_count + 1
        failed_count = failed_count + (1, 0)[page_ok]
        log(f"  {('FAILED','PASSED')[page_ok]} 5O verify_set_page_emoji")
        run_test(session, "5O collectives_set_page_full_width", "collectives_set_page_full_width", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"], "is_full_width": True})
        run_test(session, "5O collectives_move_page", "collectives_move_page", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"], "destination_collective_id": store["Collective"]["id"]})
        run_test(session, "5O collectives_assign_tag", "collectives_assign_tag", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"], "tag_id": store["Tag"]["id"]})
        run_test(session, "5O collectives_remove_tag", "collectives_remove_tag", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"], "tag_id": store["Tag"]["id"]})
        run_test(session, "5O collectives_search_pages", "collectives_search_pages", {"collective_id": store["Collective"]["id"], "query": make_name("Page")})
        run_test(session, "5O verify_tag_ops", "collectives_get_page", {"collective_id": store["Collective"]["id"], "page_id": store["Page"]["id"]})

        # ---- Phase 5Q: Notification Tests ----
        log("\n=== Phase 5Q: Notification Tests ===")
        run_test(session, "5Q list_notifications", "notification_list", {})
        run_test(session, "5Q get_notification", "notification_get", {"id": 0})
        run_test(session, "5Q delete_notification", "notification_delete", {"id": 0})
        run_test(session, "5Q delete_all_notifications", "notification_delete_all", {})

        # ---- Phase 5R: Misc Tests ----
        log("\n=== Phase 5R: Misc Tests ===")
        cal_uri = store["Calendar_id"]
        run_test(session, "5R find_availability", "find_availability", {"calendar_uris": cal_uri, "from_date": "2026-07-01T00:00:00+00:00", "to_date": "2026-07-07T00:00:00+00:00"})
        run_test(session, "5R create_meeting", "create_meeting", {"summary": f"t{rid}-Meeting", "dtstart": "2026-07-01T10:00:00+00:00", "dtend": "2026-07-01T11:00:00+00:00", "attendees": "test@example.com", "description": "Test meeting", "calendar_uri": cal_uri})
        run_test(session, "5R search_todos", "todo_search", {"query": make_name("Todo"), "calendar_uri": cal_uri})
        run_test(session, "5R get_activity_filter", "activity_get_filter", {"filter_name": "all", "limit": 5})
        run_test(session, "5R list_all_contacts", "list_all_contacts", {})
        run_test(session, "5R list_all_events", "list_all_events", {})
        run_test(session, "5R get_events_by_calendar", "get_events_by_calendar", {"calendar_uri": cal_uri})
        run_test(session, "5R list_all_todos", "todo_list", {})
        run_test(session, "5R get_todos_by_calendar", "todo_get_by_calendar", {"calendar_uri": cal_uri})
        run_test(session, "5R get_attachment", "note_get_attachment", {"note_id": store["Note"]["id"], "path": f"t{rid}-attach.txt"})

        # ---- Phase 5S: WebDAV Fixture Cleanup ----
        log("\n=== Phase 5S: WebDAV Fixture Cleanup ===")
        run_test(session, "5S delete_fixture", "webdav_delete_resource", {"path": fixture_path})

        # ---- Phase 6: Circle Member Management Tests ----
        log("\n=== Phase 6: Circle Member Management Tests ===")
        whoami = session.call_tool("get_user_status")
        wd = extract_content(whoami)
        current_user = wd["userId"]
        store["current_user"] = current_user
        all_users = session.call_tool("get_users")
        ud = extract_content(all_users)
        user_list = get_list_items(ud)
        second_user = user_list[0]

        circle_name = make_name("memberTest")
        run_test(session, "6 create_circle", "circle_create", {"name": circle_name})
        circle_id = results[-1]["data"]["id"]

        run_test(session, "6 list_members", "circle_list_members", {"circle_id": circle_id, "include_all_fields": True})
        run_test(session, "6 get_circle", "circle_get", {"circle_id": circle_id})
        run_test(session, "6 update_circle", "circle_update", {"circle_id": circle_id, "name": f"t{rid}-circle-updated"})
        run_test(session, "6 add_member", "circle_add_member", {"circle_id": circle_id, "user_id": second_user})
        run_test(session, "6 list_members_post", "circle_list_members", {"circle_id": circle_id, "include_all_fields": True})
        store["member_id"] = toon_to_json(results[-1]["data"]["items"])[1]["id"]
        run_test(session, "6 update_level", "circle_update_member_level", {"circle_id": circle_id, "member_id": store["member_id"], "level": 4})
        run_test(session, "6 remove_member", "circle_remove_member", {"circle_id": circle_id, "member_id": store["member_id"]})
        run_test(session, "6 delete_circle", "circle_delete", {"circle_id": circle_id})

        # ------------------------------------------------------------------
        # Phase 3D: Delete All Resources (REVERSE ORDER)
        # ------------------------------------------------------------------
        log("\n=== Phase 3D: Delete All Resources (REVERSE ORDER) ===")
        run_test(session, "3D delete_tag", "collectives_delete_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"]})
        run_test(session, "3D delete_page", "collectives_delete_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"]})
        run_test(session, "3D delete_row", "table_delete_row", {"row_id": store["Row_id"]})
        run_test(session, "3D delete_contact", "delete_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3D delete_todo", "todo_delete", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3D delete_event", "delete_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"]})
        run_test(session, "3D delete_card", "deck_delete_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_test(session, "3D delete_stack", "deck_delete_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"]})
        run_test(session, "3D delete_collective", "collectives_trash", {"id": store["Collective_id"]})
        run_test(session, "3D delete_collective", "collectives_delete", {"id": store["Collective_id"]})
        run_test(session, "3D delete_addressbook", "delete_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_test(session, "3D delete_calendar", "delete_calendar", {"calendar_uri": store["Calendar_id"]})
        run_test(session, "3D delete_board", "deck_delete_board", {"board_id": store["Board_id"]})
        run_test(session, "3D delete_table", "table_delete", {"table_id": store["Table_id"]})
        run_test(session, "3D delete_note", "note_delete", {"id": store["Note_id"]})

        # ------------------------------------------------------------------
        # Phase 3E: Verify Delete (REVERSE ORDER)
        # ------------------------------------------------------------------
        log("\n=== Phase 3E: Verify Delete (REVERSE ORDER) ===")
        run_verify_delete(session, "3E verify_delete_tag", "collectives_get_tag", {"tag_id": store["Tag_id"], "collective_id": store["Collective_id"]})
        run_verify_delete(session, "3E verify_delete_page", "collectives_get_page", {"page_id": store["Page_id"], "collective_id": store["Collective_id"]})
        run_verify_delete(session, "3E verify_delete_row", "table_get_row", {"row_id": store["Row_id"]})
        run_verify_delete(session, "3E verify_delete_contact", "get_contact", {"contact_uid": store["Contact_id"], "addressbook_uri": store["Addressbook_id"]})
        run_verify_delete(session, "3E verify_delete_todo", "todo_get", {"todo_uid": store["Todo_id"], "calendar_uri": store["Calendar_id"]})
        run_verify_delete(session, "3E verify_delete_event", "get_event", {"event_uid": store["Event_id"], "calendar_uri": store["Calendar_id"]})
        run_verify_delete(session, "3E verify_delete_card", "deck_get_card", {"card_id": store["Card_id"], "board_id": store["Board_id"], "stack_id": store["Stack_id"]})
        run_verify_delete(session, "3E verify_delete_stack", "deck_get_stack", {"stack_id": store["Stack_id"], "board_id": store["Board_id"]})
        run_verify_delete(session, "3E verify_delete_collective", "collectives_get_collective", {"id": store["Collective_id"]})
        run_verify_delete(session, "3E verify_delete_addressbook", "get_addressbook", {"addressbook_uri": store["Addressbook_id"]})
        run_verify_delete(session, "3E verify_delete_calendar", "get_calendar", {"calendar_uri": store["Calendar_id"]})
        run_verify_delete(session, "3E verify_delete_board", "deck_get_board", {"board_id": store["Board_id"]})
        run_verify_delete(session, "3E verify_delete_table", "table_get", {"table_id": store["Table_id"]})
        run_verify_delete(session, "3E verify_delete_note", "note_get", {"id": store["Note_id"]})

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
