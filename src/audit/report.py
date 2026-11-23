"""Markdown and JSON reporting for the audit suite."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from collections import defaultdict
from typing import Any

from .harness import Run

STATUS_ORDER = ("FAIL", "WARN", "SKIP", "PASS")


def _user_from_token(token: str) -> str:
    try:
        return base64.b64decode(token).decode("utf-8").split(":", 1)[0]
    except Exception:
        return "(unknown)"


def summarize(run: Run) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {status: 0 for status in STATUS_ORDER}
    )
    for result in run.results:
        summary[result.domain][result.status] += 1
    return dict(summary)


def coverage(run: Run, live_tools: set[str]) -> dict[str, Any]:
    missing = sorted(live_tools - run.declared_tools)
    unknown = sorted(run.declared_tools - live_tools)
    return {
        "live": len(live_tools),
        "declared": len(run.declared_tools),
        "called": len(run.tools_called),
        "missing_contracts": missing,
        "unknown_contract_tools": unknown,
        "uncalled": sorted(run.declared_tools - run.tools_called),
    }


def failures(run: Run) -> list[dict[str, Any]]:
    return [
        {
            "domain": r.domain,
            "scenario": r.scenario,
            "label": r.label,
            "tool": r.tool,
            "detail": r.detail,
            "args": r.args,
        }
        for r in run.results
        if r.status == "FAIL"
    ]


def write_json(run: Run, live_tools: set[str], path: str) -> None:
    payload = {
        "run_id": run.run_id,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "user": _user_from_token(run.client.token),
        "url": run.client.url,
        "summary": summarize(run),
        "coverage": coverage(run, live_tools),
        "failures": failures(run),
        "results": [
            {
                "domain": r.domain,
                "scenario": r.scenario,
                "label": r.label,
                "tool": r.tool,
                "status": r.status,
                "detail": r.detail,
                "elapsed": r.elapsed,
                "args": r.args,
            }
            for r in run.results
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_markdown(run: Run, live_tools: set[str], path: str,
                   selected_domains: list[str] | None = None) -> None:
    cov = coverage(run, live_tools)
    summary = summarize(run)
    total_pass = sum(row["PASS"] for row in summary.values())
    total_fail = sum(row["FAIL"] for row in summary.values())
    total_warn = sum(row["WARN"] for row in summary.values())
    total_skip = sum(row["SKIP"] for row in summary.values())

    lines: list[str] = []
    lines.append("# Nextcloud MCP Tool Audit")
    lines.append("")
    lines.append(f"- **Run:** `{run.run_id}`")
    lines.append(f"- **Date:** {dt.datetime.now(dt.timezone.utc).isoformat()}")
    lines.append(f"- **User:** `{_user_from_token(run.client.token)}`")
    lines.append(f"- **Endpoint:** `{run.client.url}`")
    if selected_domains:
        lines.append(f"- **Domains:** {', '.join(selected_domains)}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Domain | PASS | FAIL | SKIP | WARN |")
    lines.append("|---|---:|---:|---:|---:|")
    for domain in sorted(summary):
        row = summary[domain]
        lines.append(
            f"| {domain} | {row['PASS']} | {row['FAIL']} | "
            f"{row['SKIP']} | {row['WARN']} |"
        )
    lines.append(
        f"| **Total** | **{total_pass}** | **{total_fail}** | "
        f"**{total_skip}** | **{total_warn}** |"
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Live tools: **{cov['live']}**")
    lines.append(f"- Declared by contracts: **{cov['declared']}**")
    lines.append(f"- Called in this run: **{cov['called']}**")
    lines.append(
        f"- Tools with no contract: **{len(cov['missing_contracts'])}**"
    )
    if cov["missing_contracts"]:
        lines.append("")
        lines.append("Missing contracts:")
        lines.append("")
        for name in cov["missing_contracts"]:
            lines.append(f"- `{name}`")
    if cov["unknown_contract_tools"]:
        lines.append("")
        lines.append("Contracts reference tools that do not exist:")
        lines.append("")
        for name in cov["unknown_contract_tools"]:
            lines.append(f"- `{name}`")
    if cov["uncalled"]:
        lines.append("")
        lines.append("Declared but not reached in this run:")
        lines.append("")
        for name in cov["uncalled"]:
            lines.append(f"- `{name}`")
    lines.append("")

    failed = failures(run)
    lines.append("## Findings")
    lines.append("")
    if not failed:
        lines.append("No failures.")
    else:
        for item in failed:
            lines.append(
                f"### {item['domain']} / {item['scenario']} / {item['label']}"
            )
            lines.append("")
            lines.append(f"- Tool: `{item['tool']}`")
            if item["args"]:
                lines.append(
                    f"- Args: `{json.dumps(item['args'], ensure_ascii=False)[:500]}`"
                )
            lines.append("")
            lines.append("```")
            lines.append(item["detail"][:6000])
            lines.append("```")
            lines.append("")

    warnings = [r for r in run.results if r.status == "WARN"]
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for item in warnings:
            lines.append(
                f"- `{item.domain}` / `{item.scenario}` / `{item.label}`: "
                f"{item.detail[:500]}"
            )
        lines.append("")

    skips = [r for r in run.results if r.status == "SKIP"]
    if skips:
        lines.append("## Skips")
        lines.append("")
        for item in skips:
            lines.append(
                f"- `{item.domain}` / `{item.label}`: {item.detail}"
            )
        lines.append("")

    lines.append("## All Checks")
    lines.append("")
    current = None
    for result in run.results:
        key = (result.domain, result.scenario)
        if key != current:
            current = key
            lines.append(f"### {result.domain} / {result.scenario}")
            lines.append("")
        marker = {
            "PASS": "PASS",
            "FAIL": "FAIL",
            "SKIP": "SKIP",
            "WARN": "WARN",
        }.get(result.status, result.status)
        tool = f" (`{result.tool}`)" if result.tool else ""
        lines.append(f"- {marker} {result.label}{tool}")
    lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
