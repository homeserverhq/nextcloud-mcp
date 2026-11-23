"""Notes and contacts contracts."""

from __future__ import annotations

from ..expect import find_item, require
from ..harness import Scenario, scenario


@scenario(
    "notes_contacts",
    "note_lifecycle",
    [
        "create_note", "get_note", "update_note", "delete_note",
        "append_note_content", "search_notes", "list_notes",
        "list_notes_by_category", "get_note_attachment",
    ],
)
def note_lifecycle(s: Scenario) -> None:
    category = f"AuditCat-{s.run.run_id}"
    title = f"{s.prefix}-note"
    attachment = f"{s.prefix}-attachment.txt"
    attachment_path = f"Notes/{category}/{attachment}"

    note = s.call("create note", "create_note", {
        "title": title,
        "content": "line one",
        "category": category,
    })
    note_id = note.data.get("id")
    s.check("create_note returns an id",
            lambda: require(isinstance(note_id, int), note.data))
    s.cleanup("delete_note", {"id": note_id}, "cleanup note")
    s.cleanup("delete_folder", {"path": f"Notes/{category}"},
              "cleanup note category folder")

    got = s.call("get note", "get_note", {"id": note_id})
    s.check("get_note returns the created content",
            lambda: require(
                got.data.get("title") == title
                and got.data.get("content") == "line one"
                and got.data.get("category") == category,
                got.data,
            ))

    appended = s.call("append note content", "append_note_content",
                      {"id": note_id, "content": "line two"})
    s.check("append_note_content appends without dropping existing text",
            lambda: require(
                "line one" in str(appended.data.get("content"))
                and "line two" in str(appended.data.get("content")),
                appended.data,
            ))

    listed = s.call("list notes", "list_notes", {})
    listed_items = s.items("list_notes returns items", listed)
    s.check("list_notes includes the new note",
            lambda: require(any(item.get("id") == note_id for item in listed_items),
                            listed_items[:3]))

    by_category = s.call("list notes by category", "list_notes_by_category",
                         {"category": category})
    category_items = s.items("list_notes_by_category returns items", by_category)
    s.check("list_notes_by_category includes the new note",
            lambda: require(any(item.get("id") == note_id for item in category_items),
                            category_items))

    search = s.call("search notes", "search_notes", {"query": title, "limit": 10})
    search_items = s.items("search_notes returns items", search)
    s.check("search_notes finds the note by title",
            lambda: require(any(item.get("id") == note_id for item in search_items),
                            search_items[:3]))

    updated = s.call("update note", "update_note", {
        "id": note_id,
        "title": f"{title}-v2",
        "content": "updated content",
        "favorite": True,
    })
    s.check("update_note returns the updated fields",
            lambda: require(
                updated.data.get("title") == f"{title}-v2"
                and updated.data.get("content") == "updated content"
                and updated.data.get("favorite") is True,
                updated.data,
            ))
    got = s.call("get updated note", "get_note", {"id": note_id})
    s.check("note update persisted",
            lambda: require(
                got.data.get("title") == f"{title}-v2"
                and got.data.get("content") == "updated content"
                and got.data.get("favorite") is True,
                got.data,
            ))

    s.call("write note attachment", "write_file",
           {"path": attachment_path, "content": "attachment body"})
    s.cleanup("delete_file", {"path": attachment_path},
              "cleanup note attachment")
    attachment_call = s.call("get note attachment", "get_note_attachment",
                             {"note_id": note_id, "path": attachment})
    s.check("get_note_attachment returns the file content",
            lambda: require(attachment_call.data.get("text") == "attachment body",
                            attachment_call.data))

    s.call("delete note", "delete_note", {"id": note_id})
    s.expect_error("deleted note is gone", "get_note", {"id": note_id})


