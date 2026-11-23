"""Calendar events, tasks, availability, out-of-office, and the agenda."""

from __future__ import annotations

import datetime as dt

from ..expect import (
    find_item,
    require,
    require_iso,
    require_sorted,
)
from ..harness import Scenario, scenario

DAY = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).date()
NEXT_DAY = DAY + dt.timedelta(days=1)


def _at(day: dt.date, hour: int) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00-06:00"


def _window(day: dt.date, days: int = 1) -> dict[str, str]:
    return {
        "from_date": f"{day.isoformat()}T00:00:00-06:00",
        "to_date": f"{(day + dt.timedelta(days=days)).isoformat()}T00:00:00-06:00",
    }


def _new_calendar(s: Scenario, suffix: str = "cal") -> str:
    name = f"{s.prefix}-{suffix}"
    call = s.call("create calendar", "create_calendar",
                  {"display_name": name, "color": "3366FF"})
    uri = call.data.get("calendar_uri")
    s.check("create_calendar returns a calendar URI",
            lambda: require(isinstance(uri, str) and "/" in uri, call.data))
    s.cleanup("delete_calendar", {"calendar_uri": uri}, "cleanup calendar")
    return uri


@scenario(
    "calendar",
    "calendar_event_lifecycle",
    [
        "create_calendar", "get_calendar", "update_calendar",
        "list_all_calendars", "delete_calendar", "create_event", "get_event",
        "list_events_by_calendar", "list_all_events", "update_event",
        "delete_event", "create_meeting", "find_availability",
    ],
)
def calendar_event_lifecycle(s: Scenario) -> None:
    uri = _new_calendar(s, "events")
    cal_name = uri.split("/")[-1]

    get_call = s.call("get calendar", "get_calendar",
                      {"calendar_uri": uri, "include_all_fields": True})
    s.check("get_calendar returns the calendar",
            lambda: require(
                get_call.data.get("displayname", "").startswith(s.prefix),
                get_call.data,
            ))

    listed = s.call("list calendars", "list_all_calendars", {})
    calendars = s.items("list_all_calendars returns items", listed)
    s.check("new calendar is listed",
            lambda: require(
                any(cal_name in (item.get("href") or "") for item in calendars),
                f"calendars: {[item.get('href') for item in calendars][:5]}",
            ))

    updated = s.call("update calendar", "update_calendar",
                     {"calendar_uri": uri, "display_name": f"{s.prefix}-renamed",
                      "color": "00AA00"})
    updated_calendar = updated.data.get("result") if isinstance(
        updated.data.get("result"), dict) else updated.data
    s.check("update_calendar response contains the new name",
            lambda: require(
                updated_calendar.get("displayname") == f"{s.prefix}-renamed",
                updated.data,
            ))
    get_call = s.call("get renamed calendar", "get_calendar",
                      {"calendar_uri": uri, "include_all_fields": True})
    s.check("calendar rename persisted",
            lambda: require(get_call.data.get("displayname") == f"{s.prefix}-renamed",
                            get_call.data))

    event = s.call("create event", "create_event", {
        "calendar_uri": uri,
        "summary": "Audit Event",
        "dtstart": _at(DAY, 10),
        "dtend": _at(DAY, 11),
        "description": "audit description",
        "location": "AuditLocation",
    })
    uid = event.data.get("event_uid")
    s.check("create_event returns an event UID",
            lambda: require(bool(uid), event.data))

    got = s.call("get event", "get_event", {"calendar_uri": uri, "event_uid": uid})
    s.check("get_event returns the event",
            lambda: require(got.data.get("summary") == "Audit Event"
                            and got.data.get("location") == "AuditLocation", got.data))
    s.check("event start is normalized to UTC",
            lambda: require(str(got.data.get("dtstart", "")).startswith(
                f"{DAY.isoformat()}T16:00:00"), got.data))
    s.check("event end is normalized to UTC",
            lambda: require(str(got.data.get("dtend", "")).startswith(
                f"{DAY.isoformat()}T17:00:00"), got.data))

    by_cal = s.call("list events by calendar", "list_events_by_calendar", {
        "calendar_uri": uri, **_window(DAY), "include_all_fields": True,
    })
    day_items = s.items("list_events_by_calendar returns items", by_cal)
    s.check("event is listed on its calendar for its day",
            lambda: require(
                any(item.get("uid") == uid for item in day_items),
                f"uids: {[item.get('uid') for item in day_items]}",
            ))

    all_call = s.call("list all events", "list_all_events", {
        **_window(DAY), "limit": 100, "include_all_fields": True,
    })
    all_items = s.items("list_all_events returns items", all_call)
    s.check("event is included in the all-calendar listing",
            lambda: require(
                any(item.get("uid") == uid for item in all_items),
                f"uids: {[item.get('uid') for item in all_items][:10]}",
            ))

    s.call("update event summary", "update_event", {
        "calendar_uri": uri, "event_uid": uid, "summary": "Audit Event v2",
    })
    got = s.call("get updated event", "get_event", {"calendar_uri": uri, "event_uid": uid})
    s.check("partial event update keeps dates and location",
            lambda: require(
                got.data.get("summary") == "Audit Event v2"
                and str(got.data.get("dtstart", "")).startswith(
                    f"{DAY.isoformat()}T16:00:00")
                and got.data.get("location") == "AuditLocation",
                got.data,
            ))

    recurring = s.call("create recurring event", "create_event", {
        "calendar_uri": uri,
        "summary": "Audit Recurring",
        "dtstart": _at(DAY, 9),
        "dtend": _at(DAY, 9).replace(":00:00-06:00", ":30:00-06:00"),
        "description": "daily",
        "rrule": "FREQ=DAILY;COUNT=2",
    })
    recurring_uid = recurring.data.get("event_uid")
    rec_list = s.call("list recurring instances", "list_events_by_calendar", {
        "calendar_uri": uri, **_window(DAY, 3), "include_all_fields": True,
    })
    rec_items = s.items("recurring listing returns items", rec_list)
    occurrences = [
        item for item in rec_items
        if item.get("uid") == recurring_uid or item.get("summary") == "Audit Recurring"
    ]
    s.check("recurring event expands to two occurrences",
            lambda: require(len(occurrences) == 2,
                            f"got {len(occurrences)}: {occurrences}"))

    meeting = s.call("create meeting", "create_meeting", {
        "summary": "Audit Meeting",
        "dtstart": _at(DAY, 13),
        "dtend": _at(DAY, 14),
        "attendees": "audit@example.com",
        "description": "meeting",
        "calendar_uri": uri,
    })
    meeting_uid = meeting.data.get("event_uid")
    s.check("create_meeting returns an event UID",
            lambda: require(bool(meeting_uid), meeting.data))
    if meeting_uid:
        got = s.call("get meeting event", "get_event",
                     {"calendar_uri": uri, "event_uid": meeting_uid})
        s.check("meeting event is stored",
                lambda: require(got.data.get("summary") == "Audit Meeting", got.data))

    availability = s.call("find availability", "find_availability", {
        "calendar_uris": uri, **_window(DAY),
    })
    s.check("availability covers the requested calendar",
            lambda: require(uri in availability.data, availability.data))
    s.check("availability reports the busy block",
            lambda: require(
                "FREEBUSY" in str(availability.data.get(uri, ""))
                and f"{DAY.isoformat().replace('-', '')}T160000Z" in
                    str(availability.data.get(uri, "")),
                str(availability.data.get(uri, ""))[:400],
            ))

    for event_uid in (uid, recurring_uid, meeting_uid):
        if event_uid:
            s.call("delete event", "delete_event",
                   {"calendar_uri": uri, "event_uid": event_uid})
    s.expect_error("deleted event is gone", "get_event",
                   {"calendar_uri": uri, "event_uid": uid})

    s.call("delete calendar", "delete_calendar", {"calendar_uri": uri})
    all_call = s.call("list calendars after delete", "list_all_calendars", {})
    calendars = s.items("calendar listing after delete", all_call)
    s.check("deleted calendar is no longer listed",
            lambda: require(
                not any(cal_name in (item.get("href") or "") for item in calendars),
                f"still listed: {cal_name}",
            ))
    deleted_cal = s.call("fetch deleted calendar", "get_calendar",
                         {"calendar_uri": uri}, required=False)
    s.check("deleted calendar is reported as deleted",
            lambda: require(
                deleted_cal.is_error
                or str(deleted_cal.data.get("status", "")).lower() == "deleted"
                or "deleted" in deleted_cal.error.lower(),
                deleted_cal.error or deleted_cal.data,
            ))


