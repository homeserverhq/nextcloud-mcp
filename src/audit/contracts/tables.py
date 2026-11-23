"""Tables, columns, and rows."""

from __future__ import annotations

from toon_mcp import toon_to_json

from ..expect import find_item, require
from ..harness import Scenario, scenario


def _rows(call) -> list[dict]:
    raw = call.data.get("rows", call.data.get("items"))
    if isinstance(raw, str):
        raw = toon_to_json(raw)
    require(isinstance(raw, list), f"rows is not a list: {raw!r}")
    return raw


def _value(row: dict, column_id: int):
    for entry in row.get("data") or []:
        if isinstance(entry, dict) and str(entry.get("columnId")) == str(column_id):
            return entry.get("value")
    return None


@scenario(
    "tables",
    "table_schema_row_lifecycle",
    [
        "list_tables", "get_table", "create_table", "update_table",
        "delete_table", "get_table_schema", "create_table_column",
        "list_table_rows", "create_table_row", "get_table_row",
        "update_table_row", "delete_table_row",
    ],
)
def table_schema_row_lifecycle(s: Scenario) -> None:
    table = s.call("create table", "create_table", {
        "title": f"{s.prefix}-table",
        "description": "audit table",
        "emoji": "🧪",
    })
    table_id = table.data.get("id")
    s.check("create_table returns an id",
            lambda: require(table_id is not None, table.data))
    s.cleanup("delete_table", {"table_id": table_id}, "cleanup table")

    got = s.call("get table", "get_table", {"table_id": table_id})
    s.check("get_table returns the new table",
            lambda: require(
                got.data.get("id") == table_id
                and got.data.get("title") == f"{s.prefix}-table"
                and got.data.get("emoji") == "🧪",
                got.data,
            ))
    s.check("get_table exposes the description",
            lambda: require(got.data.get("description") == "audit table",
                            got.data))

    tables = s.call("list tables", "list_tables", {})
    table_items = s.items("list_tables returns items", tables)
    s.check("new table is listed",
            lambda: require(any(item.get("id") == table_id for item in table_items),
                            [item.get("id") for item in table_items][:10]))

    updated = s.call("update table", "update_table", {
        "table_id": table_id,
        "title": f"{s.prefix}-table-v2",
        "emoji": "🔧",
    })
    s.check("update_table returns the new title and emoji",
            lambda: require(
                updated.data.get("title") == f"{s.prefix}-table-v2"
                and updated.data.get("emoji") == "🔧",
                updated.data,
            ))
    got = s.call("get updated table", "get_table", {"table_id": table_id})
    s.check("table update persisted",
            lambda: require(
                got.data.get("title") == f"{s.prefix}-table-v2"
                and got.data.get("emoji") == "🔧",
                got.data,
            ))

    column = s.call("create column", "create_table_column", {
        "table_id": table_id, "title": "Name", "subtype": "line",
    })
    column_id = column.data.get("id")
    technical_name = column.data.get("technicalName")
    s.check("create_table_column returns id and technical name",
            lambda: require(column_id is not None and bool(technical_name),
                            column.data))
    s.check("create_table_column uses the requested title",
            lambda: require(column.data.get("title") == "Name", column.data))

    schema = s.call("get schema", "get_table_schema",
                    {"table_id": table_id, "include_all_fields": True})
    s.check("schema lists the new column",
            lambda: require(
                any(str(entry.get("id")) == str(column_id)
                    for entry in (schema.data.get("columns") or [])),
                schema.data.get("columns"),
            ))
    s.check("schema exposes the column technical name",
            lambda: require(
                find_item(schema.data.get("columns") or [], id=column_id)
                .get("technicalName") == technical_name,
                schema.data.get("columns"),
            ))

    first_row = s.call("create row by title", "create_table_row",
                       {"table_id": table_id, "data": {"Name": "Alice"}})
    first_id = first_row.data.get("id")
    s.check("create_table_row resolves a column title to its id",
            lambda: require(
                _value(first_row.data, column_id) == "Alice",
                first_row.data,
            ))

    second_row = s.call("create row by technical name", "create_table_row",
                        {"table_id": table_id,
                         "data": {technical_name: "Bob"}})
    second_id = second_row.data.get("id")
    s.check("create_table_row resolves a technical name",
            lambda: require(
                _value(second_row.data, column_id) == "Bob",
                second_row.data,
            ))

    got_row = s.call("get row", "get_table_row",
                     {"row_id": first_id, "include_all_fields": True})
    s.check("get_table_row returns the stored value",
            lambda: require(
                got_row.data.get("id") == first_id
                and _value(got_row.data, column_id) == "Alice",
                got_row.data,
            ))

    rows_call = s.call("list rows", "list_table_rows",
                       {"table_id": table_id, "include_all_fields": True})
    rows = s.check("list_table_rows returns rows", lambda: _rows(rows_call)) or []
    s.check("list_table_rows includes both rows",
            lambda: require(
                {row.get("id") for row in rows} >= {first_id, second_id},
                [row.get("id") for row in rows],
            ))

    limited = s.call("limited rows", "list_table_rows",
                     {"table_id": table_id, "limit": 1})
    limited_rows = s.check("limited rows decode", lambda: _rows(limited)) or []
    s.check("list_table_rows respects limit",
            lambda: require(len(limited_rows) == 1,
                            f"got {len(limited_rows)} rows"))

    updated_row = s.call("update row", "update_table_row",
                         {"row_id": first_id, "data": {"Name": "Alice2"}})
    s.check("update_table_row returns the new value",
            lambda: require(_value(updated_row.data, column_id) == "Alice2",
                            updated_row.data))
    got_row = s.call("get updated row", "get_table_row",
                     {"row_id": first_id, "include_all_fields": True})
    s.check("row update persisted",
            lambda: require(_value(got_row.data, column_id) == "Alice2",
                            got_row.data))

    s.call("delete row", "delete_table_row", {"row_id": first_id})
    s.expect_error("deleted row is gone", "get_table_row", {"row_id": first_id})

    s.call("delete table", "delete_table", {"table_id": table_id})
    s.expect_error("deleted table is gone", "get_table", {"table_id": table_id})
    tables = s.call("list tables after delete", "list_tables", {})
    table_items = s.items("table listing after delete", tables)
    s.check("deleted table is no longer listed",
            lambda: require(
                not any(item.get("id") == table_id for item in table_items),
                f"still listed: {table_id}",
            ))
