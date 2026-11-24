"""Direct backend helpers for audit fixtures and cleanup.

Only used for things the MCP surface cannot do (marking a favorite, deleting
audit-created mail, fetching a direct-download URL). Never touches pre-existing
data.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import quote

import httpx


def _credentials() -> tuple[str, str]:
    token = os.getenv("API_KEY", "").strip()
    if not token:
        return (
            os.getenv("NEXTCLOUD_USER", ""),
            os.getenv("NEXTCLOUD_PASSWORD", ""),
        )
    decoded = base64.b64decode(token).decode("utf-8")
    user, _, password = decoded.partition(":")
    return user, password


class Backend:
    def __init__(self, base_url: str | None = None,
                 user: str | None = None, password: str | None = None):
        self.base_url = (
            base_url
            or os.getenv("NEXTCLOUD_BASE_URL")
            or "http://nextcloud-web:80"
        ).rstrip("/")
        if user is None:
            self.user, self.password = _credentials()
        else:
            self.user, self.password = user, password or ""
        self.auth = (self.user, self.password)

    def dav_path(self, path: str) -> str:
        return (
            f"/remote.php/dav/files/{quote(self.user, safe='')}/"
            f"{quote(path.lstrip('/'), safe='/')}"
        )

    def set_favorite(self, path: str, favorite: bool = True) -> None:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<d:propertyupdate xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:set>
    <d:prop>
      <oc:favorite>{1 if favorite else 0}</oc:favorite>
    </d:prop>
  </d:set>
</d:propertyupdate>"""
        response = httpx.request(
            "PROPPATCH",
            f"{self.base_url}{self.dav_path(path)}",
            auth=self.auth,
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "OCS-APIRequest": "true",
            },
            content=body,
            timeout=30.0,
        )
        response.raise_for_status()

    def put_bytes(self, path: str, data: bytes,
                  content_type: str = "application/octet-stream") -> None:
        """Upload a binary fixture that cannot be written as a string."""
        response = httpx.request(
            "PUT",
            f"{self.base_url}{self.dav_path(path)}",
            auth=self.auth,
            headers={"Content-Type": content_type},
            content=data,
            timeout=60.0,
        )
        response.raise_for_status()

    def fetch(self, url: str) -> httpx.Response:
        if url.startswith("/"):
            url = f"{self.base_url}{url}"
        return httpx.get(url, auth=self.auth, follow_redirects=True, timeout=60.0)

    def delete_mail_message(self, account_id: int, message_id: int) -> None:
        """Delete an audit-created mail message through the Mail app API."""
        response = httpx.request(
            "DELETE",
            f"{self.base_url}/index.php/apps/mail/api/messages/{message_id}",
            auth=self.auth,
            headers={"OCS-APIRequest": "true"},
            timeout=30.0,
        )
        if response.status_code not in (200, 202, 204, 404):
            response.raise_for_status()

    def sync_mailbox(self, mailbox_id: int) -> dict:
        response = httpx.request(
            "POST",
            f"{self.base_url}/index.php/apps/mail/api/mailboxes/{mailbox_id}/sync",
            auth=self.auth,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def talk_room(self, token: str) -> dict:
        response = httpx.get(
            f"{self.base_url}/ocs/v2.php/apps/spreed/api/v4/room/{token}",
            auth=self.auth,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return (response.json().get("ocs") or {}).get("data") or {}
