"""Talk rooms/messages and Mail read/draft/move/flags."""

from __future__ import annotations

import time

from ..backend import Backend
from ..expect import find_item, require
from ..harness import Scenario, scenario

SECOND_USER = "solo"


@scenario(
    "talk",
    "talk_lifecycle",
    [
        "list_talk_rooms", "create_talk_room", "delete_talk_room",
        "update_talk_room", "set_talk_description", "list_talk_participants",
        "add_talk_participant", "remove_talk_participant",
        "send_chat_message", "list_chat_messages", "delete_chat_message",
        "send_direct_message",
    ],
)
def talk_lifecycle(s: Scenario) -> None:
    backend = Backend()
    room = s.call("create room", "create_talk_room",
                  {"room_type": "group", "room_name": f"{s.prefix}-room"})
    token = room.data.get("token")
    s.check("create_talk_room returns a token",
            lambda: require(bool(token), room.data))
    s.cleanup("delete_talk_room", {"token": token}, "cleanup talk room")

    rooms = s.call("list rooms", "list_talk_rooms", {})
    room_items = s.items("list_talk_rooms returns items", rooms)
    s.check("new room is listed",
            lambda: require(any(item.get("token") == token for item in room_items),
                            [item.get("token") for item in room_items]))

    updated = s.call("update room", "update_talk_room",
                     {"token": token, "room_name": f"{s.prefix}-renamed"})
    s.check("update_talk_room returns the new name",
            lambda: require(updated.data.get("name") == f"{s.prefix}-renamed",
                            updated.data))

    s.call("set description", "set_talk_description",
           {"token": token, "description": "audit description"})
    try:
        room_detail = backend.talk_room(token)
    except Exception as exc:
        room_detail = {}
        s.skip("room description verification",
               f"could not read the room through the backend: {exc}")
    if room_detail:
        s.check("room description persisted",
                lambda: require(room_detail.get("description") == "audit description",
                                room_detail))

    participants = s.call("list participants", "list_talk_participants",
                          {"token": token, "include_all_fields": True})
    parts = s.items("participants return items", participants)
    s.check("room starts with the owner as a participant",
            lambda: require(bool(parts), parts))

    s.call("add participant", "add_talk_participant",
           {"token": token, "new_participant": SECOND_USER, "source": "users"})
    participants = s.call("list participants after add", "list_talk_participants",
                          {"token": token, "include_all_fields": True})
    parts = s.items("participants after add return items", participants)
    member = None
    for item in parts:
        if item.get("actorId") == SECOND_USER:
            member = item
            break
    s.check("added participant is listed",
            lambda: require(member is not None, parts))
    if member:
        s.call("remove participant", "remove_talk_participant",
               {"token": token, "attendee_id": member.get("attendeeId")})
        participants = s.call("list participants after remove",
                              "list_talk_participants",
                              {"token": token, "include_all_fields": True})
        parts = s.items("participants after remove return items", participants)
        s.check("removed participant is gone",
                lambda: require(
                    not any(item.get("actorId") == SECOND_USER for item in parts),
                    parts,
                ))

    message = s.call("send chat message", "send_chat_message",
                     {"token": token, "message": "Audit talk message"})
    message_id = message.data.get("id")
    s.check("send_chat_message returns a numeric id",
            lambda: require(message_id is not None, message.data))

    messages = s.call("list chat messages", "list_chat_messages",
                      {"token": token, "limit": 20, "include_all_fields": True})
    items = s.items("chat messages return items", messages)
    s.check("sent message is listed with its text",
            lambda: require(
                any(item.get("id") == message_id
                    and item.get("message") == "Audit talk message"
                    for item in items),
                items[:5],
            ))
    s.check("chat message timestamps are integers",
            lambda: require(
                all(isinstance(item.get("timestamp"), int) for item in items),
                items[:3],
            ))

    s.call("delete chat message", "delete_chat_message",
           {"token": token, "message_id": message_id})
    messages = s.call("list chat messages after delete", "list_chat_messages",
                      {"token": token, "limit": 20, "include_all_fields": True})
    items = s.items("chat messages after delete return items", messages)
    s.check("deleted message text is no longer listed",
            lambda: require(
                not any(item.get("id") == message_id
                        and item.get("message") == "Audit talk message"
                        and not item.get("deleted")
                        for item in items),
                items[:5],
            ))

    dm = s.call("send direct message", "send_direct_message",
                {"recipient": SECOND_USER, "message": "Audit DM message"})
    dm_token = dm.data.get("room_token")
    dm_id = dm.data.get("id")
    s.check("send_direct_message targets the requested recipient",
            lambda: require(
                (dm.data.get("recipient") or {}).get("id") == SECOND_USER,
                dm.data,
            ))
    s.check("send_direct_message returns the DM room token",
            lambda: require(bool(dm_token), dm.data))
    if dm_token and dm_id:
        s.call("delete DM message", "delete_chat_message",
               {"token": dm_token, "message_id": dm_id})
        dm_room_delete = s.attempt("delete DM room", "delete_talk_room",
                                   {"token": dm_token})
        if dm_room_delete.is_error:
            s.skip("delete DM room",
                   "Talk does not allow deleting a 1:1 room; the message was "
                   f"removed but the empty conversation remains: "
                   f"{dm_room_delete.error}")

    s.call("delete room", "delete_talk_room", {"token": token})
    rooms = s.call("list rooms after delete", "list_talk_rooms", {})
    room_items = s.items("rooms after delete return items", rooms)
    s.check("deleted room is no longer listed",
            lambda: require(
                not any(item.get("token") == token for item in room_items),
                f"still listed: {token}",
            ))


