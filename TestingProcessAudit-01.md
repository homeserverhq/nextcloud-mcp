# Testing Process Audit — Nextcloud MCP Server

**Date**: 2026-07-04
**Scope**: Cross-reference of all `@mcp.tool()` endpoints in `src/main.py` against test coverage in `src/test_runner.py`
**Method**: Manual enumeration of 179 tools and full trace of every tool call in `src/test_runner.py`

---

## 1. Tool Coverage Summary

| Status | Count |
|--------|-------|
| **Total tools defined** | 179 |
| **Tools with at least one test call** | 172 |
| **Tools with ZERO test coverage** | 7 |

---

## 2. Untested Tools (Zero Coverage)

### 2A. Commented-Out Code — Phase 5Q (3 tools)

The entire Phase 5Q notification test block (`src/test_runner.py`, lines 1993-2015) is **commented out**:

```python
# # ---- Phase 5Q: Notification Tests ----
# log("\n=== Phase 5Q: Notification Tests ===")
# result = session.call_tool("list_notifications")
# ...
```

This silently removes three tools from the test suite:

| Tool | Defined at |
|------|-----------|
| `notification_get` | `main.py:941` |
| `notification_delete` | `main.py:955` |
| `notification_delete_all` | `main.py:967` |

There is no alternative test path for any of these three tools. They are completely uncovered.

### 2B. Tools Never Referenced (4 tools)

| Tool | Defined at | Notes |
|------|-----------|-------|
| `circle_get` | `main.py:3041` | No test entry anywhere |
| `circle_update` | `main.py:3065` | No test entry anywhere |
| `collectives_restore` | `main.py:2681` | No test entry anywhere |
| `circle_list` | `main.py:3028` | Called only as a side-effect inside Phase 3A Collective-creation code to discover the auto-created Circle ID. No dedicated `run_test` call, no `DOMAIN_TESTS_*` entry. |

---

## 3. Completely Empty Test Phase

`DOMAIN_TESTS_BACKEND_BROKEN` (line 926) is assigned an empty list `[]`. Despite Phase 5P being called `"Backend-Broken Error Tests"`, no actual tests are run:

```python
DOMAIN_TESTS_BACKEN_BROKEN = []
```

This phase exists in the report output structure but does nothing.

---

## 4. Dishonest / Lax Pass Criteria

### 4A. `verify_tag_ops` — Always PASSES

At `src/test_runner.py:1983-1987`:

```python
results.append({
    "label": "5O verify_tag_ops", "tool": "collectives_get_page",
    "status": "PASSED", "data": data
})
```

This test **always records PASSED** regardless of what data is returned. It does not check whether tag assignment or removal actually worked. It issues the `collectives_get_page` call, then unconditionally marks the result as passed.

### 4B. `run_verify_delete` — Loose String Matching

At `src/test_runner.py:262`:

```python
if "not found" in err.lower() or "404" in err or "403" in err:
```

A completely unrelated error containing the substring `"not found"` (e.g., `"User 'not found' in directory"`) would falsely register as a confirmed deletion. Error matching is far too permissive.

### 4C. `is_error` — Silent Pass on Malformed Responses

At `src/test_runner.py:113-130`, the `is_error` function returns `None` for many edge cases, meaning tools can return error-like data in non-standard formats and silently pass as successful.

---

## 5. State-Dependency / Test Cascading

Every test after Phase 3A depends on the global `store` and `created` dictionaries. If a Phase 3A `create` fails, subsequent Phase 3B/3C/3D/3E tests for that resource automatically report `"No ID for {label} — prerequisite create failed"`. A single backend failure cascades into 4-5 false failures.

The `RESOURCE_TESTS` list (line 491) has explicit `prereqs` fields. Dependent resources like `Card` require `["Board", "Stack"]` — if either parent fails, the child tests are never run.

This is not a self-healing test suite but a fragile sequential script.

---

## 6. Pervasive Conditional Branching

Tests do **not** always run. Every phase uses conditional guards. Specific categories:

### 6A. Prerequisite Guards (every resource CRUD test)

```python
if prereqs:
    missing = check_prereqs(prereqs)
    if missing:
        # report FAILED, return False
```

Skip pattern for: Stack, Card, Event, Todo, Contact, Row, Page, Tag, and all minimal-param tests.

### 6B. Create-Failure Guards (every Phase 3B/3C/3D)

```python
if label not in created:
    # report FAILED, continue
```

### 6C. Missing-Tool Guards

```python
if get_tool is None:        # Phase 3B
if update_tool is None:     # Phase 3C
if delete_tool is None:     # Phase 3D
if tool is None:            # Phase 5A
```

### 6D. Availability Guards (Mail, Chat, Circles)

- **Phase 5L (Mail)**: 5+ nested `if/else` blocks branching on `accounts`, `account_id`, `mailboxes`, `mbox_db_id`, `msg_db_id`. Mail message tests are silently skipped if no messages exist.
- **Phase 5M (Chat)**: Branching on `token`, `second_user`, `attendee_id`, `msg_id`. Participant tests are skipped if only one user exists.
- **Phase 5G (Collaboration)**: Branching on `circle_id`, `created_file_source`, `created_coll_id`.
- **Phase 6 (Circle Members)**: Branching on `circle_id`, `second_user`, `member_id`.

### 6E. Response-Format Branching

The `get_list_items` function (line 358) tries 7 different keys (`items`, `results`, `rows`, `tree`, `collectives`, `pages`, `tags`, `data`), falls back to Toon parsing, and finally returns `[]`. This is adaptive, not deterministic.

---

## 7. Summary of Findings

| Finding | Severity | Location |
|---------|----------|----------|
| `notification_get` — not tested | Critical | Phase 5Q commented out (line 1993) |
| `notification_delete` — not tested | Critical | Phase 5Q commented out |
| `notification_delete_all` — not tested | Critical | Phase 5Q commented out |
| `circle_get` — not tested | High | No test entry exists |
| `circle_update` — not tested | High | No test entry exists |
| `collectives_restore` — not tested | High | No test entry exists |
| `circle_list` — not tested | High | Only called as side-effect, no test entry |
| Backend-broken tests list is empty | High | `DOMAIN_TESTS_BACKEND_BROKEN = []` (line 926) |
| `verify_tag_ops` always passes | High | Line 1983-1987 |
| `run_verify_delete` uses loose string match | Medium | Line 262 |
| Test cascade on create failure | Medium | All Phase 3B/3C/3D |
| Every test has conditional branching | Medium | Every phase |
| Mail tests heavily conditional | Medium | Phase 5L |
| Chat tests skip on conditions | Medium | Phase 5M |
| No test isolation (global `store`/`created`) | Medium | Lines 33-34 |

---

## 8. Recommended Corrections

1. **Uncomment Phase 5Q** with a proper notification discovery + get/delete cycle
2. **Add direct test entries** for `circle_get`, `circle_update`, `circle_list`, `collectives_restore`
3. **Fix `verify_tag_ops`** to check actual tag state instead of unconditionally passing
4. **Tighten `run_verify_delete`** error matching to use exact checks instead of substring matching
5. **Populate `DOMAIN_TESTS_BACKEND_BROKEN`** with tools that are expected to fail under error conditions
6. **Convert tests to isolated units** — each test should not depend on state from earlier tests
7. **Replace conditional skips** with deterministic always-run patterns where possible
