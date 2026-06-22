import asyncio
import os
import re
import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import quote

import httpx
from icalendar import Calendar as ICal, Event, Todo, vCalAddress, vText
import vobject
from vobject import vCard as VCard, readOne as vcard_read


# DAV XML namespaces
NS_DAV = "DAV:"
NS_CAL = "urn:ietf:params:xml:ns:caldav"
NS_CARD = "urn:ietf:params:xml:ns:carddav"


def _normalize_datetime(value: str) -> str:
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$', value):
        parsed = dt.datetime.fromisoformat(value)
        parsed = parsed.astimezone(dt.timezone.utc)
        return parsed.strftime('%Y-%m-%d %H:%M:%S')
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value):
        raise ValueError(
            f"Invalid datetime: {value}. Timezone offset is required. "
            "Must use format: 2026-06-22T15:00:00-04:00"
        )
    return value


def _to_dav_datetime(value: str) -> str:
    if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$', value):
        parsed = dt.datetime.fromisoformat(value)
        return parsed.strftime('%Y%m%dT%H%M%S')
    return value


class NextcloudClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or os.getenv("NEXTCLOUD_BASE_URL", "")).rstrip("/")
        if not self.base_url:
            raise ValueError(
                "NEXTCLOUD_BASE_URL required. Set NEXTCLOUD_BASE_URL env var "
                "or pass base_url."
            )

    @staticmethod
    def extract_username(api_key: str) -> str:
        import base64
        try:
            decoded = base64.b64decode(api_key).decode("utf-8")
            return decoded.split(":")[0]
        except Exception:
            return ""

    def _get_headers(self, api_key: Optional[str] = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "OCS-APIRequest": "true",
        }
        if api_key:
            headers["Authorization"] = f"Basic {api_key}"
        return headers

    def _get_dav_headers(self, api_key: Optional[str] = None) -> dict[str, str]:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Basic {api_key}"
        return headers

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float = 60.0,
        retries: int = 8,
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(1, retries + 1):
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
            is_rate_limited = (
                response.status_code == 429
                or (response.status_code >= 500 and "rate limited" in response.text.lower())
            )
            if is_rate_limited and attempt < retries:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        delay = 2 ** attempt
                else:
                    delay = 2 ** attempt
                await asyncio.sleep(delay)
                continue
            return response
        return response

    def _unwrap_ocs(self, data: Any, is_raw_json: bool = False) -> Any:
        if is_raw_json:
            return data
        if isinstance(data, dict) and "ocs" in data:
            ocs = data["ocs"]
            meta = ocs.get("meta", {})
            if meta.get("statuscode", 200) >= 400:
                raise Exception(f"OCS API error: {meta.get('statuscode')} {meta.get('message', '')}")
            return ocs.get("data", {})
        return data

    async def request(
        self,
        method: str,
        path: str,
        api_key: Optional[str] = None,
        is_ocs: bool = True,
        is_raw_json: bool = False,
        **kwargs: Any,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if is_ocs:
            headers = self._get_headers(api_key)
            params = kwargs.get("params") or {}
            if isinstance(params, dict) and "format" not in params:
                params["format"] = "json"
                kwargs["params"] = params
        else:
            headers = self._get_dav_headers(api_key)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        response = await self._request_with_retry(method, url, headers, timeout=60.0, **kwargs)
        response.raise_for_status()
        if response.status_code == 204:
            return {}
        ct = response.headers.get("content-type", "")
        if ct.startswith("application/json") or ct.startswith("text/json"):
            data = response.json()
            if is_ocs:
                return self._unwrap_ocs(data, is_raw_json)
            return data
        if ct.startswith("text/") or ct.startswith("application/xml") or ct.startswith("application/octet-stream"):
            return {"text": response.text, "content_type": ct, "status": response.status_code}
        return {"content": response.content, "content_type": ct, "status": response.status_code}

    async def get(self, path: str, api_key: Optional[str] = None, **kwargs: Any) -> Any:
        return await self.request("GET", path, api_key, **kwargs)

    async def post(self, path: str, api_key: Optional[str] = None, **kwargs: Any) -> Any:
        return await self.request("POST", path, api_key, **kwargs)

    async def put(self, path: str, api_key: Optional[str] = None, **kwargs: Any) -> Any:
        return await self.request("PUT", path, api_key, **kwargs)

    async def delete(self, path: str, api_key: Optional[str] = None, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, api_key, **kwargs)

    # =========================================================================
    # DAV Protocol Helpers
    # =========================================================================

    def _propfind_xml(self, props: list[str]) -> str:
        prop_elements = "\n".join(f"      <{p}/>" for p in props)
        return f"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/"
            xmlns:c="urn:ietf:params:xml:ns:caldav"
            xmlns:card="urn:ietf:params:xml:ns:carddav"
            xmlns:oc="http://owncloud.org/ns">
  <d:prop>
{prop_elements}
  </d:prop>
</d:propfind>"""

    def _calendar_query_xml(self, filters: dict[str, str] = None, comp_filter: str = "VEVENT") -> str:
        time_range = ""
        if filters:
            start = filters.get("start", "")
            end = filters.get("end", "")
            if start and end:
                time_range = f"""          <c:time-range start="{start}" end="{end}"/>"""
        return f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <d:resourcetype/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="{comp_filter}">
{time_range}
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

    def _freebusy_query_xml(self, start: str, end: str) -> str:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<c:free-busy-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <c:time-range start="{start}" end="{end}"/>
</c:free-busy-query>"""

    def _parse_dav_multistatus(self, xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        results = []
        for resp in root.findall(f"{{{NS_DAV}}}response"):
            href = resp.findtext(f"{{{NS_DAV}}}href", "")
            item = {"href": href}
            for propstat in resp.findall(f"{{{NS_DAV}}}propstat"):
                prop = propstat.find(f"{{{NS_DAV}}}prop")
                if prop is not None:
                    for child in prop:
                        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        item[tag] = child.text or ""
                        if tag == "resourcetype":
                            rt = []
                            for rtc in child:
                                rtt = rtc.tag.split("}")[-1] if "}" in rtc.tag else rtc.tag
                                rt.append(rtt)
                            item["resourcetype"] = rt
                        if child.text is None and len(child) > 0:
                            item[tag] = ET.tostring(child, encoding="unicode")
            results.append(item)
        return results

    async def _dav_request(
        self,
        method: str,
        path: str,
        api_key: Optional[str] = None,
        body: Optional[str] = None,
        content_type: str = "application/xml; charset=utf-8",
        extra_headers: Optional[dict[str, str]] = None,
    ) -> Any:
        headers = self._get_dav_headers(api_key)
        headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self.base_url}{path}"
        response = await self._request_with_retry(method, url, headers, content=body, timeout=120.0)
        response.raise_for_status()
        ct = response.headers.get("content-type", "")
        if "xml" in ct or "multistatus" in ct:
            return self._parse_dav_multistatus(response.text)
        return response.text

    async def _dav_get(self, path: str, api_key: Optional[str] = None) -> str:
        headers = self._get_dav_headers(api_key)
        url = f"{self.base_url}{path}"
        response = await self._request_with_retry("GET", url, headers, timeout=60.0)
        response.raise_for_status()
        return response.text

    async def _dav_put(self, path: str, body: str, api_key: Optional[str] = None, content_type: str = "text/calendar; charset=utf-8") -> Any:
        headers = self._get_dav_headers(api_key)
        headers["Content-Type"] = content_type
        url = f"{self.base_url}{path}"
        response = await self._request_with_retry("PUT", url, headers, content=body, timeout=60.0)
        if response.status_code in (200, 201, 204):
            return {"status": response.status_code}
        response.raise_for_status()
        return {"status": response.status_code}

    async def _dav_delete(self, path: str, api_key: Optional[str] = None) -> Any:
        headers = self._get_dav_headers(api_key)
        url = f"{self.base_url}{path}"
        response = await self._request_with_retry("DELETE", url, headers, timeout=60.0)
        if response.status_code in (200, 204):
            return {"status": response.status_code}
        response.raise_for_status()
        return {"status": response.status_code}

    async def _dav_move(self, source: str, destination: str, api_key: Optional[str] = None) -> Any:
        headers = self._get_dav_headers(api_key)
        headers["Destination"] = destination
        url = f"{self.base_url}{source}"
        response = await self._request_with_retry("MOVE", url, headers, timeout=60.0)
        if response.status_code in (200, 201, 204):
            return {"status": response.status_code}
        response.raise_for_status()
        return {"status": response.status_code}

    async def _dav_copy(self, source: str, destination: str, api_key: Optional[str] = None) -> Any:
        headers = self._get_dav_headers(api_key)
        headers["Destination"] = destination
        url = f"{self.base_url}{source}"
        response = await self._request_with_retry("COPY", url, headers, timeout=60.0)
        if response.status_code in (200, 201, 204):
            return {"status": response.status_code}
        response.raise_for_status()
        return {"status": response.status_code}

    async def _dav_mkcol(self, path: str, api_key: Optional[str] = None,
                         body: Optional[str] = None,
                         content_type: str = "application/xml; charset=utf-8") -> Any:
        headers = self._get_dav_headers(api_key)
        headers["Content-Type"] = content_type
        url = f"{self.base_url}{path}"
        response = await self._request_with_retry("MKCOL", url, headers, content=body, timeout=60.0)
        if response.status_code in (200, 201, 204):
            return {"status": response.status_code}
        response.raise_for_status()
        return {"status": response.status_code}

    # =========================================================================
    # iCal Helpers
    # =========================================================================

    def _create_ics_event(self, summary: str, dtstart: str, dtend: str,
                          description: str = "", location: str = "",
                          rrule: str = "", timezone: str = "",
                          uid: str = "") -> str:
        cal = ICal()
        cal.add("prodid", "-//Nextcloud MCP//nextcloud-mcp//EN")
        cal.add("version", "2.0")
        event = Event()
        event.add("summary", summary)
        event.add("dtstart", dt.datetime.fromisoformat(dtstart))
        event.add("dtend", dt.datetime.fromisoformat(dtend))
        if description:
            event.add("description", description)
        if location:
            event.add("location", location)
        if rrule:
            event.add("rrule", rrule)
        if uid:
            event.add("uid", uid)
        cal.add_component(event)
        return cal.to_ical().decode("utf-8")

    def _parse_ics_events(self, ics_data: str) -> list[dict[str, Any]]:
        cal = ICal.from_ical(ics_data)
        events = []
        for component in cal.walk():
            if component.name == "VEVENT":
                event = {}
                for key in ("SUMMARY", "DTSTART", "DTEND", "DESCRIPTION",
                            "LOCATION", "UID", "RRULE", "STATUS", "DTSTAMP"):
                    val = component.get(key)
                    if val:
                        if hasattr(val, "to_ical"):
                            event[key.lower()] = val.to_ical().decode("utf-8")
                        else:
                            event[key.lower()] = str(val)
                events.append(event)
        return events

    def _create_ics_todo(self, summary: str, description: str = "",
                         due_date: str = "", priority: int = 0,
                         status: str = "NEEDS-ACTION", uid: str = "",
                         calendar_uri: str = "") -> str:
        cal = ICal()
        cal.add("prodid", "-//Nextcloud MCP//nextcloud-mcp//EN")
        cal.add("version", "2.0")
        todo = Todo()
        todo.add("summary", summary)
        if description:
            todo.add("description", description)
        if due_date:
            todo.add("due", dt.datetime.fromisoformat(due_date))
        if priority:
            todo.add("priority", priority)
        todo.add("status", status)
        if uid:
            todo.add("uid", uid)
        cal.add_component(todo)
        return cal.to_ical().decode("utf-8")

    def _parse_ics_todos(self, ics_data: str) -> list[dict[str, Any]]:
        cal = ICal.from_ical(ics_data)
        todos = []
        for component in cal.walk():
            if component.name == "VTODO":
                todo = {}
                for key in ("SUMMARY", "DESCRIPTION", "DUE", "PRIORITY",
                            "STATUS", "UID", "DTSTAMP"):
                    val = component.get(key)
                    if val:
                        if hasattr(val, "to_ical"):
                            todo[key.lower()] = val.to_ical().decode("utf-8")
                        else:
                            todo[key.lower()] = str(val)
                todos.append(todo)
        return todos

    # =========================================================================
    # vCard Helpers
    # =========================================================================

    def _create_vcard(self, fn: str, email: str = "",
                      telephone: str = "", organization: str = "",
                      adr: str = "", uid: str = "") -> str:
        card = VCard()
        card.add("fn").value = fn
        name_parts = fn.split(" ", 1)
        family = name_parts[1] if len(name_parts) > 1 else ""
        given = name_parts[0]
        n = card.add("n")
        n.value = vobject.vcard.Name(family=family, given=given)
        if email:
            card.add("email").value = email
        if telephone:
            tel = card.add("tel")
            tel.value = telephone
            tel.type_param = "VOICE"
        if organization:
            card.add("org").value = [organization]
        if adr:
            card.add("adr").value = adr
        if uid:
            card.add("uid").value = uid
        return card.serialize()

    # =========================================================================
    # Domain: Users / Groups
    # =========================================================================

    async def get_users(self, api_key: str = None, search: str = "",
                        limit: int = 100, offset: int = 0,
                        include_all_fields: bool = False) -> Any:
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        data = await self.get("/ocs/v2.php/cloud/users", api_key, params=params)
        if isinstance(data, dict) and "users" in data:
            return data["users"]
        return data

    async def get_user(self, user_id: str, api_key: str = None,
                       include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/cloud/users/{quote(user_id, safe='')}", api_key)

    async def get_groups(self, api_key: str = None, limit: int = 100,
                         offset: int = 0, search: str = "",
                         include_all_fields: bool = False) -> Any:
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        data = await self.get("/ocs/v2.php/cloud/groups", api_key, params=params)
        if isinstance(data, dict) and "groups" in data:
            return data["groups"]
        return data

    async def get_group_members(self, group_id: str, api_key: str = None,
                                limit: int = 100, offset: int = 0,
                                include_all_fields: bool = False) -> Any:
        params = {"limit": limit, "offset": offset}
        data = await self.get(f"/ocs/v2.php/cloud/groups/{quote(group_id, safe='')}/users", api_key, params=params)
        if isinstance(data, dict) and "users" in data:
            return data["users"]
        return data

    # =========================================================================
    # Domain: File Sharing
    # =========================================================================

    async def list_shares(self, api_key: str = None, shared_with_me: bool = False,
                          path: str = "", include_all_fields: bool = False) -> Any:
        params = {}
        if shared_with_me:
            params["shared_with_me"] = "true"
        if path:
            params["path"] = path
        return await self.get("/ocs/v2.php/apps/files_sharing/api/v1/shares", api_key, params=params or None)

    async def get_share(self, share_id: int, api_key: str = None,
                        include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}", api_key)

    async def create_share(self, payload: dict[str, Any], api_key: str = None) -> Any:
        return await self.post("/ocs/v2.php/apps/files_sharing/api/v1/shares", api_key, json=payload)

    async def update_share(self, share_id: int, payload: dict[str, Any],
                           api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}", api_key, json=payload)

    async def delete_share(self, share_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}", api_key)

    # =========================================================================
    # Domain: User Status
    # =========================================================================

    async def get_user_status(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/user_status/api/v1/user_status", api_key)

    async def set_status(self, status_type: str, api_key: str = None) -> Any:
        return await self.put("/ocs/v2.php/apps/user_status/api/v1/user_status/status", api_key,
                            json={"statusType": status_type})

    async def set_predefined_message(self, message_id: str, api_key: str = None,
                                     clear_at: str = "") -> Any:
        body = {"messageId": message_id}
        if clear_at:
            body["clearAt"] = clear_at
        return await self.put("/ocs/v2.php/apps/user_status/api/v1/user_status/message/predefined", api_key, json=body)

    async def set_custom_message(self, message: str, status_icon: str,
                                 api_key: str = None, clear_at: str = "") -> Any:
        body = {"message": message, "statusIcon": status_icon}
        if clear_at:
            body["clearAt"] = clear_at
        return await self.put("/ocs/v2.php/apps/user_status/api/v1/user_status/message/custom", api_key, json=body)

    async def clear_message(self, api_key: str = None) -> Any:
        return await self.delete("/ocs/v2.php/apps/user_status/api/v1/user_status/message", api_key)

    async def get_predefined_statuses(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/user_status/api/v1/predefined_statuses", api_key)

    async def get_user_statuses(self, api_key: str = None, limit: int = 100,
                                include_all_fields: bool = False) -> Any:
        params = {"limit": limit} if limit else None
        return await self.get("/ocs/v2.php/apps/user_status/api/v1/statuses", api_key, params=params)

    # =========================================================================
    # Domain: DAV Events / OOO
    # =========================================================================

    async def get_ooo(self, user_id: str, api_key: str = None,
                      include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/dav/api/v1/outOfOffice/{quote(user_id, safe='')}", api_key)

    async def set_ooo(self, user_id: str, payload: dict[str, Any],
                      api_key: str = None) -> Any:
        return await self.post(f"/ocs/v2.php/apps/dav/api/v1/outOfOffice/{quote(user_id, safe='')}", api_key, json=payload)

    async def clear_ooo(self, user_id: str, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/dav/api/v1/outOfOffice/{quote(user_id, safe='')}", api_key)

    async def get_upcoming_events(self, api_key: str = None,
                                  location: str = "", limit: int = 20,
                                  include_all_fields: bool = False) -> Any:
        params = {"limit": limit}
        if location:
            params["location"] = location
        return await self.get("/ocs/v2.php/apps/dav/api/v1/events/upcoming", api_key, params=params)

    async def get_direct_download_url(self, file_id: int, api_key: str = None) -> Any:
        return await self.post("/ocs/v2.php/apps/dav/api/v1/direct", api_key, json={"fileId": file_id})

    # =========================================================================
    # Domain: Folder Tree
    # =========================================================================

    async def get_folder_tree(self, api_key: str = None, depth: int = 3,
                              path: str = "", include_all_fields: bool = False) -> Any:
        params = {"depth": depth}
        if path:
            params["path"] = path
        return await self.get("/ocs/v2.php/apps/files/api/v1/folder-tree", api_key, is_raw_json=True, params=params)

    # =========================================================================
    # Domain: Collaboration Collections
    # =========================================================================

    async def list_collection(self, collection_id: str, api_key: str = None,
                              include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/collaboration/resources/collections/{quote(collection_id, safe='')}", api_key)

    async def search_collections(self, search_query: str, api_key: str = None,
                                 limit: int = 50, include_all_fields: bool = False) -> Any:
        params = {}
        if search_query:
            return await self.get(f"/ocs/v2.php/collaboration/resources/collections/search/{quote(search_query, safe='')}", api_key, params=params or None)
        return await self.get("/ocs/v2.php/collaboration/resources/collections/search/", api_key)

    async def get_collections_by_resource(self, resource_type: str, resource_id: str,
                                         api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/collaboration/resources/{quote(resource_type, safe='')}/{quote(resource_id, safe='')}", api_key)

    # =========================================================================
    # Domain: Files Reminders
    # =========================================================================

    async def get_file_reminder(self, file_id: int, api_key: str = None,
                               include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/files_reminders/api/v1/{file_id}", api_key)

    async def set_file_reminder(self, file_id: int, due_date: str, api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/files_reminders/api/v1/{file_id}", api_key, json={"dueDate": due_date})

    async def delete_file_reminder(self, file_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/files_reminders/api/v1/{file_id}", api_key)

    # =========================================================================
    # Domain: Search
    # =========================================================================

    async def get_search_providers(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        data = await self.get("/ocs/v2.php/search/providers", api_key)
        if isinstance(data, dict) and "providers" in data:
            return data["providers"]
        return data

    async def search(self, provider_id: str, term: str, api_key: str = None,
                    limit: int = 50, cursor: str = "",
                    include_all_fields: bool = False) -> Any:
        params = {"term": term, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await self.get(f"/ocs/v2.php/search/providers/{quote(provider_id, safe='')}/search", api_key, params=params)

    # =========================================================================
    # Domain: Capabilities
    # =========================================================================

    async def get_capabilities(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        data = await self.get("/ocs/v2.php/cloud/capabilities", api_key)
        if isinstance(data, dict) and "capabilities" in data:
            return data["capabilities"]
        if isinstance(data, dict) and "capability" in data:
            return data["capability"]
        return data

    # =========================================================================
    # Domain: WebDAV File Management
    # =========================================================================

    async def webdav_get_user(self, api_key: str = None) -> str:
        data = await self.get_user("", api_key)
        if isinstance(data, dict):
            return data.get("id", "")
        return ""

    async def webdav_list_directory(self, path: str, api_key: str = None,
                                    depth: int = 1, include_all_fields: bool = False) -> Any:
        props = ["d:displayname", "d:getcontenttype", "d:getcontentlength",
                 "d:getlastmodified", "d:resourcetype", "d:getetag",
                 "oc:fileid"]
        body = self._propfind_xml(props)
        return await self._dav_request("PROPFIND", path, api_key, body=body,
                                       extra_headers={"Depth": str(depth)})

    async def webdav_read_file(self, path: str, api_key: str = None) -> Any:
        return await self._dav_get(path, api_key)

    async def webdav_write_file(self, path: str, content: str, api_key: str = None) -> Any:
        return await self._dav_put(path, content, api_key, content_type="application/octet-stream")

    async def webdav_create_directory(self, path: str, api_key: str = None) -> Any:
        return await self._dav_mkcol(path, api_key)

    async def webdav_delete_resource(self, path: str, api_key: str = None) -> Any:
        return await self._dav_delete(path, api_key)

    async def webdav_move_resource(self, source: str, destination: str,
                                   api_key: str = None) -> Any:
        return await self._dav_move(source, destination, api_key)

    async def webdav_copy_resource(self, source: str, destination: str,
                                   api_key: str = None) -> Any:
        return await self._dav_copy(source, destination, api_key)

    async def webdav_search_files(self, query: str, api_key: str = None,
                                  limit: int = 50, mime_type: str = "",
                                  include_all_fields: bool = False) -> Any:
        params = {"term": query, "limit": limit}
        if mime_type:
            params["mime"] = mime_type
        return await self.get("/ocs/v2.php/search/providers/files/search", api_key, params=params)

    async def webdav_list_favorites(self, api_key: str = None, limit: int = 50,
                                    include_all_fields: bool = False) -> Any:
        params = {"term": "", "limit": limit, "is-favorite": "true"}
        try:
            return await self.get("/ocs/v2.php/search/providers/files/search", api_key, params=params)
        except Exception:
            return []

    # =========================================================================
    # Domain: Notifications
    # =========================================================================

    async def list_notifications(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/notifications/api/v2/notifications", api_key)

    async def get_notification(self, notification_id: int, api_key: str = None,
                              include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/notifications/api/v2/notifications/{notification_id}", api_key)

    async def delete_notification(self, notification_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/notifications/api/v2/notifications/{notification_id}", api_key)

    async def delete_all_notifications(self, api_key: str = None) -> Any:
        return await self.delete("/ocs/v2.php/apps/notifications/api/v2/notifications", api_key)

    # =========================================================================
    # Domain: Activity
    # =========================================================================

    async def get_activity(self, api_key: str = None, limit: int = 50,
                           sort: str = "", object_type: str = "",
                           object_id: int = 0, include_all_fields: bool = False) -> Any:
        params = {"limit": limit}
        if sort:
            params["sort"] = sort
        if object_type:
            params["object_type"] = object_type
        if object_id:
            params["object_id"] = object_id
        return await self.get("/ocs/v2.php/apps/activity/api/v2/activity", api_key, params=params)

    async def get_activity_filters(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/activity/api/v2/activity/filters", api_key)

    async def get_activity_filter(self, filter_name: str, api_key: str = None,
                                  limit: int = 50, include_all_fields: bool = False) -> Any:
        params = {"limit": limit}
        return await self.get(f"/ocs/v2.php/apps/activity/api/v2/activity/{quote(filter_name, safe='')}", api_key, params=params)

    # =========================================================================
    # Domain: Calendar (CalDAV)
    # =========================================================================

    async def list_calendars(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        username = self.extract_username(api_key) if api_key else ""
        props = ["d:displayname", "d:resourcetype", "cs:getctag",
                 "c:supported-calendar-component-set", "d:getetag"]
        body = self._propfind_xml(props)
        return await self._dav_request("PROPFIND", f"/remote.php/dav/calendars/{username}/", api_key, body=body,
                                       extra_headers={"Depth": "1"})

    async def create_calendar(self, display_name: str, color: str = "",
                              api_key: str = None) -> Any:
        username = self.extract_username(api_key) if api_key else ""
        if not username:
            raise ValueError("Could not extract username from api_key")
        calendar_uri = display_name.lower().replace(" ", "_").replace("/", "_")
        path = f"/remote.php/dav/calendars/{quote(username, safe='')}/{quote(calendar_uri, safe='')}/"
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<d:mkcol xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:set>
    <d:prop>
      <d:resourcetype>
        <d:collection/>
        <cal:calendar/>
      </d:resourcetype>
      <d:displayname>{display_name}</d:displayname>
    </d:prop>
  </d:set>
</d:mkcol>"""
        await self._dav_mkcol(path, api_key, body=body)
        return {"calendar_uri": f"{username}/{calendar_uri}", "display_name": display_name, "status": "created"}

    async def update_calendar(self, calendar_uri: str, display_name: str,
                              color: str = None, api_key: str = None) -> Any:
        path = f"/remote.php/dav/calendars/{calendar_uri}/"
        prop_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:propertyupdate xmlns:d="DAV:">
  <d:set>
    <d:prop>
      <d:displayname>{display_name}</d:displayname>
    </d:prop>
  </d:set>
</d:propertyupdate>"""
        return await self._dav_request("PROPPATCH", path, api_key, body=prop_xml)

    async def delete_calendar(self, calendar_uri: str, api_key: str = None) -> Any:
        return await self._dav_delete(f"/remote.php/dav/calendars/{calendar_uri}/", api_key)

    async def list_events(self, calendar_uri: str, api_key: str = None,
                          from_date: str = "", to_date: str = "",
                          limit: int = 50, include_all_fields: bool = False) -> Any:
        filters = {}
        if from_date:
            filters["start"] = _to_dav_datetime(from_date)
        if to_date:
            filters["end"] = _to_dav_datetime(to_date)
        body = self._calendar_query_xml(filters if filters else None, "VEVENT")
        results = await self._dav_request("REPORT",
            f"/remote.php/dav/calendars/{calendar_uri}/", api_key, body=body,
            extra_headers={"Depth": "1"})
        parsed = []
        for r in results:
            cal_data = r.get("calendar-data", "")
            if cal_data:
                events = self._parse_ics_events(cal_data)
                parsed.extend(events)
        return parsed[:limit] if limit else parsed

    async def get_event(self, calendar_uri: str, event_uid: str,
                        api_key: str = None, include_all_fields: bool = False) -> Any:
        path = f"/remote.php/dav/calendars/{calendar_uri}/{event_uid}.ics"
        ics = await self._dav_get(path, api_key)
        events = self._parse_ics_events(ics)
        return events[0] if events else {}

    async def create_event(self, calendar_uri: str, summary: str,
                           dtstart: str, dtend: str, description: str = "",
                           location: str = "", rrule: str = "", timezone: str = "",
                           api_key: str = None, uid: str = "") -> Any:
        ics = self._create_ics_event(summary, dtstart, dtend, description,
                                     location, rrule, timezone, uid)
        euid = uid or summary.replace(" ", "_")
        result = await self._dav_put(
            f"/remote.php/dav/calendars/{calendar_uri}/{euid}.ics",
            ics, api_key)
        result["event_uid"] = euid
        return result

    async def update_event(self, calendar_uri: str, event_uid: str,
                           summary: str = None, dtstart: str = None,
                           dtend: str = None, description: str = None,
                           location: str = None, api_key: str = None) -> Any:
        path = f"/remote.php/dav/calendars/{calendar_uri}/{event_uid}.ics"
        ics = await self._dav_get(path, api_key)
        existing = self._parse_ics_events(ics)
        current = existing[0] if existing else {}
        final_summary = summary or current.get("summary", "")
        final_dtstart = dtstart or current.get("dtstart", "")
        final_dtend = dtend or current.get("dtend", "")
        final_desc = description if description is not None else current.get("description", "")
        final_loc = location if location is not None else current.get("location", "")
        new_ics = self._create_ics_event(
            final_summary, final_dtstart, final_dtend,
            final_desc, final_loc, "", "", event_uid)
        return await self._dav_put(path, new_ics, api_key)

    async def delete_event(self, calendar_uri: str, event_uid: str,
                           api_key: str = None) -> Any:
        return await self._dav_delete(
            f"/remote.php/dav/calendars/{calendar_uri}/{event_uid}.ics", api_key)

    async def create_meeting(self, summary: str, dtstart: str, dtend: str,
                             attendees: str, description: str = "",
                             location: str = "", calendar_uri: str = "",
                             api_key: str = None) -> Any:
        cal = ICal()
        cal.add("prodid", "-//Nextcloud MCP//nextcloud-mcp//EN")
        cal.add("version", "2.0")
        event = Event()
        event.add("summary", summary)
        event.add("dtstart", dt.datetime.fromisoformat(dtstart))
        event.add("dtend", dt.datetime.fromisoformat(dtend))
        if description:
            event.add("description", description)
        if location:
            event.add("location", location)
        for attendee_email in attendees.split(","):
            attendee_email = attendee_email.strip()
            if attendee_email:
                a = vCalAddress(f"mailto:{attendee_email}")
                a.params["PARTSTAT"] = vText("NEEDS-ACTION")
                a.params["ROLE"] = vText("REQ-PARTICIPANT")
                event.add("attendee", a)
        euid = summary.replace(" ", "_")
        cal.add_component(event)
        ics = cal.to_ical().decode("utf-8")
        if not calendar_uri:
            cals = await self.list_calendars(api_key)
            calendar_uri = cals[0].get("displayname", "personal") if cals else "personal"
        return await self._dav_put(
            f"/remote.php/dav/calendars/{calendar_uri}/{euid}.ics",
            ics, api_key)

    async def find_availability(self, calendar_uris: str, from_date: str,
                                to_date: str, timezone: str = "",
                                api_key: str = None) -> Any:
        start = _to_dav_datetime(from_date)
        end = _to_dav_datetime(to_date)
        body = self._freebusy_query_xml(start, end)
        calendars = calendar_uris.split(",")
        results = {}
        for cal_uri in calendars:
            cal_uri = cal_uri.strip()
            if cal_uri:
                try:
                    res = await self._dav_request("REPORT",
                        f"/remote.php/dav/calendars/{cal_uri}/", api_key, body=body,
                        extra_headers={"Depth": "0"})
                    results[cal_uri] = res
                except Exception as e:
                    results[cal_uri] = {"error": str(e)}
        return results

    # =========================================================================
    # Domain: Todos (CalDAV VTODO)
    # =========================================================================

    async def _get_todo_calendar_uri(self, api_key: str = None) -> str:
        cals = await self.list_calendars(api_key)
        for c in cals:
            rt = c.get("resourcetype", [])
            if "calendar" in rt:
                href = c.get("href", "")
                if href:
                    parts = href.rstrip("/").split("/")
                    return parts[-1] if parts else "personal"
        return "personal"

    async def list_todos(self, api_key: str = None, calendar_uri: str = "",
                         include_all_fields: bool = False) -> Any:
        cal_uri = calendar_uri or await self._get_todo_calendar_uri(api_key)
        body = self._calendar_query_xml(comp_filter="VTODO")
        results = await self._dav_request("REPORT",
            f"/remote.php/dav/calendars/{cal_uri}/", api_key, body=body,
            extra_headers={"Depth": "1"})
        parsed = []
        for r in results:
            cal_data = r.get("calendar-data", "")
            if cal_data:
                todos = self._parse_ics_todos(cal_data)
                parsed.extend(todos)
        return parsed

    async def create_todo(self, summary: str, description: str = "",
                          due_date: str = "", priority: int = 0,
                          status: str = "NEEDS-ACTION",
                          calendar_uri: str = "", api_key: str = None) -> Any:
        cal_uri = calendar_uri or await self._get_todo_calendar_uri(api_key)
        ics = self._create_ics_todo(summary, description, due_date, priority, status)
        euid = summary.replace(" ", "_")
        result = await self._dav_put(
            f"/remote.php/dav/calendars/{cal_uri}/{euid}.ics", ics, api_key)
        result["todo_uid"] = euid
        return result

    async def update_todo(self, todo_uid: str, calendar_uri: str,
                          summary: str = None, description: str = None,
                          priority: int = None, due_date: str = None,
                          status: str = None, api_key: str = None) -> Any:
        path = f"/remote.php/dav/calendars/{calendar_uri}/{todo_uid}.ics"
        try:
            ics = await self._dav_get(path, api_key)
            existing = self._parse_ics_todos(ics)
        except Exception:
            existing = []
        current = existing[0] if existing else {}
        final_summary = summary or current.get("summary", "")
        final_desc = description if description is not None else current.get("description", "")
        final_due = due_date or current.get("due", "")
        final_priority = priority if priority is not None else int(current.get("priority", 0))
        final_status = status or current.get("status", "NEEDS-ACTION")
        new_ics = self._create_ics_todo(final_summary, final_desc, final_due,
                                        final_priority, final_status, todo_uid)
        return await self._dav_put(path, new_ics, api_key)

    async def delete_todo(self, todo_uid: str, calendar_uri: str,
                          api_key: str = None) -> Any:
        return await self._dav_delete(
            f"/remote.php/dav/calendars/{calendar_uri}/{todo_uid}.ics", api_key)

    async def search_todos(self, query: str, api_key: str = None,
                           calendar_uri: str = "", limit: int = 50,
                           include_all_fields: bool = False) -> Any:
        cal_uri = calendar_uri or await self._get_todo_calendar_uri(api_key)
        body = self._calendar_query_xml(comp_filter="VTODO")
        results = await self._dav_request("REPORT",
            f"/remote.php/dav/calendars/{cal_uri}/", api_key, body=body,
            extra_headers={"Depth": "1"})
        parsed = []
        query_lower = query.lower()
        for r in results:
            cal_data = r.get("calendar-data", "")
            if cal_data:
                todos = self._parse_ics_todos(cal_data)
                for t in todos:
                    if query_lower in t.get("summary", "").lower() or \
                       query_lower in t.get("description", "").lower():
                        parsed.append(t)
        return parsed[:limit] if limit else parsed

    # =========================================================================
    # Domain: Contacts (CardDAV)
    # =========================================================================

    async def list_addressbooks(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        username = self.extract_username(api_key) if api_key else ""
        props = ["d:displayname", "d:resourcetype", "d:getetag",
                 "card:supported-addressbook-data"]
        body = self._propfind_xml(props)
        path = f"/remote.php/dav/addressbooks/users/{quote(username, safe='')}/" if username else "/remote.php/dav/addressbooks/"
        return await self._dav_request("PROPFIND", path, api_key, body=body,
                                       extra_headers={"Depth": "1"})

    async def create_addressbook(self, display_name: str, description: str,
                                  api_key: str = None) -> Any:
        username = self.extract_username(api_key) if api_key else ""
        if not username:
            raise ValueError("Could not extract username from api_key")
        uri = display_name.lower().replace(" ", "_").replace("/", "_")
        path = f"/remote.php/dav/addressbooks/users/{quote(username, safe='')}/{quote(uri, safe='')}/"
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<d:mkcol xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:set>
    <d:prop>
      <d:resourcetype>
        <d:collection/>
        <card:addressbook/>
      </d:resourcetype>
      <d:displayname>{display_name}</d:displayname>
    </d:prop>
  </d:set>
</d:mkcol>"""
        await self._dav_mkcol(path, api_key, body=body)
        return {"addressbook_uri": f"users/{username}/{uri}", "display_name": display_name, "description": description, "status": "created"}

    async def delete_addressbook(self, addressbook_uri: str, api_key: str = None) -> Any:
        return await self._dav_delete(f"/remote.php/dav/addressbooks/{addressbook_uri}/", api_key)

    async def list_contacts(self, addressbook_uri: str, api_key: str = None,
                            limit: int = 50, include_all_fields: bool = False) -> Any:
        props = ["d:displayname", "d:getetag", "card:addressbook-data"]
        body = self._propfind_xml(props)
        results = await self._dav_request("PROPFIND",
            f"/remote.php/dav/addressbooks/{addressbook_uri}/", api_key, body=body,
            extra_headers={"Depth": "1"})
        parsed = []
        for r in results:
            if r.get("addressbook-data") or r.get("href", "").endswith(".vcf"):
                if r.get("href", "").endswith(".vcf"):
                    vcard_text = await self._dav_get(
                        f"/remote.php/dav/addressbooks/{addressbook_uri}/" + r["href"].split("/")[-1],
                        api_key)
            try:
                vc = vcard_read(vcard_text)
                org_val = vc.org.value if hasattr(vc, "org") else ""
                if isinstance(org_val, list):
                    org_val = "; ".join(str(x) for x in org_val)
                contact = {"fn": vc.fn.value if hasattr(vc, "fn") else "",
                          "email": str(vc.email.value) if hasattr(vc, "email") else "",
                          "telephone": str(vc.tel.value) if hasattr(vc, "tel") else "",
                          "organization": org_val,
                          "uid": vc.uid.value if hasattr(vc, "uid") else ""}
                parsed.append(contact)
            except Exception:
                parsed.append({"href": r.get("href", "")})
        return parsed[:limit] if limit else parsed

    async def get_contact(self, addressbook_uri: str, contact_uid: str,
                          api_key: str = None, include_all_fields: bool = False) -> Any:
        path = f"/remote.php/dav/addressbooks/{addressbook_uri}/{contact_uid}.vcf"
        vcard_text = await self._dav_get(path, api_key)
        try:
            vc = vcard_read(vcard_text)
            org_val = vc.org.value if hasattr(vc, "org") else ""
            if isinstance(org_val, list):
                org_val = "; ".join(str(x) for x in org_val)
            return {
                "fn": vc.fn.value if hasattr(vc, "fn") else "",
                "email": str(vc.email.value) if hasattr(vc, "email") else "",
                "telephone": str(vc.tel.value) if hasattr(vc, "tel") else "",
                "organization": org_val,
                "adr": str(vc.adr.value) if hasattr(vc, "adr") else "",
                "contact_uid": vc.uid.value if hasattr(vc, "uid") else "",
            }
        except Exception as e:
            return {"error": str(e), "raw": vcard_text}

    async def create_contact(self, addressbook_uri: str, fn: str, email: str,
                             telephone: str = "", organization: str = "",
                             adr: str = "", api_key: str = None) -> Any:
        vcard_text = self._create_vcard(fn, email, telephone, organization, adr)
        cuid = fn.replace(" ", "_")
        result = await self._dav_put(
            f"/remote.php/dav/addressbooks/{addressbook_uri}/{cuid}.vcf",
            vcard_text, api_key, content_type="text/vcard; charset=utf-8")
        result["contact_uid"] = cuid
        return result

    async def update_contact(self, addressbook_uri: str, contact_uid: str,
                             fn: str = None, email: str = None,
                             telephone: str = None, organization: str = None,
                             adr: str = None, api_key: str = None) -> Any:
        path = f"/remote.php/dav/addressbooks/{addressbook_uri}/{contact_uid}.vcf"
        vcard_text = await self._dav_get(path, api_key)
        try:
            vc = vcard_read(vcard_text)
            current_fn = vc.fn.value if hasattr(vc, "fn") else ""
            current_email = str(vc.email.value) if hasattr(vc, "email") else ""
            current_tel = str(vc.tel.value) if hasattr(vc, "tel") else ""
            current_org = str(vc.org.value) if hasattr(vc, "org") else ""
            current_adr = str(vc.adr.value) if hasattr(vc, "adr") else ""
        except Exception:
            current_fn = current_email = current_tel = current_org = current_adr = ""
        final_fn = fn or current_fn
        final_email = email if email is not None else current_email
        final_tel = telephone if telephone is not None else current_tel
        final_org = organization if organization is not None else current_org
        final_adr = adr if adr is not None else current_adr
        new_vcard = self._create_vcard(final_fn, final_email, final_tel,
                                       final_org, final_adr, contact_uid)
        return await self._dav_put(path, new_vcard, api_key,
                                   content_type="text/vcard; charset=utf-8")

    async def delete_contact(self, addressbook_uri: str, contact_uid: str,
                             api_key: str = None) -> Any:
        return await self._dav_delete(
            f"/remote.php/dav/addressbooks/{addressbook_uri}/{contact_uid}.vcf", api_key)

    # =========================================================================
    # Domain: Notes (Custom API)
    # =========================================================================

    async def list_notes(self, api_key: str = None, category: str = "",
                         include_all_fields: bool = False) -> Any:
        params = {}
        if category:
            params["category"] = category
        return await self.get("/index.php/apps/notes/api/v1/notes", api_key, params=params or None, is_ocs=False)

    async def get_note(self, note_id: int, api_key: str = None,
                       include_all_fields: bool = False) -> Any:
        return await self.get(f"/index.php/apps/notes/api/v1/notes/{note_id}", api_key, is_ocs=False)

    async def create_note(self, payload: dict[str, Any], api_key: str = None) -> Any:
        return await self.post("/index.php/apps/notes/api/v1/notes", api_key, json=payload, is_ocs=False)

    async def update_note(self, note_id: int, payload: dict[str, Any],
                          api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/notes/api/v1/notes/{note_id}", api_key, json=payload, is_ocs=False)

    async def delete_note(self, note_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/index.php/apps/notes/api/v1/notes/{note_id}", api_key, is_ocs=False)

    async def get_attachment(self, note_id: int, attachment_id: str,
                             api_key: str = None) -> Any:
        return await self.get(
            f"/index.php/apps/notes/api/v1/notes/{note_id}/attachment/{attachment_id}",
            api_key, is_ocs=False)

    # =========================================================================
    # Domain: Deck (REST API — OCS routes return 404)
    # =========================================================================

    async def deck_list_boards(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/index.php/apps/deck/api/v1.0/boards", api_key, is_ocs=False)

    async def deck_get_board(self, board_id: int, api_key: str = None,
                             include_all_fields: bool = False) -> Any:
        return await self.get(f"/index.php/apps/deck/api/v1.0/boards/{board_id}", api_key, is_ocs=False)

    async def deck_create_board(self, payload: dict[str, Any], api_key: str = None) -> Any:
        return await self.post("/index.php/apps/deck/api/v1.0/boards", api_key, json=payload, is_ocs=False)

    async def deck_list_stacks(self, board_id: int, api_key: str = None,
                               include_all_fields: bool = False) -> Any:
        return await self.get(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks", api_key, is_ocs=False)

    async def deck_create_stack(self, board_id: int, payload: dict[str, Any],
                                api_key: str = None) -> Any:
        return await self.post(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks", api_key, json=payload, is_ocs=False)

    async def deck_list_cards(self, board_id: int, stack_id: int, api_key: str = None,
                              include_all_fields: bool = False) -> Any:
        stack = await self.get(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}", api_key, is_ocs=False)
        if isinstance(stack, dict):
            return stack.get("cards", [])
        return []

    async def deck_get_card(self, board_id: int, stack_id: int, card_id: int, api_key: str = None,
                            include_all_fields: bool = False) -> Any:
        return await self.get(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}", api_key, is_ocs=False)

    async def deck_create_card(self, board_id: int, stack_id: int, payload: dict[str, Any],
                                api_key: str = None) -> Any:
        return await self.post(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards",
                               api_key, json=payload, is_ocs=False)

    async def deck_update_card(self, board_id: int, stack_id: int, card_id: int,
                                payload: dict[str, Any], api_key: str = None) -> Any:
        allowed = {"title", "description", "duedate", "order"}
        clean = {k: v for k, v in payload.items() if k in allowed and v is not None}
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}",
                              api_key, json=clean, is_ocs=False)

    async def deck_delete_card(self, board_id: int, stack_id: int, card_id: int,
                                api_key: str = None) -> Any:
        return await self.delete(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}",
                                 api_key, is_ocs=False)

    async def deck_archive_card(self, board_id: int, stack_id: int, card_id: int,
                                 api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/archive",
                              api_key, is_ocs=False)

    async def deck_unarchive_card(self, board_id: int, stack_id: int, card_id: int,
                                   api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/unarchive",
                              api_key, is_ocs=False)

    async def deck_list_labels(self, board_id: int, api_key: str = None,
                                include_all_fields: bool = False) -> Any:
        board = await self.get(f"/index.php/apps/deck/api/v1.0/boards/{board_id}", api_key, is_ocs=False)
        if isinstance(board, dict):
            return board.get("labels", [])
        return []

    async def deck_assign_label(self, board_id: int, stack_id: int, card_id: int,
                                 label_id: int, api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignLabel",
                              api_key, json={"labelId": label_id}, is_ocs=False)

    async def deck_remove_label(self, board_id: int, stack_id: int, card_id: int,
                                 label_id: int, api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/removeLabel",
                              api_key, json={"labelId": label_id}, is_ocs=False)

    async def deck_assign_user(self, board_id: int, stack_id: int, card_id: int,
                                user_id: str, api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/assignUser",
                              api_key, json={"userId": user_id}, is_ocs=False)

    async def deck_remove_user(self, board_id: int, stack_id: int, card_id: int,
                                user_id: str, api_key: str = None) -> Any:
        return await self.put(f"/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/unassignUser",
                              api_key, json={"userId": user_id}, is_ocs=False)

    # =========================================================================
    # Domain: Mail (Custom API + OCS)
    # =========================================================================

    async def mail_list_accounts(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        try:
            return await self.get("/index.php/apps/mail/api/accounts", api_key, is_ocs=False)
        except Exception:
            return []

    async def mail_list_mailboxes(self, account_id: int, api_key: str = None,
                                  include_all_fields: bool = False) -> Any:
        return await self.get(f"/index.php/apps/mail/api/accounts/{account_id}/mailboxes", api_key, is_ocs=False)

    async def mail_list_messages(self, account_id: int, mailbox_id: str,
                                 api_key: str = None, limit: int = 50,
                                 include_all_fields: bool = False) -> Any:
        params = {"limit": limit}
        return await self.get(f"/index.php/apps/mail/api/accounts/{account_id}/mailboxes/{mailbox_id}/messages",
                            api_key, params=params, is_ocs=False)

    async def mail_get_message(self, account_id: int, message_id: int,
                               api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get(f"/index.php/apps/mail/api/accounts/{account_id}/messages/{message_id}",
                            api_key, is_ocs=False)

    async def mail_get_message_body(self, account_id: int, message_id: int,
                                    api_key: str = None) -> Any:
        return await self.get(f"/index.php/apps/mail/api/accounts/{account_id}/messages/{message_id}/body",
                            api_key, is_ocs=False)

    async def mail_send_message(self, account_id: int, subject: str, body: str,
                                to: str, cc: str = "", bcc: str = "",
                                api_key: str = None) -> Any:
        draft = await self.post(f"/index.php/apps/mail/api/accounts/{account_id}/drafts",
                               api_key, is_ocs=False,
                               json={"subject": subject, "body": {"body": body, "type": "text"},
                                     "to": [{"email": to}],
                                     "cc": [{"email": cc}] if cc else [],
                                     "bcc": [{"email": bcc}] if bcc else []})
        draft_id = ""
        if isinstance(draft, dict):
            draft_id = draft.get("id", "")
        if not draft_id:
            return draft
        return await self.post(f"/index.php/apps/mail/api/accounts/{account_id}/drafts/{draft_id}/send",
                              api_key, is_ocs=False, json={})

    async def mail_set_message_flags(self, account_id: int, message_id: int,
                                     seen: bool = None, flagged: bool = None,
                                     api_key: str = None) -> Any:
        flags = {}
        if seen is not None:
            flags["seen"] = seen
        if flagged is not None:
            flags["flagged"] = flagged
        return await self.post(f"/index.php/apps/mail/api/accounts/{account_id}/messages/{message_id}/flags",
                              api_key, json=flags, is_ocs=False)

    async def mail_move_message(self, account_id: int, message_id: int,
                                mailbox_id: str, api_key: str = None) -> Any:
        return await self.post(f"/index.php/apps/mail/api/accounts/{account_id}/mailboxes/{mailbox_id}/messages/{message_id}/move",
                              api_key, json={}, is_ocs=False)

    # =========================================================================
    # Domain: Talk (OCS)
    # =========================================================================

    async def talk_send_message(self, token: str, message: str,
                                api_key: str = None) -> Any:
        return await self.post(f"/ocs/v2.php/apps/spreed/api/v1/chat/{token}",
                              api_key, json={"message": message})

    async def talk_get_messages(self, token: str, api_key: str = None,
                                limit: int = 50, look_into_future: int = 0,
                                include_all_fields: bool = False) -> Any:
        params = {"limit": limit, "lookIntoFuture": look_into_future}
        return await self.get(f"/ocs/v2.php/apps/spreed/api/v1/chat/{token}",
                             api_key, params=params)

    async def talk_delete_message(self, token: str, message_id: int,
                                  api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/spreed/api/v1/chat/{token}/{message_id}", api_key)

    # =========================================================================
    # Domain: Tables (OCS)
    # =========================================================================

    async def tables_list_tables(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/tables/api/2/tables", api_key)

    async def tables_get_table(self, table_id: int, api_key: str = None,
                               include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/tables/api/2/tables/{table_id}", api_key)

    async def tables_create_table(self, payload: dict[str, Any], api_key: str = None) -> Any:
        return await self.post("/ocs/v2.php/apps/tables/api/2/tables", api_key, json=payload)

    async def tables_update_table(self, table_id: int, payload: dict[str, Any],
                                  api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/tables/api/2/tables/{table_id}", api_key, json=payload)

    async def tables_delete_table(self, table_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/tables/api/2/tables/{table_id}", api_key)

    async def tables_get_schema(self, table_id: int, api_key: str = None,
                                include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/tables/api/2/tables/scheme/{table_id}", api_key)

    async def tables_list_rows(self, table_id: int, api_key: str = None,
                               limit: int = 50, offset: int = 0,
                               include_all_fields: bool = False) -> Any:
        params = {"limit": limit, "offset": offset}
        return await self.get(f"/ocs/v2.php/apps/tables/api/2/tables/{table_id}/rows",
                             api_key, params=params)

    async def tables_create_row(self, table_id: int, data: str,
                                 api_key: str = None) -> Any:
        import json as _json
        data_obj = _json.loads(data) if isinstance(data, str) else data
        try:
            return await self.post(f"/ocs/v2.php/apps/tables/api/2/tables/{table_id}/rows",
                                  api_key, json={"data": data_obj})
        except Exception:
            return await self.post(f"/ocs/v2.php/apps/tables/api/2/tables/{table_id}/rows",
                                  api_key, json={"data": {}})

    async def tables_update_row(self, row_id: int, data: str = None,
                                 api_key: str = None) -> Any:
        import json as _json
        payload = {}
        if data is not None:
            payload["data"] = _json.loads(data) if isinstance(data, str) else data
        return await self.put(f"/ocs/v2.php/apps/tables/api/2/rows/{row_id}", api_key, json=payload)

    async def tables_delete_row(self, row_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/tables/api/2/rows/{row_id}", api_key)

    # =========================================================================
    # Domain: Collectives (OCS)
    # =========================================================================

    async def collectives_list(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/collectives/api/v1.0/collectives", api_key)

    async def collectives_create(self, payload: dict[str, Any], api_key: str = None) -> Any:
        return await self.post("/ocs/v2.php/apps/collectives/api/v1.0/collectives", api_key, json=payload)

    async def collectives_update_emoji(self, collective_id: int, emoji: str,
                                       api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}",
                             api_key, json={"emoji": emoji})

    async def collectives_trash(self, collective_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}", api_key)

    async def collectives_restore(self, collective_id: int, api_key: str = None) -> Any:
        return await self.patch(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/trash/{collective_id}", api_key)

    async def collectives_delete(self, collective_id: int, api_key: str = None) -> Any:
        try:
            await self.collectives_trash(collective_id, api_key)
        except Exception:
            pass
        return await self.delete(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/trash/{collective_id}", api_key)

    async def collectives_list_trashed(self, api_key: str = None, include_all_fields: bool = False) -> Any:
        return await self.get("/ocs/v2.php/apps/collectives/api/v1.0/collectives/trash", api_key)

    async def collectives_list_pages(self, collective_id: int, api_key: str = None,
                                     include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages", api_key)

    async def collectives_get_page(self, collective_id: int, page_id: int,
                                   api_key: str = None, include_all_fields: bool = False) -> Any:
        data = await self.get(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}", api_key)
        if isinstance(data, dict) and "page" in data:
            return data["page"]
        return data

    async def collectives_create_page(self, collective_id: int, parent_id: int,
                                      payload: dict[str, Any], api_key: str = None) -> Any:
        if not parent_id:
            pages_data = await self.collectives_list_pages(collective_id, api_key)
            pages = pages_data.get("pages", []) if isinstance(pages_data, dict) else []
            if pages:
                parent_id = pages[0].get("id", 0)
        path = f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{parent_id}"
        return await self.post(path, api_key, json=payload)

    async def collectives_trash_page(self, collective_id: int, page_id: int,
                                     api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}", api_key)

    async def collectives_restore_page(self, collective_id: int, page_id: int,
                                       api_key: str = None) -> Any:
        return await self.patch(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/trash/{page_id}", api_key)

    async def collectives_delete_page(self, collective_id: int, page_id: int,
                                      api_key: str = None) -> Any:
        try:
            await self.collectives_trash_page(collective_id, page_id, api_key)
        except Exception:
            pass
        return await self.delete(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/trash/{page_id}", api_key)

    async def collectives_move_page(self, collective_id: int, page_id: int,
                                    destination_collective_id: int,
                                    destination_parent_id: int = 0,
                                    api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}",
                             api_key, json={"destinationCollectiveId": destination_collective_id,
                                           "destinationParentId": destination_parent_id})

    async def collectives_set_page_emoji(self, collective_id: int, page_id: int,
                                         emoji: str, api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}/emoji",
                             api_key, json={"emoji": emoji})

    async def collectives_set_page_full_width(self, collective_id: int, page_id: int,
                                              is_full_width: bool, api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}/fullWidth",
                             api_key, json={"fullWidth": is_full_width})

    async def collectives_search_pages(self, collective_id: int, query: str,
                                       api_key: str = None, limit: int = 50,
                                       include_all_fields: bool = False) -> Any:
        params = {"search": query, "limit": limit}
        return await self.get(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/search",
                             api_key, params=params)

    async def collectives_list_tags(self, collective_id: int, api_key: str = None,
                                    include_all_fields: bool = False) -> Any:
        return await self.get(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/tags", api_key)

    async def collectives_create_tag(self, collective_id: int, payload: dict[str, Any],
                                     api_key: str = None) -> Any:
        return await self.post(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/tags",
                              api_key, json=payload)

    async def collectives_update_tag(self, collective_id: int, tag_id: int,
                                     payload: dict[str, Any], api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/tags/{tag_id}",
                             api_key, json=payload)

    async def collectives_delete_tag(self, collective_id: int, tag_id: int,
                                     api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/tags/{tag_id}", api_key)

    async def collectives_assign_tag(self, collective_id: int, page_id: int,
                                     tag_id: int, api_key: str = None) -> Any:
        return await self.put(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}/tags/{tag_id}",
                             api_key, json={})

    async def collectives_remove_tag(self, collective_id: int, page_id: int,
                                     tag_id: int, api_key: str = None) -> Any:
        return await self.delete(f"/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}/tags/{tag_id}", api_key)

    async def patch(self, path: str, api_key: Optional[str] = None, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, api_key, **kwargs)
