"""CLI for the Nextcloud MCP semantic audit suite.

Examples:
    python -m src.audit.runner --list-domains
    python -m src.audit.runner --domain system,users
    python -m src.audit.runner --all
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from pathlib import Path

from . import contracts
from .harness import Run
from .mcp_client import AuditClient
from .report import write_json, write_markdown, summarize, coverage


def _load_contracts() -> None:
    package_dir = Path(contracts.__file__).parent
    for module in pkgutil.iter_modules([str(package_dir)]):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{contracts.__name__}.{module.name}")


def _domains() -> list[str]:
    from .harness import REGISTRY

    return sorted({definition.domain for definition in REGISTRY})


def _parse_domains(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    domains: set[str] = set()
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                domains.add(part)
    return domains


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nextcloud MCP semantic audit")
    parser.add_argument("--domain", action="append",
                        help="Domain or comma-separated domains (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="Run every registered contract and enforce coverage")
    parser.add_argument("--list-domains", action="store_true",
                        help="List registered domains and exit")
    parser.add_argument("--url", help="MCP endpoint (default: MCP_URL env)")
    parser.add_argument("--report", default="/tmp/nextcloud-mcp-audit.md",
                        help="Markdown report path")
    parser.add_argument("--json", dest="json_path",
                        default="/tmp/nextcloud-mcp-audit.json",
                        help="JSON report path")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print the summary")
    args = parser.parse_args(argv)

    _load_contracts()
    if args.list_domains:
        for domain in _domains():
            print(domain)
        return 0

    domains = None if args.all else _parse_domains(args.domain)
    if not args.all and not domains:
        parser.error("pass --domain <domain> or --all")

    client = AuditClient(url=args.url)
    live_tools = client.tool_names()
    run = Run(client, domains=domains)

    if not args.quiet:
        print(f"Audit {run.run_id} against {client.url}", file=sys.stderr)
        print(f"Live tools: {len(live_tools)}", file=sys.stderr)

    run.run()
    client.close()

    write_markdown(run, live_tools, args.report,
                   sorted(domains) if domains else None)
    write_json(run, live_tools, args.json_path)

    summary = summarize(run)
    total_pass = sum(row["PASS"] for row in summary.values())
    total_fail = sum(row["FAIL"] for row in summary.values())
    total_skip = sum(row["SKIP"] for row in summary.values())
    total_warn = sum(row["WARN"] for row in summary.values())
    cov = coverage(run, live_tools)

    print("")
    print("| Domain | PASS | FAIL | SKIP | WARN |")
    print("|---|---:|---:|---:|---:|")
    for domain in sorted(summary):
        row = summary[domain]
        print(f"| {domain} | {row['PASS']} | {row['FAIL']} | "
              f"{row['SKIP']} | {row['WARN']} |")
    print(f"| Total | {total_pass} | {total_fail} | {total_skip} | {total_warn} |")
    print("")
    print(f"Coverage: {cov['declared']}/{cov['live']} tools declared, "
          f"{cov['called']} called")
    if cov["missing_contracts"]:
        print(f"Missing contracts: {len(cov['missing_contracts'])}")
    if cov["unknown_contract_tools"]:
        print(f"Unknown contract tools: {cov['unknown_contract_tools']}")
    print(f"Report: {args.report}")
    print(f"JSON:   {args.json_path}")

    coverage_failure = bool(cov["unknown_contract_tools"])
    if args.all and cov["missing_contracts"]:
        coverage_failure = True
    return 1 if (total_fail or coverage_failure) else 0


if __name__ == "__main__":
    raise SystemExit(main())
