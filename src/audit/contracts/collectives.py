"""Collectives, pages, and tags."""

from __future__ import annotations

from ..expect import find_item, require
from ..harness import Scenario, scenario


def _inner(data, key: str) -> dict:
    return data.get(key, data) if isinstance(data, dict) else {}


@scenario(
    "collectives",
    "collective_page_tag_lifecycle",
    [
        "list_collectives", "create_collective", "get_collective",
        "update_collective", "update_collective_emoji", "trash_collective",
        "restore_collective", "delete_collective",
        "list_trashed_collectives", "list_collective_pages",
        "get_collective_page", "create_collective_page",
        "trash_collective_page", "restore_collective_page",
        "delete_collective_page", "move_collective_page",
        "set_collective_page_emoji", "set_collective_page_full_width",
        "update_collective_page", "search_collective_pages",
        "list_collective_tags", "get_collective_tag",
        "create_collective_tag", "update_collective_tag",
        "delete_collective_tag", "assign_collective_tag",
        "remove_collective_tag",
    ],
)
def collective_page_tag_lifecycle(s: Scenario) -> None:
    collective = s.call("create collective", "create_collective",
                        {"name": f"{s.prefix}-collective", "emoji": "📁"})
    collective_id = _inner(collective.data, "collective").get("id")
    s.check("create_collective returns an id",
            lambda: require(collective_id is not None, collective.data))

    page_id = None
    tag_id = None
    try:
        got = s.call("get collective", "get_collective",
                     {"id": collective_id})
        s.check("get_collective returns the new collective",
                lambda: require(
                    got.data.get("id") == collective_id
                    and got.data.get("name") == f"{s.prefix}-collective",
                    got.data,
                ))

        collectives = s.call("list collectives", "list_collectives", {})
        collective_items = s.items("list_collectives returns items", collectives)
        s.check("new collective is listed",
                lambda: require(
                    any(item.get("id") == collective_id for item in collective_items),
                    [item.get("id") for item in collective_items][:10],
                ))

        updated = s.call("update collective", "update_collective",
                         {"id": collective_id, "emoji": "🌈"})
        s.check("update_collective returns the new emoji",
                lambda: require(
                    _inner(updated.data, "collective").get("emoji") == "🌈",
                    updated.data,
                ))
        got = s.call("get emoji collective", "get_collective",
                     {"id": collective_id})
        s.check("collective emoji persisted",
                lambda: require(got.data.get("emoji") == "🌈", got.data))

        emoji = s.call("update collective emoji", "update_collective_emoji",
                       {"id": collective_id, "emoji": "⭐"})
        s.check("update_collective_emoji succeeds",
                lambda: require(not emoji.is_error, emoji.error))
        got = s.call("get emoji collective", "get_collective",
                     {"id": collective_id})
        s.check("collective emoji persisted",
                lambda: require(got.data.get("emoji") == "⭐", got.data))

        page = s.call("create page", "create_collective_page",
                      {"collective_id": collective_id, "title": "Audit Page",
                       "parent_id": 0})
        page_id = _inner(page.data, "page").get("id")
        s.check("create_collective_page returns a page id",
                lambda: require(page_id is not None, page.data))

        got_page = s.call("get page", "get_collective_page",
                          {"collective_id": collective_id, "page_id": page_id,
                           "include_all_fields": True})
        s.check("get_collective_page returns the new title",
                lambda: require(got_page.data.get("title") == "Audit Page",
                                got_page.data))

        pages = s.call("list pages", "list_collective_pages",
                       {"collective_id": collective_id})
        page_items = s.items("list_collective_pages returns items", pages)
        s.check("new page is listed",
                lambda: require(any(item.get("id") == page_id for item in page_items),
                                [item.get("id") for item in page_items]))

        s.call("update page", "update_collective_page",
               {"collective_id": collective_id, "page_id": page_id,
                "title": "Audit Page v2"})
        got_page = s.call("get updated page", "get_collective_page",
                          {"collective_id": collective_id, "page_id": page_id,
                           "include_all_fields": True})
        s.check("page title update persisted",
                lambda: require(got_page.data.get("title") == "Audit Page v2",
                                got_page.data))

        search = s.call("search pages", "search_collective_pages",
                        {"collective_id": collective_id, "query": "Audit Page"})
        search_items = s.items("search_collective_pages returns items", search)
        s.check("page title search finds the page",
                lambda: require(any(item.get("id") == page_id for item in search_items),
                                search_items))

        s.call("set page emoji", "set_collective_page_emoji",
               {"collective_id": collective_id, "page_id": page_id,
                "emoji": "🔧"})
        s.call("set page full width", "set_collective_page_full_width",
               {"collective_id": collective_id, "page_id": page_id,
                "is_full_width": True})
        got_page = s.call("get styled page", "get_collective_page",
                          {"collective_id": collective_id, "page_id": page_id,
                           "include_all_fields": True})
        s.check("page emoji and full-width persisted",
                lambda: require(
                    got_page.data.get("emoji") == "🔧"
                    and bool(got_page.data.get("isFullWidth")),
                    got_page.data,
                ))

        moved = s.call("move page", "move_collective_page",
                       {"collective_id": collective_id, "page_id": page_id,
                        "destination_collective_id": collective_id,
                        "destination_parent_id": 0})
        s.check("move_collective_page returns the page",
                lambda: require(_inner(moved.data, "page").get("id") == page_id,
                                moved.data))

        s.call("trash page", "trash_collective_page",
               {"collective_id": collective_id, "page_id": page_id})
        pages = s.call("list pages after trash", "list_collective_pages",
                       {"collective_id": collective_id})
        page_items = s.items("pages after trash return items", pages)
        s.check("trashed page is no longer listed",
                lambda: require(
                    not any(item.get("id") == page_id for item in page_items),
                    page_items,
                ))
        s.call("restore page", "restore_collective_page",
               {"collective_id": collective_id, "page_id": page_id})
        pages = s.call("list pages after restore", "list_collective_pages",
                       {"collective_id": collective_id})
        page_items = s.items("pages after restore return items", pages)
        s.check("restored page is listed again",
                lambda: require(any(item.get("id") == page_id for item in page_items),
                                page_items))

        tag = s.call("create tag", "create_collective_tag",
                     {"collective_id": collective_id, "name": "AuditTag",
                      "color": "FF0000"})
        tag_id = _inner(tag.data, "tag").get("id")
        s.check("create_collective_tag returns a tag id",
                lambda: require(tag_id is not None, tag.data))
        got_tag = s.call("get tag", "get_collective_tag",
                         {"collective_id": collective_id, "tag_id": tag_id})
        s.check("get_collective_tag returns the tag",
                lambda: require(
                    got_tag.data.get("id") == tag_id
                    and got_tag.data.get("name") == "AuditTag",
                    got_tag.data,
                ))
        tags = s.call("list tags", "list_collective_tags",
                      {"collective_id": collective_id})
        tag_items = s.items("list_collective_tags returns items", tags)
        s.check("new tag is listed",
                lambda: require(any(item.get("id") == tag_id for item in tag_items),
                                tag_items))

        updated_tag = s.call("update tag", "update_collective_tag",
                             {"collective_id": collective_id, "tag_id": tag_id,
                              "name": "AuditTag v2", "color": "00FF00"})
        s.check("update_collective_tag returns the new name",
                lambda: require(
                    _inner(updated_tag.data, "tag").get("name") == "AuditTag v2",
                    updated_tag.data,
                ))

        s.call("assign tag", "assign_collective_tag",
               {"collective_id": collective_id, "page_id": page_id,
                "tag_id": tag_id})
        got_page = s.call("get tagged page", "get_collective_page",
                          {"collective_id": collective_id, "page_id": page_id,
                           "include_all_fields": True})
        page_tags = got_page.data.get("tags") or []
        if page_tags:
            s.check("page lists the assigned tag",
                    lambda: require(
                        any(str(tag) == str(tag_id)
                            for tag in (
                                page_tags if not isinstance(page_tags[0], dict)
                                else [entry.get("id") for entry in page_tags]
                            )),
                        page_tags,
                    ))
        else:
            s.skip("page tag verification",
                   "get_collective_page does not expose assigned tags")

        s.call("remove tag", "remove_collective_tag",
               {"collective_id": collective_id, "page_id": page_id,
                "tag_id": tag_id})
        s.call("delete tag", "delete_collective_tag",
               {"collective_id": collective_id, "tag_id": tag_id})
        tags = s.call("list tags after delete", "list_collective_tags",
                      {"collective_id": collective_id})
        tag_items = s.items("tags after delete return items", tags)
        s.check("deleted tag is no longer listed",
                lambda: require(
                    not any(item.get("id") == tag_id for item in tag_items),
                    tag_items,
                ))

        disposable = s.call("create disposable page", "create_collective_page",
                            {"collective_id": collective_id,
                             "title": "Disposable Page", "parent_id": 0})
        disposable_id = _inner(disposable.data, "page").get("id")
        s.call("delete disposable page", "delete_collective_page",
               {"collective_id": collective_id, "page_id": disposable_id})
        pages = s.call("list pages after page delete", "list_collective_pages",
                       {"collective_id": collective_id})
        page_items = s.items("pages after page delete return items", pages)
        s.check("deleted page is no longer listed",
                lambda: require(
                    not any(item.get("id") == disposable_id for item in page_items),
                    page_items,
                ))

        s.call("trash collective", "trash_collective", {"id": collective_id})
        trashed = s.call("list trashed collectives", "list_trashed_collectives", {})
        trashed_items = s.items("list_trashed_collectives returns items", trashed)
        s.check("trashed collective is listed",
                lambda: require(
                    any(item.get("id") == collective_id for item in trashed_items),
                    trashed_items,
                ))
        s.call("restore collective", "restore_collective", {"id": collective_id})
        got = s.call("get restored collective", "get_collective",
                     {"id": collective_id})
        s.check("restored collective is readable",
                lambda: require(got.data.get("id") == collective_id, got.data))
    finally:
        if collective_id is not None:
            s.attempt("cleanup trash collective", "trash_collective",
                      {"id": collective_id})
            s.attempt("cleanup delete collective", "delete_collective",
                      {"id": collective_id})

    collectives = s.call("list collectives after delete", "list_collectives", {})
    collective_items = s.items("collectives after delete return items", collectives)
    s.check("deleted collective is no longer listed",
            lambda: require(
                not any(item.get("id") == collective_id
                        for item in collective_items),
                f"still listed: {collective_id}",
            ))
    deleted = s.call("fetch deleted collective", "get_collective",
                     {"id": collective_id}, required=False)
    s.check("deleted collective cannot be fetched",
            lambda: require(
                deleted.is_error
                or "not found" in (deleted.error or "").lower()
                or "not found" in str(deleted.data).lower(),
                deleted.error or deleted.data,
            ))
