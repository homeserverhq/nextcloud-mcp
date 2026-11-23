"""Scenario and result plumbing for the audit suite."""

from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .mcp_client import AuditClient, AuditError, ToolCall
from .expect import require

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@dataclass
class ScenarioDef:
    domain: str
    name: str
    tools: tuple[str, ...]
    fn: Callable[["Scenario"], None]


REGISTRY: list[ScenarioDef] = []


def scenario(domain: str, name: str, tools: list[str] | tuple[str, ...]):
    """Register a scenario. ``tools`` is used by the coverage gate."""

    def decorator(fn: Callable[["Scenario"], None]):
        REGISTRY.append(ScenarioDef(domain, name, tuple(tools), fn))
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class Result:
    domain: str
    scenario: str
    label: str
    tool: str
    status: str
    detail: str = ""
    elapsed: float = 0.0
    args: dict[str, Any] = field(default_factory=dict)


class Run:
    def __init__(self, client: AuditClient, domains: set[str] | None = None):
        self.client = client
        self.domains = domains
        self.run_id = uuid.uuid4().hex[:8]
        self.results: list[Result] = []
        self.failures = 0
        self.skips = 0
        self.tools_called: set[str] = set()
        self.declared_tools: set[str] = set()

    def record(self, domain: str, scenario_name: str, label: str,
               tool: str, status: str, detail: str = "", elapsed: float = 0.0,
               args: dict[str, Any] | None = None) -> None:
        self.results.append(Result(
            domain=domain, scenario=scenario_name, label=label, tool=tool,
            status=status, detail=detail, elapsed=elapsed, args=args or {},
        ))
        if status == "FAIL":
            self.failures += 1
        elif status == "SKIP":
            self.skips += 1

    def scenarios(self) -> list[ScenarioDef]:
        selected = [
            sc for sc in REGISTRY
            if self.domains is None or sc.domain in self.domains
        ]
        order: dict[str, int] = {}
        for index, sc in enumerate(REGISTRY):
            order.setdefault(sc.domain, index)
        selected.sort(key=lambda sc: (order.get(sc.domain, 0), sc.name))
        return selected

    def run(self) -> None:
        for definition in self.scenarios():
            self.declared_tools.update(definition.tools)
            scenario = Scenario(self, definition)
            try:
                with scenario:
                    definition.fn(scenario)
            except Exception as exc:
                self.record(
                    definition.domain, definition.name, "scenario aborted",
                    "", "FAIL",
                    f"{type(exc).__name__}: {exc}\n"
                    f"{traceback.format_exc(limit=4)}",
                )


# ---------------------------------------------------------------------------
# Scenario context
# ---------------------------------------------------------------------------


class Scenario:
    def __init__(self, run: Run, definition: ScenarioDef):
        self.run = run
        self.definition = definition
        self.prefix = (
            f"MCPAudit-{run.run_id}-{definition.domain}-{definition.name}"
        )
        self.cleanups: list[tuple[str, dict[str, Any], str]] = []
        self.failed = False
        self._last: ToolCall | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "Scenario":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._run_cleanups()
        return False

    def _record(self, label: str, tool: str, status: str,
                detail: str = "", elapsed: float = 0.0,
                args: dict[str, Any] | None = None) -> None:
        if status == "FAIL":
            self.failed = True
        self.run.record(
            self.definition.domain, self.definition.name, label, tool,
            status, detail, elapsed, args,
        )

    # -- calls -------------------------------------------------------------

    def call(self, label: str, tool: str, args: dict[str, Any] | None = None,
             required: bool = True) -> ToolCall:
        call = self.run.client.call(tool, args)
        self.run.tools_called.add(tool)
        self._last = call
        if call.is_error:
            self._record(label, tool, "FAIL", call.error, call.elapsed, args)
            if required:
                raise AuditError(f"{tool}: {call.error}")
        else:
            self._record(label, tool, "PASS", "", call.elapsed, args)
        return call

    def call_any(self, label: str, tool: str,
                 args: dict[str, Any] | None = None) -> ToolCall:
        """Call a tool whose failure is reported but does not abort."""
        return self.call(label, tool, args, required=False)

    def expect_error(self, label: str, tool: str,
                     args: dict[str, Any] | None = None,
                     contains: tuple[str, ...] = ("404", "not found",
                                                  "does not exist")) -> ToolCall:
        """Assert that a tool rejects the call, without recording a false FAIL."""
        call = self.run.client.call(tool, args)
        self.run.tools_called.add(tool)
        lowered = call.error.lower()
        ok = call.is_error and any(needle in lowered for needle in contains)
        self._record(
            label, tool, "PASS" if ok else "FAIL",
            "" if ok else call.error, call.elapsed, args,
        )
        return call

    def attempt(self, label: str, tool: str,
                args: dict[str, Any] | None = None) -> ToolCall:
        """Best-effort call: success is PASS, failure is SKIP (e.g. cleanup)."""
        call = self.run.client.call(tool, args)
        self.run.tools_called.add(tool)
        self._record(
            label, tool, "PASS" if not call.is_error else "SKIP",
            "" if not call.is_error else call.error, call.elapsed, args,
        )
        return call

    # -- checks ------------------------------------------------------------

    def check(self, label: str, fn: Callable[[], Any],
              tool: str = "assert") -> Any:
        try:
            value = fn()
        except Exception as exc:
            self._record(label, tool, "FAIL", str(exc))
            return None
        self._record(label, tool, "PASS")
        return value

    def require(self, label: str, condition: Any, detail: str = "") -> bool:
        return bool(self.check(
            label,
            lambda: require(condition, detail or f"condition failed: {label}"),
        ))

    def items(self, label: str, call: ToolCall,
              field: str = "items") -> list[Any]:
        # The call already recorded the transport result; this records the
        # semantic shape check separately.
        holder: dict[str, list[Any]] = {}

        def _decode():
            holder["items"] = call.items(field)
            return holder["items"]

        self.check(label, _decode, tool=call.name)
        return holder.get("items", [])

    def skip(self, label: str, reason: str) -> None:
        self._record(label, "", "SKIP", reason)

    # -- cleanup -----------------------------------------------------------

    def cleanup(self, tool: str, args: dict[str, Any],
                label: str | None = None) -> None:
        self.cleanups.append((tool, dict(args), label or f"cleanup {tool}"))

    def _run_cleanups(self) -> None:
        for tool, args, label in reversed(self.cleanups):
            try:
                call = self.run.client.call(tool, args)
            except Exception as exc:
                self._record(label, tool, "WARN", f"cleanup raised: {exc}")
                continue
            if call.is_error:
                lowered = call.error.lower()
                benign = any(
                    phrase in lowered
                    for phrase in (
                        "not found", "does not exist", "404", "forbidden",
                        "403", "no such",
                    )
                )
                status = "PASS" if benign else "WARN"
                self._record(label, tool, status, call.error)
            else:
                self._record(label, tool, "PASS")