@scenario(
    "calendar",
    "todo_lifecycle",
    [
        "create_todo", "get_todo", "list_todos", "list_todos_by_calendar",
        "search_todos", "update_todo", "delete_todo",
    ],
)
def todo_lifecycle(s: Scenario) -> None:
    uri = _new_calendar(s, "todos")
    todo = s.call("create todo", "create_todo", {
        "summary": "Audit Todo",
        "description": "todo description",
        "due_date": _at(DAY, 15),
        "calendar_uri": uri,
        "priority": 5,
    })
    uid = todo.data.get("todo_uid")
    s.check("create_todo returns a UID", lambda: require(bool(uid), todo.data))

    got = s.call("get todo", "get_todo", {"todo_uid": uid, "calendar_uri": uri})
    s.check("todo due date is normalized to UTC",
            lambda: require(
                str(got.data.get("due", "")).startswith(f"{DAY.isoformat()}T21:00:00"),
                got.data,
            ))
    s.check("todo keeps its priority",
            lambda: require(str(got.data.get("priority")) == "5", got.data))

    by_cal = s.call("list todos by calendar", "list_todos_by_calendar",
                    {"calendar_uri": uri})
    by_cal_items = s.items("list_todos_by_calendar returns items", by_cal)
    s.check("todo is listed in its calendar",
            lambda: require(any(item.get("uid") == uid for item in by_cal_items),
                            by_cal_items[:3]))

    all_call = s.call("list all todos", "list_todos", {})
    all_items = s.items("list_todos returns items", all_call)
    s.check("todo is listed by list_todos",
            lambda: require(any(item.get("uid") == uid for item in all_items),
                            all_items[:5]))

    search_call = s.call("search todos", "search_todos",
                         {"query": "Audit Todo", "calendar_uri": uri})
    search_items = s.items("search_todos returns items", search_call)
    s.check("search_todos finds the todo",
            lambda: require(any(item.get("uid") == uid for item in search_items),
                            search_items))

    s.call("update todo", "update_todo", {
        "todo_uid": uid, "calendar_uri": uri,
        "summary": "Audit Todo v2", "due_date": _at(NEXT_DAY, 15),
    })
    got = s.call("get updated todo", "get_todo", {"todo_uid": uid, "calendar_uri": uri})
    s.check("todo update changes summary and due date",
            lambda: require(
                got.data.get("summary") == "Audit Todo v2"
                and str(got.data.get("due", "")).startswith(f"{NEXT_DAY.isoformat()}T21:00:00"),
                got.data,
            ))

    s.call("clear todo due date", "update_todo", {
        "todo_uid": uid, "calendar_uri": uri, "due_date": "",
    })
    got = s.call("get cleared todo", "get_todo", {"todo_uid": uid, "calendar_uri": uri})
    s.check("empty due date clears the todo due date",
            lambda: require(not got.data.get("due"), got.data))

    s.call("delete todo", "delete_todo", {"todo_uid": uid, "calendar_uri": uri})
    s.expect_error("deleted todo is gone", "get_todo",
                   {"todo_uid": uid, "calendar_uri": uri})