@scenario(
    "notes_contacts",
    "contact_lifecycle",
    [
        "create_addressbook", "get_addressbook", "update_addressbook",
        "delete_addressbook", "list_all_addressbooks",
        "create_contact", "get_contact", "update_contact", "delete_contact",
        "list_all_contacts", "list_contacts_by_addressbook",
    ],
)
def contact_lifecycle(s: Scenario) -> None:
    book_name = f"{s.prefix}-book"
    book = s.call("create addressbook", "create_addressbook",
                  {"display_name": book_name, "description": "audit"})
    book_uri = book.data.get("addressbook_uri")
    s.check("create_addressbook returns a URI",
            lambda: require(isinstance(book_uri, str) and "/" in book_uri, book.data))
    s.cleanup("delete_addressbook", {"addressbook_uri": book_uri},
              "cleanup addressbook")

    got_book = s.call("get addressbook", "get_addressbook",
                      {"addressbook_uri": book_uri, "include_all_fields": True})
    s.check("get_addressbook returns the new addressbook",
            lambda: require(got_book.data.get("displayname") == book_name, got_book.data))

    books = s.call("list addressbooks", "list_all_addressbooks", {})
    book_items = s.items("list_all_addressbooks returns items", books)
    s.check("new addressbook is listed",
            lambda: require(
                any(book_uri in (item.get("href") or "") for item in book_items),
                f"books: {[item.get('href') for item in book_items][:5]}",
            ))

    updated_book = s.call("update addressbook", "update_addressbook",
                          {"addressbook_uri": book_uri,
                           "display_name": f"{book_name}-v2"})
    s.check("update_addressbook reports success",
            lambda: require(not updated_book.is_error, updated_book.error))
    got_book = s.call("get updated addressbook", "get_addressbook",
                      {"addressbook_uri": book_uri, "include_all_fields": True})
    s.check("addressbook rename persisted",
            lambda: require(got_book.data.get("displayname") == f"{book_name}-v2",
                            got_book.data))

    full_name = f"Audit Contact {s.run.run_id}"
    contact = s.call("create contact", "create_contact", {
        "addressbook_uri": book_uri,
        "fn": full_name,
        "email": "audit@example.com",
        "telephone": "+15550001111",
        "organization": "AuditOrg",
        "adr": "1 Audit Way",
    })
    contact_uid = contact.data.get("contact_uid")
    s.check("create_contact returns a usable contact UID",
            lambda: require(bool(contact_uid), contact.data))
    s.cleanup("delete_contact", {"addressbook_uri": book_uri,
                                 "contact_uid": contact_uid},
              "cleanup contact")

    got = s.call("get contact", "get_contact",
                 {"addressbook_uri": book_uri, "contact_uid": contact_uid},
                 )
    s.check("get_contact returns the same UID and fields",
            lambda: require(
                got.data.get("contact_uid") == contact_uid
                and got.data.get("fn") == full_name
                and got.data.get("email") == "audit@example.com"
                and got.data.get("organization") == "AuditOrg",
                got.data,
            ))

    by_book = s.call("list contacts by addressbook", "list_contacts_by_addressbook",
                     {"addressbook_uri": book_uri, "include_all_fields": True})
    book_contacts = s.items("list_contacts_by_addressbook returns items", by_book)
    s.check("addressbook listing returns a UID that matches the contact",
            lambda: require(
                any(item.get("contact_uid") == contact_uid for item in book_contacts),
                f"uids: {[item.get('contact_uid') for item in book_contacts]}",
            ))
    s.check("addressbook listing exposes the contact name",
            lambda: require(
                find_item(book_contacts, contact_uid=contact_uid).get("fn") == full_name,
                book_contacts,
            ))

    all_contacts = s.call("list all contacts", "list_all_contacts",
                          {"limit": 200, "include_all_fields": True})
    all_items = s.items("list_all_contacts returns items", all_contacts)
    s.check("list_all_contacts includes the new contact",
            lambda: require(
                any(item.get("contact_uid") == contact_uid for item in all_items),
                f"uids: {[item.get('contact_uid') for item in all_items][:10]}",
            ))

    updated = s.call("update contact", "update_contact", {
        "addressbook_uri": book_uri,
        "contact_uid": contact_uid,
        "fn": f"{full_name} v2",
        "organization": "AuditOrg2",
        "telephone": "+15550002222",
    })
    s.check("update_contact succeeds", lambda: require(not updated.is_error,
                                                       updated.error))
    got = s.call("get updated contact", "get_contact",
                 {"addressbook_uri": book_uri, "contact_uid": contact_uid})
    s.check("contact update preserves unspecified fields",
            lambda: require(
                got.data.get("fn") == f"{full_name} v2"
                and got.data.get("email") == "audit@example.com"
                and got.data.get("organization") == "AuditOrg2"
                and got.data.get("telephone") == "+15550002222",
                got.data,
            ))

    s.call("delete contact", "delete_contact",
           {"addressbook_uri": book_uri, "contact_uid": contact_uid})
    s.expect_error("deleted contact is gone", "get_contact",
                   {"addressbook_uri": book_uri, "contact_uid": contact_uid})

    s.call("delete addressbook", "delete_addressbook",
           {"addressbook_uri": book_uri})
    books = s.call("list addressbooks after delete", "list_all_addressbooks", {})
    book_items = s.items("addressbook listing after delete", books)
    s.check("deleted addressbook is no longer listed",
            lambda: require(
                not any(book_uri in (item.get("href") or "") for item in book_items),
                f"still listed: {book_uri}",
            ))
