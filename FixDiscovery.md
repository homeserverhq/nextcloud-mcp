# FixDiscovery — Test Runner Hardcoded IDs & List/Use Verification

## Problem

### 1. 404 on `list_contacts` with URI from `list_addressbooks`

`list_addressbooks()` returns raw DAV hrefs like:
```
/remote.php/dav/addressbooks/users/shbadmin_nextcloud/test_addressbook/
```

But `list_contacts()` expects the short URI form:
```
users/shbadmin_nextcloud/test_addressbook
```

The test currently uses the URI returned by `create_addressbook` (pre-formatted), **masking** this mismatch. A real AI agent calling `list_addressbooks()` then passing the href directly as `addressbook_uri` would hit a 404 (doubled path).

### 2. Hardcoded IDs

| Location | Value | Should come from |
|----------|-------|-----------------|
| `get_group_members` | `group_id="admin"` | `get_groups()` |
| `search` | `provider_id="files"` | `get_search_providers()` |
| `get_activity_filter` | `filter_name="all"` | `get_activity_filters()` |
| `set_predefined_message` | `message_id="meeting"` | `get_predefined_statuses()` |
| Notifications fallback | `id=999999` | `list_notifications()` |

## Plan

### Step 1: Verify create ID/URI matches list output

After each Phase 3A create, call the corresponding `list_*` tool and confirm the created resource's ID/URI exists in the returned items by matching on name/title/display_name. Store the **list-derived** ID for subsequent operations instead of the create-response ID.

This immediately surfaces the `list_addressbooks` href vs URI mismatch.

### Step 2: Refactor URI extraction for contacts/calendars

For resources where the ID/URI format differs between create and list (Addressbook, Calendar):
- After create + list, extract URI from the **list response** by parsing the DAV href
- Use that list-derived URI for all subsequent get/update/delete operations on contacts/events
- Replicates the exact user/AI agent flow

### Step 3: Replace hardcoded values with discovery

- `"admin"` → first group from `get_groups()`
- `"files"` → first provider from `get_search_providers()`
- `"all"` → first filter from `get_activity_filters()`
- `"meeting"` → first message from `get_predefined_statuses()`
- `999999` → only call get/delete_notification with IDs from `list_notifications()` (skip if empty)

### Step 4: Add delete verification via list

When verifying deletion (`run_verify_delete`), also confirm the resource is absent from the `list_*` output, not just that GET returns an error.

## File: `src/test_runner.py`

All changes go here. No changes to `main.py` or `client.py`.
