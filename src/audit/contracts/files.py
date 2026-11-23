"""Files, folders, favorites, direct downloads, reminders, collections."""

from __future__ import annotations

from urllib.parse import urlparse

from toon_mcp import toon_to_json

from ..backend import Backend
from ..expect import (
    find_item,
    require,
    require_iso,
    require_not_contains,
    scalar_text,
)
from ..harness import Scenario, scenario


def _tree_nodes(call) -> list[dict]:
    raw = call.data.get("tree")
    require(isinstance(raw, str) and raw.startswith("["),
            f"folder tree is not a TOON list: {raw!r}")
    nodes = toon_to_json(raw)
    require(isinstance(nodes, list), f"folder tree decoded to {type(nodes).__name__}")
    return nodes


def _walk(nodes: list[dict], basename: str) -> dict | None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("basename") == basename:
            return node
        found = _walk(node.get("children") or [], basename)
        if found:
            return found
    return None


def _file_id(items: list[dict], displayname: str) -> int:
    item = find_item(items, displayname=displayname)
    require(item.get("fileid"), f"{displayname} has no fileid: {item}")
    return int(item["fileid"])


@scenario(
    "files",
    "files_folders_favorites_reminders",
    [
        "create_folder", "write_file", "read_file", "list_folder_contents",
        "list_all_folders", "search_files", "move_file", "copy_file",
        "delete_file", "move_folder", "copy_folder", "delete_folder",
        "move_files", "list_favorites", "get_direct_download_url",
        "set_file_reminder", "get_file_reminder", "delete_file_reminder",
    ],
)
def files_folders_favorites_reminders(s: Scenario) -> None:
    backend = Backend()
    folder = f"{s.prefix}-files"
    sub = f"{folder}/sub"
    hello = f"{folder}/hello.txt"
    moved = f"{folder}/moved.txt"
    copied = f"{folder}/copied.txt"
    subcopy = f"{folder}/subcopy"
    subrenamed = f"{folder}/subrenamed"
    content_v1 = "Hello ünïcode\nline two\n"
    content_v2 = "Hello ünïcode v2\nline three\n"

    s.call("create folder", "create_folder", {"path": folder})
    s.cleanup("delete_folder", {"path": folder}, "cleanup files folder")
    s.call("create subfolder", "create_folder", {"path": sub})

    s.call("write file", "write_file", {"path": hello, "content": content_v1})
    read_call = s.call("read file", "read_file", {"path": hello})
    s.check("read_file returns the exact content",
            lambda: require(read_call.data.get("content") == content_v1,
                            f"got {read_call.data.get('content')!r}"))
    s.call("overwrite file", "write_file", {"path": hello, "content": content_v2})
    read_call = s.call("read overwritten file", "read_file", {"path": hello})
    s.check("overwrite replaces the content",
            lambda: require(read_call.data.get("content") == content_v2,
                            f"got {read_call.data.get('content')!r}"))

    list_call = s.call("list folder contents", "list_folder_contents",
                       {"path": folder, "include_all_fields": True})
    items = s.items("list_folder_contents returns items", list_call)
    s.check("folder listing contains the file and subfolder",
            lambda: require(
                any(item.get("displayname") == "hello.txt" for item in items)
                and any(item.get("displayname") == "sub" for item in items),
                f"names: {[item.get('displayname') for item in items]}",
            ))
    s.check("folder listing does not include the folder itself",
            lambda: require(
                not any(
                    (item.get("href", "") or "").rstrip("/")
                    == (list_call.data.get("message") or "").rstrip("/")
                    for item in items
                )
                and not any(item.get("displayname") == folder.split("/")[-1]
                            and "collection" in (item.get("resourcetype") or [])
                            for item in items),
                f"names: {[item.get('displayname') for item in items]}",
            ))
    fid = _file_id(items, "hello.txt")
    s.check("file id is exposed as an integer-compatible value",
            lambda: require(str(fid).isdigit(), fid))

    tree_call = s.call("list all folders", "list_all_folders", {})
    tree = s.check("list_all_folders returns a decodable tree",
                   lambda: _tree_nodes(tree_call)) or []
    s.check("folder tree contains the audit folder",
            lambda: require(_walk(tree, folder.split("/")[-1]) is not None,
                            f"did not find {folder!r}"))

    search_call = s.call("search files", "search_files",
                         {"query": "hello.txt", "limit": 10,
                          "include_all_fields": True})
    found = s.items("search_files returns items", search_call)
    s.check("search_files finds the audit file",
            lambda: require(
                any(
                    scalar_text(item.get("title")) == "hello.txt"
                    or str((item.get("attributes") or {}).get("fileId")) == str(fid)
                    for item in found
                ),
                f"results: {found[:3]}",
            ))

    # Favorites are a WebDAV property; no MCP tool can set one, so the audit
    # fixture uses the backend directly and still reads through the MCP tool.
    backend.set_favorite(hello, True)
    fav_call = s.call("list favorites", "list_favorites", {"limit": 10})
    favorites = s.items("list_favorites returns items", fav_call)
    s.check("list_favorites returns the starred fixture",
            lambda: require(
                any(item.get("displayname") == "hello.txt" for item in favorites),
                f"favorites: {[item.get('displayname') for item in favorites]}",
            ))
    backend.set_favorite(hello, False)
    fav_call = s.call("list favorites after unstar", "list_favorites", {"limit": 10})
    favorites = s.items("unstarred list returns items", fav_call)
    s.check("list_favorites no longer returns the unstarred file",
            lambda: require(
                not any(item.get("displayname") == "hello.txt" for item in favorites),
                f"favorites: {[item.get('displayname') for item in favorites]}",
            ))

    direct_call = s.call("direct download URL", "get_direct_download_url",
                         {"file_id": fid})
    url = direct_call.data.get("url")
    s.check("direct download returns a URL",
            lambda: require(isinstance(url, str) and url.startswith("http"), url))

    def _fetch_direct():
        # The tool intentionally returns the public URL. Inside the audit
        # container the public hostname may not resolve, so fetch the same
        # path through the internal backend URL.
        parsed = urlparse(url)
        response = backend.fetch(parsed.path)
        require(response.status_code == 200,
                f"direct download returned HTTP {response.status_code}")
        require(response.text == content_v2,
                f"direct download content differs: {response.text[:80]!r}")

    s.check("direct download URL serves the file content", _fetch_direct)

    due_local = "2026-12-15T15:00:00-06:00"
    s.call("set reminder", "set_file_reminder",
           {"file_id": fid, "due_date": due_local})
    s.cleanup("delete_file_reminder", {"file_id": fid}, "cleanup file reminder")
    reminder_call = s.call("get reminder", "get_file_reminder", {"file_id": fid})
    s.check("reminder due date is normalized to UTC",
            lambda: require_iso(reminder_call.data.get("dueDate"), "dueDate")
            if reminder_call.data.get("dueDate") else
            require(False, f"no dueDate: {reminder_call.data}"))
    s.check("reminder due date keeps the correct instant",
            lambda: require(
                str(reminder_call.data.get("dueDate", "")).startswith("2026-12-15T21:00:00"),
                reminder_call.data,
            ))
    s.call("delete reminder", "delete_file_reminder", {"file_id": fid})
    reminder_call = s.call("get deleted reminder", "get_file_reminder", {"file_id": fid})
    s.check("deleted reminder is reported as absent",
            lambda: require(
                not reminder_call.data.get("dueDate"),
                f"dueDate still present: {reminder_call.data}",
            ))

    s.call("move file", "move_file", {"source": hello, "destination": moved})
    s.check("moved file is readable",
            lambda: require(
                s.run.client.call("read_file", {"path": moved}).data.get("content") == content_v2,
                "moved file content mismatch",
            ))
    s.expect_error("old file path is gone", "read_file", {"path": hello})

    s.call("copy file", "copy_file", {"source": moved, "destination": copied})
    s.check("copied file is readable",
            lambda: require(
                s.run.client.call("read_file", {"path": copied}).data.get("content") == content_v2,
                "copied file content mismatch",
            ))

    s.call("move multiple files", "move_files",
           {"files": [copied], "destination": sub})
    sub_list = s.call("list subfolder", "list_folder_contents",
                      {"path": sub, "include_all_fields": True})
    sub_items = s.items("subfolder returns items", sub_list)
    s.check("move_files moved the file into the subfolder",
            lambda: require(
                any(item.get("displayname") == "copied.txt" for item in sub_items),
                f"subfolder: {[item.get('displayname') for item in sub_items]}",
            ))

    s.call("copy folder", "copy_folder", {"source": sub, "destination": subcopy})
    copy_list = s.call("list copied folder", "list_folder_contents",
                       {"path": subcopy, "include_all_fields": True})
    copy_items = s.items("copied folder returns items", copy_list)
    s.check("copy_folder copied its contents",
            lambda: require(
                any(item.get("displayname") == "copied.txt" for item in copy_items),
                f"copied folder: {[item.get('displayname') for item in copy_items]}",
            ))

    s.call("move folder", "move_folder",
           {"source": subcopy, "destination": subrenamed})
    s.check("moved folder still contains its file",
            lambda: require(
                any(
                    item.get("displayname") == "copied.txt"
                    for item in s.run.client.call(
                        "list_folder_contents",
                        {"path": subrenamed, "include_all_fields": True},
                    ).items()
                ),
                "moved folder lost its contents",
            ))

    folder_delete = s.call("delete_file rejects a folder", "delete_file",
                           {"path": sub}, required=False)
    s.check("delete_file reports a folder as invalid",
            lambda: require(
                folder_delete.is_error
                or bool(folder_delete.data.get("error"))
                or str(folder_delete.data.get("status", "")).lower()
                in {"error", "failed", "not_found"},
                folder_delete.error or folder_delete.data,
            ))

    file_delete = s.call("delete_folder rejects a file", "delete_folder",
                         {"path": moved}, required=False)
    s.check("delete_folder reports a file as invalid",
            lambda: require(
                file_delete.is_error
                or bool(file_delete.data.get("error"))
                or str(file_delete.data.get("status", "")).lower()
                in {"error", "failed", "not_found"},
                file_delete.error or file_delete.data,
            ))

    s.call("delete file", "delete_file", {"path": moved})
    s.expect_error("deleted file is no longer readable", "read_file",
                   {"path": moved})
    s.call("delete subfolder", "delete_folder", {"path": sub})
    s.call("delete renamed folder", "delete_folder", {"path": subrenamed})