@scenario(
    "calendar",
    "out_of_office",
    ["get_ooo", "set_ooo", "clear_ooo"],
)
def out_of_office(s: Scenario) -> None:
    current = s.call("get current ooo", "get_ooo", {})
    s.check("get_ooo works without an explicit user id",
            lambda: require(isinstance(current.data, dict), current.data))
    existing = bool(current.data.get("firstDay") or current.data.get("message")
                    or current.data.get("status") not in (None, "", "not_set"))
    if existing:
        s.skip("out-of-office mutation",
               f"OOO already configured; refusing to overwrite it: {current.data}")
        return

    first = (DAY + dt.timedelta(days=5)).isoformat()
    last = (DAY + dt.timedelta(days=7)).isoformat()
    s.call("set ooo", "set_ooo", {
        "first_day": f"{first}T09:00:00-06:00",
        "last_day": f"{last}T17:00:00-06:00",
        "status": "Away",
        "message": "Audit OOO message",
    })
    s.cleanup("clear_ooo", {}, "cleanup out-of-office")
    got = s.call("get configured ooo", "get_ooo", {})
    s.check("configured OOO returns the status and message",
            lambda: require(
                got.data.get("status") == "Away"
                and got.data.get("message") == "Audit OOO message",
                got.data,
            ))
    s.check("configured OOO keeps the start date",
            lambda: require(
                str(got.data.get("firstDay", "")).startswith(first),
                got.data,
            ))

    s.call("clear ooo", "clear_ooo", {})
    got = s.call("get cleared ooo", "get_ooo", {})
    s.check("cleared OOO reports not_set",
            lambda: require(
                got.data.get("status") == "not_set"
                or not got.data.get("firstDay"),
                got.data,
            ))


