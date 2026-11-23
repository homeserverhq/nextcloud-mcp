"""Minimal JSON-RPC client used by the audit runner.

The audit suite talks to the same streamable-HTTP MCP endpoint as any other
consumer. It deliberately does not import the server code, so it observes the
real wire behaviour (TOON strings, structured content, error messages).
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import httpx
from toon_mcp import toon_to_json


class AuditError(AssertionError):
    """Raised when a tool response violates an expectation."""


def _token_from_env() -> str:
    token = os.getenv("API_KEY", "").strip()
    if token:
        return token
    user = os.getenv("NEXTCLOUD_USER", "").strip()
    password = os.getenv("NEXTCLOUD_PASSWORD", "").strip()
    if not user or not password:
        raise SystemExit(
            "Set API_KEY (base64 of user:password) or NEXTCLOUD_USER / "
            "NEXTCLOUD_PASSWORD before running the audit."
        )
    return base64.b64encode(f"{user}:{password}".encode()).decode()


class ToolCall:
    """One tools/call result plus convenience decoding."""

    def __init__(self, name: str, arguments: dict[str, Any],
                 result: dict[str, Any] | None, elapsed: float):
        self.name = name
        self.arguments = arguments
        self.result = result or {}
        self.elapsed = elapsed
        self.is_error = bool(self.result.get("isError"))
        content = self.result.get("content") or [{}]
        self.text = content[0].get("text", "") if content else ""
        structured = self.result.get("structuredContent")
        self.data: dict[str, Any] = structured if isinstance(structured, dict) else {}
        if not self.data and self.text:
            try:
                parsed = json.loads(self.text)
                if isinstance(parsed, dict):
                    self.data = parsed
            except Exception:
                pass

    @property
    def error(self) -> str:
        return (self.text or "unknown tool error").strip()

    def items(self, field: str = "items") -> list[Any]:
        return decode_items(self.data, self.name, field)

    def to_check(self) -> dict[str, Any]:
        return {
            "tool": self.name,
            "args": self.arguments,
            "elapsed": round(self.elapsed, 3),
            "is_error": self.is_error,
            "text": self.text[:4000],
            "data": self.data,
        }


def decode_items(data: dict[str, Any], tool: str,
                 field: str = "items") -> list[Any]:
    """Decode a response's items field, accepting TOON strings and lists."""
    if not isinstance(data, dict):
        raise AuditError(f"{tool}: response is {type(data).__name__}, expected dict")
    if field not in data:
        raise AuditError(f"{tool}: response is missing {field!r}: {data!r}")
    raw = data[field]
    if isinstance(raw, str):
        try:
            raw = toon_to_json(raw)
        except Exception as exc:
            raise AuditError(
                f"{tool}: {field} is not valid TOON ({exc}); raw={raw!r}"
            ) from exc
    if not isinstance(raw, list):
        raise AuditError(
            f"{tool}: {field} is {type(raw).__name__}, expected list; raw={raw!r}"
        )
    return raw


class AuditClient:
    """Synchronous MCP JSON-RPC client."""

    def __init__(self, url: str | None = None, token: str | None = None,
                 timeout: float = 120.0):
        self.url = (
            url
            or os.getenv("MCP_URL")
            or f"http://localhost:{os.getenv('MCP_SERVER_PORT', '80')}/mcp"
        )
        self.token = token or _token_from_env()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        self.client = httpx.Client(timeout=timeout)
        self._id = 0
        self._session_id = ""
        self._tools: list[dict[str, Any]] | None = None
        self._initialize()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = dict(self.headers)
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        response = self.client.post(self.url, headers=headers, json=payload)
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        response.raise_for_status()
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    def _initialize(self) -> None:
        self._post({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nextcloud-audit", "version": "1.0"},
            },
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolCall:
        self._id += 1
        started = time.perf_counter()
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        data = self._post(payload)
        elapsed = time.perf_counter() - started
        if "error" in data:
            raise AuditError(f"{name}: JSON-RPC error: {data['error']}")
        return ToolCall(name, arguments or {}, data.get("result"), elapsed)

    def tools(self) -> list[dict[str, Any]]:
        if self._tools is None:
            self._id += 1
            data = self._post({
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/list",
                "params": {},
            })
            self._tools = (data.get("result") or {}).get("tools", [])
        return self._tools

    def tool_names(self) -> set[str]:
        return {t.get("name", "") for t in self.tools()}

    def close(self) -> None:
        self.client.close()