@scenario(
    "files",
    "collections_readonly",
    [
        "search_collections", "search_collections_raw",
        "get_collection_by_id", "list_collections_by_resource",
        "create_collection",
    ],
)
def collections_readonly(s: Scenario) -> None:
    backend = Backend()
    folder = f"{s.prefix}-collections"
    file_path = f"{folder}/collection.txt"

    s.call("create folder", "create_folder", {"path": folder})
    s.cleanup("delete_folder", {"path": folder}, "cleanup collections folder")
    s.call("write file", "write_file", {"path": file_path, "content": "collection"})
    listing = s.call("list folder", "list_folder_contents",
                     {"path": folder, "include_all_fields": True})
    fid = _file_id(s.items("folder items", listing), "collection.txt")

    search_call = s.call("search collections", "search_collections",
                         {"search": "test-collection"})
    collections = s.items("search_collections returns items", search_call)
    s.check("collection search results are objects",
            lambda: require(
                all(isinstance(item, dict) for item in collections),
                f"got {collections[:3]}",
            ))
    s.check("collection objects have id, name, resources",
            lambda: require(
                all(
                    item.get("id") is not None and item.get("name") is not None
                    and isinstance(item.get("resources"), list)
                    for item in collections
                ),
                f"got {collections[:3]}",
            ))

    raw_call = s.call("search collections raw", "search_collections_raw",
                      {"search": "test-collection"})
    raw = raw_call.data.get("items")
    s.check("search_collections_raw returns a JSON list",
            lambda: require(isinstance(raw, list),
                            f"got {type(raw).__name__}: {raw!r}"))

    empty_call = s.call("search collections without a term", "search_collections", {})
    empty_items = s.items("empty collection search returns items", empty_call)
    s.check("collection search without a term returns an empty list",
            lambda: require(empty_items == [], f"got {empty_items}"))

    if collections:
        first = collections[0]
        get_call = s.call("get collection by id", "get_collection_by_id",
                          {"id": first["id"]})
        s.check("get_collection_by_id returns the requested collection",
                lambda: require(
                    str(get_call.data.get("id")) == str(first["id"])
                    and get_call.data.get("name") == first.get("name"),
                    get_call.data,
                ))

    by_resource = s.call("collections by resource", "list_collections_by_resource",
                         {"resource_type": "file", "resource_id": fid})
    by_resource_items = s.items("collections by resource returns items", by_resource)
    s.check("a file with no collections returns an empty list",
            lambda: require(by_resource_items == [], f"got {by_resource_items}"))

    s.skip(
        "create_collection",
        "skipped: Nextcloud exposes no delete endpoint for collections and the "
        "MCP has no delete_collection tool, so creating one would leave residue",
    )
