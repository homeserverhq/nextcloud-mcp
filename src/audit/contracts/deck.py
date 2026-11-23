"""Deck boards, stacks, cards, labels, and assignees."""

from __future__ import annotations

import base64
import datetime as dt
import os

import httpx

from ..backend import Backend
from ..expect import find_item, require
from ..harness import Scenario, scenario

DAY = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)).date()
DUE_LOCAL = f"{DAY.isoformat()}T16:30:00-06:00"
DUE_UTC = f"{DAY.isoformat()}T22:30:00"
try:
    SECOND_USER = base64.b64decode(os.getenv("API_KEY", "")).decode().split(":", 1)[0]
except Exception:
    SECOND_USER = os.getenv("NEXTCLOUD_USER", "")


def _assignee_uid(user) -> str:
    if not isinstance(user, dict):
        return str(user or "")
    participant = user.get("participant")
    if isinstance(participant, dict):
        return str(participant.get("uid") or participant.get("primaryKey")
                   or participant.get("id") or "")
    return str(user.get("uid") or user.get("primaryKey") or user.get("id") or "")


@scenario(
    "deck",
    "deck_lifecycle",
    [
        "list_deck_boards", "get_deck_board", "create_deck_board",
        "update_deck_board", "delete_deck_board",
        "list_deck_stacks", "create_deck_stack", "get_deck_stack",
        "update_deck_stack", "delete_deck_stack",
        "list_deck_cards", "get_deck_card", "create_deck_card",
        "update_deck_card", "delete_deck_card",
        "archive_deck_card", "unarchive_deck_card",
        "list_deck_labels", "assign_deck_label", "remove_deck_label",
        "assign_deck_user", "remove_deck_user",
    ],
)
def deck_lifecycle(s: Scenario) -> None:
    backend = Backend()

    board = s.call("create board", "create_deck_board",
                   {"title": f"{s.prefix}-board", "color": "3366FF"})
    board_id = board.data.get("id")
    s.check("create_deck_board returns an id", lambda: require(board_id is not None,
                                                              board.data))
    s.cleanup("delete_deck_board", {"board_id": board_id}, "cleanup deck board")

    got_board = s.call("get board", "get_deck_board", {"board_id": board_id})
    s.check("get_deck_board returns the new board",
            lambda: require(
                got_board.data.get("id") == board_id
                and got_board.data.get("title") == f"{s.prefix}-board",
                got_board.data,
            ))

    boards = s.call("list boards", "list_deck_boards", {})
    board_items = s.items("list_deck_boards returns items", boards)
    s.check("new board is listed",
            lambda: require(any(item.get("id") == board_id for item in board_items),
                            [item.get("id") for item in board_items][:10]))

    updated = s.call("update board", "update_deck_board",
                     {"board_id": board_id, "title": f"{s.prefix}-board-v2",
                      "color": "00AA00"})
    s.check("update_deck_board returns the updated title",
            lambda: require(updated.data.get("title") == f"{s.prefix}-board-v2",
                            updated.data))
    got_board = s.call("get updated board", "get_deck_board", {"board_id": board_id})
    s.check("board rename persisted",
            lambda: require(got_board.data.get("title") == f"{s.prefix}-board-v2",
                            got_board.data))

    stack = s.call("create stack", "create_deck_stack",
                   {"board_id": board_id, "title": "Audit Stack", "order": 1})
    stack_id = stack.data.get("id")
    s.check("create_deck_stack returns an id",
            lambda: require(stack_id is not None, stack.data))

    got_stack = s.call("get stack", "get_deck_stack",
                       {"board_id": board_id, "stack_id": stack_id})
    s.check("get_deck_stack returns the new stack",
            lambda: require(got_stack.data.get("title") == "Audit Stack",
                            got_stack.data))

    stacks = s.call("list stacks", "list_deck_stacks", {"board_id": board_id})
    stack_items = s.items("list_deck_stacks returns items", stacks)
    s.check("new stack is listed",
            lambda: require(any(item.get("id") == stack_id for item in stack_items),
                            [item.get("id") for item in stack_items]))

    updated_stack = s.call("update stack", "update_deck_stack",
                           {"board_id": board_id, "stack_id": stack_id,
                            "title": "Audit Stack v2", "order": 2})
    s.check("stack update returns the new title",
            lambda: require(updated_stack.data.get("title") == "Audit Stack v2",
                            updated_stack.data))
    got_stack = s.call("get updated stack", "get_deck_stack",
                       {"board_id": board_id, "stack_id": stack_id})
    s.check("stack update persisted",
            lambda: require(got_stack.data.get("title") == "Audit Stack v2"
                            and got_stack.data.get("order") == 2, got_stack.data))

    card = s.call("create card", "create_deck_card", {
        "board_id": board_id,
        "stack_id": stack_id,
        "title": "Audit Card",
        "description": "card description",
        "duedate": DUE_LOCAL,
    })
    card_id = card.data.get("id")
    s.check("create_deck_card returns an id",
            lambda: require(card_id is not None, card.data))
    s.check("card due date is normalized to UTC",
            lambda: require(str(card.data.get("duedate", "")).startswith(DUE_UTC),
                            card.data))

    got_card = s.call("get card", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id})
    s.check("get_deck_card returns the new card",
            lambda: require(
                got_card.data.get("title") == "Audit Card"
                and got_card.data.get("description") == "card description"
                and str(got_card.data.get("duedate", "")).startswith(DUE_UTC),
                got_card.data,
            ))

    cards = s.call("list cards", "list_deck_cards",
                   {"board_id": board_id, "stack_id": stack_id})
    card_items = s.items("list_deck_cards returns items", cards)
    s.check("new card is listed",
            lambda: require(any(item.get("id") == card_id for item in card_items),
                            [item.get("id") for item in card_items]))

    s.call("update card title only", "update_deck_card", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
        "title": "Audit Card v2",
    })
    got_card = s.call("get card after title update", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id})
    s.check("card update preserves description and due date",
            lambda: require(
                got_card.data.get("title") == "Audit Card v2"
                and got_card.data.get("description") == "card description"
                and str(got_card.data.get("duedate", "")).startswith(DUE_UTC),
                got_card.data,
            ))

    s.call("update card description only", "update_deck_card", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
        "description": "updated description",
    })
    got_card = s.call("get card after description update", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id})
    s.check("card description update preserves title and due date",
            lambda: require(
                got_card.data.get("title") == "Audit Card v2"
                and got_card.data.get("description") == "updated description"
                and str(got_card.data.get("duedate", "")).startswith(DUE_UTC),
                got_card.data,
            ))

    label_id = None
    try:
        response = httpx.post(
            f"{backend.base_url}/index.php/apps/deck/api/v1.0/boards/{board_id}/labels",
            auth=backend.auth,
            json={"title": "Audit Label", "color": "FF00FF"},
            timeout=30.0,
        )
        response.raise_for_status()
        label_id = response.json().get("id")
    except Exception as exc:
        s.skip("create deck label fixture", f"could not create label: {exc}")

    labels = s.call("list labels", "list_deck_labels",
                    {"board_id": board_id, "include_all_fields": True})
    label_items = s.items("list_deck_labels returns items", labels)
    if label_id is not None:
        s.check("new label is listed",
                lambda: require(any(item.get("id") == label_id for item in label_items),
                                label_items))
        s.call("assign label", "assign_deck_label", {
            "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
            "label_id": label_id,
        })
        got_card = s.call("get card with label", "get_deck_card",
                          {"board_id": board_id, "stack_id": stack_id,
                           "card_id": card_id, "include_all_fields": True})
        s.check("card shows the assigned label",
                lambda: require(
                    any(str(label.get("id")) == str(label_id)
                        for label in (got_card.data.get("labels") or [])),
                    got_card.data.get("labels"),
                ))
        s.call("remove label", "remove_deck_label", {
            "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
            "label_id": label_id,
        })
        got_card = s.call("get card without label", "get_deck_card",
                          {"board_id": board_id, "stack_id": stack_id,
                           "card_id": card_id, "include_all_fields": True})
        s.check("card no longer shows the removed label",
                lambda: require(
                    not any(str(label.get("id")) == str(label_id)
                            for label in (got_card.data.get("labels") or [])),
                    got_card.data.get("labels"),
                ))
    else:
        s.skip("deck label checks", "no label fixture available")

    s.call("assign deck user", "assign_deck_user", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
        "user_id": SECOND_USER,
    })
    got_card = s.call("get card with assignee", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id, "include_all_fields": True})
    s.check("card shows the assigned user",
            lambda: require(
                any(_assignee_uid(user) == SECOND_USER
                    for user in (got_card.data.get("assignedUsers") or [])),
                got_card.data.get("assignedUsers"),
            ))
    s.call("remove deck user", "remove_deck_user", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
        "user_id": SECOND_USER,
    })
    got_card = s.call("get card without assignee", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id, "include_all_fields": True})
    s.check("card no longer shows the removed user",
            lambda: require(
                not any(_assignee_uid(user) == SECOND_USER
                        for user in (got_card.data.get("assignedUsers") or [])),
                got_card.data.get("assignedUsers"),
            ))

    s.call("archive card", "archive_deck_card", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
    })
    got_card = s.call("get archived card", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id})
    s.check("archived card reports archived=true",
            lambda: require(got_card.data.get("archived") is True, got_card.data))
    s.call("unarchive card", "unarchive_deck_card", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
    })
    got_card = s.call("get unarchived card", "get_deck_card",
                      {"board_id": board_id, "stack_id": stack_id,
                       "card_id": card_id})
    s.check("unarchived card reports archived=false",
            lambda: require(got_card.data.get("archived") is False, got_card.data))

    s.call("delete card", "delete_deck_card", {
        "board_id": board_id, "stack_id": stack_id, "card_id": card_id,
    })
    s.expect_error("deleted card is gone", "get_deck_card",
                   {"board_id": board_id, "stack_id": stack_id,
                    "card_id": card_id},
                   contains=("403", "404", "not found", "does not exist",
                             "deleted"))

    s.call("delete stack", "delete_deck_stack",
           {"board_id": board_id, "stack_id": stack_id})
    s.expect_error("deleted stack is gone", "get_deck_stack",
                   {"board_id": board_id, "stack_id": stack_id},
                   contains=("403", "404", "not found", "does not exist",
                             "deleted"))

    s.call("delete board", "delete_deck_board", {"board_id": board_id})
    s.expect_error("deleted board is gone", "get_deck_board",
                   {"board_id": board_id},
                   contains=("403", "404", "not found", "does not exist",
                             "deleted"))
    boards = s.call("list boards after delete", "list_deck_boards", {})
    board_items = s.items("boards after delete return items", boards)
    s.check("deleted board is no longer listed",
            lambda: require(
                not any(item.get("id") == board_id for item in board_items),
                f"still listed: {board_id}",
            ))