@scenario(
    "calendar",
    "upcoming_agenda",
    [
        "list_upcoming_events",
        "create_event", "delete_event",
        "create_todo", "delete_todo",
        "create_deck_board", "delete_deck_board",
        "create_deck_stack", "create_deck_card",
    ],
)
def upcoming_agenda(s: Scenario) -> None:
    uri = _new_calendar(s, "agenda")
    event = s.call("create agenda event", "create_event", {
        "calendar_uri": uri,
        "summary": "Audit Agenda Event",
        "dtstart": _at(DAY, 10),
        "dtend": _at(DAY, 11),
        "description": "agenda",
        "location": "AuditLocation",
    })
    event_uid = event.data.get("event_uid")

    todo = s.call("create agenda todo", "create_todo", {
        "summary": "Audit Agenda Todo",
        "description": "agenda",
        "due_date": _at(DAY, 14),
        "calendar_uri": uri,
    })
    todo_uid = todo.data.get("todo_uid")

    board = s.call("create agenda board", "create_deck_board",
                   {"title": f"{s.prefix}-agenda-board"})
    board_id = board.data.get("id")
    s.cleanup("delete_deck_board", {"board_id": board_id}, "cleanup agenda board")
    stack = s.call("create agenda stack", "create_deck_stack",
                   {"board_id": board_id, "title": "agenda-stack", "order": 1})
    stack_id = stack.data.get("id")
    card = s.call("create agenda card", "create_deck_card", {
        "board_id": board_id,
        "stack_id": stack_id,
        "title": "Audit Agenda Card",
        "description": "agenda",
        "duedate": _at(DAY, 16),
    })
    card_id = card.data.get("id")

    agenda = s.call("list upcoming agenda", "list_upcoming_events", _window(DAY))
    items = s.items("upcoming returns items", agenda)
    s.check("upcoming counts all three sources",
            lambda: require(
                agenda.data.get("count", 0) >= 3
                and agenda.data.get("calendar_events", 0) >= 1
                and agenda.data.get("tasks", 0) >= 1
                and agenda.data.get("deck_tasks", 0) >= 1,
                agenda.data,
            ))
    s.check("upcoming includes the event, task, and Deck card",
            lambda: require(
                {"Audit Agenda Event", "Audit Agenda Todo", "Audit Agenda Card"}
                <= {item.get("title") for item in items},
                f"titles: {[item.get('title') for item in items]}",
            ))
    s.check("upcoming items carry the expected types",
            lambda: require(
                {"calendar_event", "task", "deck_task"}
                <= {item.get("type") for item in items},
                f"types: {[item.get('type') for item in items]}",
            ))
    s.check("upcoming items are sorted by time",
            lambda: require_sorted(items, "at"))
    s.check("upcoming includes UTC timestamps",
            lambda: require(
                all(require_iso(item.get("at"), "at") for item in items),
                f"ats: {[item.get('at') for item in items]}",
            ))
    fixture_times = {
        item.get("title"): item.get("at")
        for item in items
        if item.get("title") in {"Audit Agenda Event", "Audit Agenda Todo",
                                 "Audit Agenda Card"}
    }
    s.check("agenda fixture times are 16:00, 20:00, 22:00 UTC",
            lambda: require(
                fixture_times == {
                    "Audit Agenda Event": f"{DAY.isoformat()}T16:00:00+00:00",
                    "Audit Agenda Todo": f"{DAY.isoformat()}T20:00:00+00:00",
                    "Audit Agenda Card": f"{DAY.isoformat()}T22:00:00+00:00",
                },
                fixture_times,
            ))

    limited = s.call("limited upcoming", "list_upcoming_events",
                     {"limit": 2, **_window(DAY)})
    limited_items = s.items("limited upcoming returns items", limited)
    s.check("limit truncates the agenda",
            lambda: require(len(limited_items) == 2
                            and limited.data.get("total", 0) >= 3
                            and limited.data.get("truncated") is True,
                            limited.data))

    filtered = s.call("location-filtered upcoming", "list_upcoming_events",
                      {"location": "AuditLocation", **_window(DAY)})
    filtered_items = s.items("filtered upcoming returns items", filtered)
    s.check("location filter keeps only the matching event",
            lambda: require(
                len(filtered_items) == 1
                and filtered_items[0].get("type") == "calendar_event",
                filtered_items,
            ))

    empty = s.call("empty upcoming", "list_upcoming_events", {
        "from_date": "2031-06-15T00:00:00+00:00",
        "to_date": "2031-06-16T00:00:00+00:00",
    })
    empty_items = s.items("empty upcoming returns items", empty)
    s.check("empty upcoming returns [] and a message",
            lambda: require(
                empty_items == [] and empty.data.get("count") == 0
                and bool(empty.data.get("message")),
                empty.data,
            ))

    default = s.call("default upcoming", "list_upcoming_events", {"limit": 100})
    default_items = s.items("default upcoming returns items", default)
    s.check("default 30-day window includes the fixtures",
            lambda: require(
                {"Audit Agenda Event", "Audit Agenda Todo", "Audit Agenda Card"}
                <= {item.get("title") for item in default_items},
                f"titles: {[item.get('title') for item in default_items][:20]}",
            ))

    s.call("delete agenda card", "delete_deck_card",
           {"board_id": board_id, "stack_id": stack_id, "card_id": card_id})
    s.call("delete agenda todo", "delete_todo",
           {"todo_uid": todo_uid, "calendar_uri": uri})
    s.call("delete agenda event", "delete_event",
           {"calendar_uri": uri, "event_uid": event_uid})