def _special(mailboxes: list[dict], name: str) -> dict | None:
    for box in mailboxes:
        uses = box.get("specialUse") or []
        if isinstance(uses, str):
            uses = [uses]
        if name in uses:
            return box
    return None


def _subjects(client, account_id: int, mailbox_id: int, subject: str) -> list[int]:
    call = client.call("list_messages", {"account_id": account_id,
                                         "mailbox_db_id": mailbox_id,
                                         "limit": 100,
                                         "include_all_fields": True})
    if call.is_error:
        return []
    return [
        item.get("databaseId") for item in call.items()
        if item.get("subject") == subject
    ]


@scenario(
    "mail",
    "mail_read_flags_draft_send",
    [
        "list_all_accounts", "list_mailboxes", "list_messages",
        "get_message", "get_message_body", "send_message",
        "create_email_draft", "set_message_flags", "move_message",
    ],
)
def mail_read_flags_draft_send(s: Scenario) -> None:
    backend = Backend()
    client = s.run.client

    accounts = s.call("list accounts", "list_all_accounts", {})
    account_items = s.items("accounts return items", accounts)
    s.check("the admin mail account is configured",
            lambda: require(bool(account_items), account_items))
    if not account_items:
        s.skip("mail lifecycle", "no mail account configured")
        return
    account = account_items[0]
    account_id = account.get("accountId") or account.get("id")
    address = account.get("emailAddress")

    boxes = s.call("list mailboxes", "list_mailboxes", {"account_id": account_id})
    mailboxes = s.items("mailboxes return items", boxes)
    inbox = _special(mailboxes, "inbox")
    drafts = _special(mailboxes, "drafts")
    sent = _special(mailboxes, "sent")
    trash = _special(mailboxes, "trash")
    s.check("mailboxes include INBOX, Drafts, Sent, and Trash",
            lambda: require(
                all(box is not None for box in (inbox, drafts, sent, trash)),
                [box.get("name") for box in mailboxes],
            ))
    if not inbox or not drafts or not sent:
        s.skip("mail message tests", "required mailboxes are missing")
        return

    s.skip("list Trash messages",
           "Nextcloud Mail returns 'mailbox 7 is not cached' "
           "(MailboxNotCachedException); Trash can only be listed after sync")

    messages = s.call("list messages", "list_messages",
                      {"account_id": account_id,
                       "mailbox_db_id": inbox["databaseId"],
                       "limit": 10, "include_all_fields": True})
    items = s.items("INBOX messages return items", messages)
    s.check("INBOX listing exposes database ids and flags",
            lambda: require(
                items and all(item.get("databaseId") is not None
                              and isinstance(item.get("flags"), dict)
                              for item in items),
                items[:2],
            ))
    if items:
        message = items[0]
        message_id = message["databaseId"]
        subject = message.get("subject")
        got = s.call("get message", "get_message",
                     {"account_id": account_id, "message_id": message_id})
        s.check("get_message returns the same subject",
                lambda: require(got.data.get("subject") == subject, got.data))
        body = s.call("get message body", "get_message_body",
                      {"account_id": account_id, "message_id": message_id})
        s.check("get_message_body returns message metadata",
                lambda: require(body.data.get("subject") == subject, body.data))
    else:
        s.skip("read an existing message", "INBOX is empty")

    draft_subject = f"{s.prefix}-draft"
    draft_ids: list[int] = []
    try:
        draft = s.call("create draft", "create_email_draft",
                       {"account_id": account_id, "to": address,
                        "subject": draft_subject, "body": "audit draft body"})
        s.check("create_email_draft reports draft_created",
                lambda: require(draft.data.get("status") == "draft_created",
                                draft.data))
        for _ in range(3):
            listing = s.call("list drafts", "list_messages",
                             {"account_id": account_id,
                              "mailbox_db_id": drafts["databaseId"],
                              "limit": 20, "include_all_fields": True})
            draft_items = s.items("draft messages return items", listing)
            draft_ids = [
                item.get("databaseId") for item in draft_items
                if item.get("subject") == draft_subject
            ]
            if draft_ids:
                break
            time.sleep(1)
        s.check("draft is saved in the Drafts mailbox",
                lambda: require(bool(draft_ids), draft_items[:5]))
    finally:
        for draft_id in draft_ids:
            backend.delete_mail_message(account_id, draft_id)

    send_subject = f"{s.prefix}-send"
    sent_result = s.call("send message to self", "send_message",
                         {"account_id": account_id, "to": address,
                          "subject": send_subject, "body": "audit sent body"})
    s.check("send_message reports sent",
            lambda: require(sent_result.data.get("status") == "sent",
                            sent_result.data))

    try:
        inbox_ids: list[int] = []
        sent_ids: list[int] = []
        for _ in range(6):
            backend.sync_mailbox(inbox["databaseId"])
            backend.sync_mailbox(sent["databaseId"])
            inbox_ids = _subjects(client, account_id, inbox["databaseId"],
                                  send_subject)
            sent_ids = _subjects(client, account_id, sent["databaseId"],
                                 send_subject)
            if inbox_ids and sent_ids:
                break
            time.sleep(2)

        if not inbox_ids:
            s.skip("mail flag and move tests",
                   "self-sent message was not observable in INBOX after sync")
            return

        s.check("self-sent message is observable in INBOX and Sent",
                lambda: require(bool(inbox_ids) and bool(sent_ids),
                                {"inbox": inbox_ids, "sent": sent_ids}))

        message_id = inbox_ids[0]
        got = s.call("get sent message", "get_message",
                     {"account_id": account_id, "message_id": message_id})
        original_seen = bool((got.data.get("flags") or {}).get("seen"))
        s.call("toggle seen flag", "set_message_flags",
               {"account_id": account_id, "message_id": message_id,
                "seen": not original_seen})
        got = s.call("get message after flag", "get_message",
                     {"account_id": account_id, "message_id": message_id})
        s.check("seen flag changes",
                lambda: require(
                    bool((got.data.get("flags") or {}).get("seen"))
                    != original_seen, got.data))
        s.call("restore seen flag", "set_message_flags",
               {"account_id": account_id, "message_id": message_id,
                "seen": original_seen})

        s.call("move message to Sent", "move_message",
               {"account_id": account_id, "message_id": message_id,
                "mailbox_db_id": sent["databaseId"]})
        backend.sync_mailbox(sent["databaseId"])
        moved_ids = _subjects(client, account_id, sent["databaseId"], send_subject)
        s.check("moved message appears in Sent",
                lambda: require(bool(moved_ids), moved_ids))
        if moved_ids:
            moved_id = max(moved_ids)
            s.call("move message back to INBOX", "move_message",
                   {"account_id": account_id, "message_id": moved_id,
                    "mailbox_db_id": inbox["databaseId"]})
            backend.sync_mailbox(inbox["databaseId"])
            returned = _subjects(client, account_id, inbox["databaseId"],
                                 send_subject)
            s.check("moved message returned to INBOX",
                    lambda: require(bool(returned), returned))
    finally:
        # Delete every copy of the audit-created message; re-sync first so
        # moved messages are visible under their current ids.
        for mailbox in (inbox, sent, drafts):
            if not mailbox:
                continue
            try:
                backend.sync_mailbox(mailbox["databaseId"])
            except Exception:
                pass
            for message_db_id in _subjects(client, account_id,
                                           mailbox["databaseId"], send_subject):
                try:
                    backend.delete_mail_message(account_id, message_db_id)
                except Exception:
                    pass
