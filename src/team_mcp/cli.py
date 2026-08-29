"""Diagnostic CLI: `python -m team_mcp.cli probe|run`.

Not the production path (that's server.py via stdio/MCP) — it exists so
each piece of the system can be verified in isolation during
development, as described in the plan's "End-to-end verification" section.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from team_mcp.config import load_config, load_env
from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router


async def _probe_agy(router: Router) -> None:
    result = await router.premium.probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def _probe_gateway(router: Router) -> None:
    alive = await router.gateway.liveliness()
    print(json.dumps({"gateway_alive": alive}, ensure_ascii=False))


async def _usage_report(router: Router, ledger: Ledger, days: int) -> None:
    """Combines two data sources that can't be merged server-side: the
    gateway's own tracking (everything that actually went through
    LiteLLM — tier-fast/coder/context, and the tier-premium API
    fallback) and team-mcp's own ledger (the only place `agy`'s usage
    ever lands, since it's a local subprocess CLI that never touches the
    gateway at all — see providers/agy.py's module docstring for why
    that's by design, not a gap to route around)."""
    import datetime as _dt

    end = _dt.date.today()  # noqa: DTZ011 — a daily usage report doesn't need tz-awareness, local date is fine
    start = end - _dt.timedelta(days=days - 1)

    gateway_totals: dict[str, dict[str, int]] = {}
    gateway_error: str | None = None
    try:
        data = await router.gateway.daily_activity(start.isoformat(), end.isoformat())
        for day in data.get("results", []):
            for model, m in day.get("breakdown", {}).get("models", {}).items():
                metrics = m.get("metrics", {})
                slot = gateway_totals.setdefault(model, {"tokens_in": 0, "tokens_out": 0, "requests": 0})
                slot["tokens_in"] += metrics.get("prompt_tokens", 0)
                slot["tokens_out"] += metrics.get("completion_tokens", 0)
                slot["requests"] += metrics.get("api_requests", 0)
    except Exception as exc:  # noqa: BLE001 — a reporting tool should degrade, not crash
        gateway_error = f"{type(exc).__name__}: {exc}"[:300]

    agy_totals = ledger.spend_summary(since_seconds=days * 86400, model_prefix="agy:")

    def _print_section(title: str, totals: dict[str, dict[str, int]], error: str | None = None) -> int:
        print(title)
        if error:
            print(f"  [unavailable: {error}]")
            return 0
        total = 0
        for model, t in sorted(totals.items(), key=lambda kv: -(kv[1]["tokens_in"] + kv[1]["tokens_out"])):
            tok = t["tokens_in"] + t["tokens_out"]
            total += tok
            print(f"  {model:55s} {tok:>10} tok  ({t['requests']} req)")
        if not totals:
            print("  (no activity in range)")
        print(f"  {'TOTAL':55s} {total:>10} tok")
        return total

    print(f"Usage report — last {days} day(s) ({start.isoformat()} to {end.isoformat()})")
    print()
    total_gw = _print_section(
        "Through the LiteLLM gateway (tier-fast/coder/context, premium API fallback):",
        gateway_totals, gateway_error,
    )
    print()
    total_agy = _print_section("agy (local subscription CLI — never touches the gateway):", agy_totals)
    print()
    print(f"GRAND TOTAL: {total_gw + total_agy} tokens")


async def _run_workflow(router: Router, ledger: Ledger, config, tool: str, kwargs: dict) -> None:
    from team_mcp.workflows import ask, epic, feature, task, validate

    # each workflow.run has a different signature (different kwargs) — Any
    # is deliberate here, it's a dynamic dispatcher by design, not an oversight.
    handlers: dict[str, Callable[..., Any]] = {
        "team_task": task.run,
        "team_feature": feature.run,
        "team_epic": epic.run,
        "team_ask": ask.run,
        "team_validate": validate.run,
    }
    handler = handlers.get(tool)
    if handler is None:
        print(f"unknown tool: {tool}. Options: {list(handlers)}", file=sys.stderr)
        raise SystemExit(2)

    manifest = await handler(router, ledger, config, **kwargs)
    print(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2))


def main() -> None:
    # manifests can contain model-generated text with any Unicode
    # character (seen crashing with "≈" against Windows' cp1252 console)
    # — the default console doesn't support it, so we force utf-8 with a
    # safe fallback instead of trusting the OS encoding.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(prog="team_mcp.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="check a provider's availability")
    p_probe.add_argument("--provider", choices=["agy", "gateway"], required=True)

    p_usage = sub.add_parser("usage", help="combined token usage report: gateway + agy")
    p_usage.add_argument("--days", type=int, default=1, help="trailing window size (default: 1, today only)")

    p_run = sub.add_parser("run", help="run a workflow directly")
    p_run.add_argument("tool", choices=["team_task", "team_feature", "team_epic", "team_ask", "team_validate"])
    p_run.add_argument("--spec")
    p_run.add_argument("--instruction")
    p_run.add_argument("--target-path")
    p_run.add_argument("--target-paths", nargs="*", default=None)
    p_run.add_argument("--kind", choices=["new", "refactor", "fix", "review"])
    p_run.add_argument("--repro-command")
    p_run.add_argument("--scope")
    p_run.add_argument("--scope-paths", nargs="*", default=None)
    p_run.add_argument("--question")
    p_run.add_argument("--spec-file")
    p_run.add_argument("--allow-web-search", action="store_true")
    p_run.add_argument("--plan-file", help="JSON with the list of nodes for team_epic")
    p_run.add_argument("--budget", type=int, default=None)
    p_run.add_argument("--selftest", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--update-docs", action="store_true")
    p_run.add_argument("--kb-path")

    args = parser.parse_args()

    load_env()

    if args.cmd == "run" and args.dry_run:
        import os
        os.environ["TEAM_DRY_RUN"] = "true"

    config = load_config()
    ledger = Ledger(config)

    spec_original = None
    if args.cmd == "run" and args.tool == "team_validate" and args.spec_file:
        with open(args.spec_file, encoding="utf-8") as f:
            spec_original = f.read()

    epic_plan: list[dict] = []
    if args.cmd == "run" and args.tool == "team_epic" and args.plan_file:
        with open(args.plan_file, encoding="utf-8") as f:
            epic_plan = json.load(f)

    async def _main() -> None:
        router = Router(config, ledger)
        try:
            if args.cmd == "probe":
                if args.provider == "agy":
                    await _probe_agy(router)
                else:
                    await _probe_gateway(router)
                return

            if args.cmd == "usage":
                await _usage_report(router, ledger, args.days)
                return

            kwargs: dict = {}
            if args.tool == "team_task":
                kwargs = {"instruction": args.instruction, "target_path": args.target_path}
            elif args.tool == "team_feature":
                kwargs = {
                    "spec": args.spec,
                    "target_paths": args.target_paths or [],
                    "kind": args.kind,
                    "repro_command": args.repro_command,
                    "update_docs": args.update_docs,
                    "kb_path": args.kb_path,
                }
            elif args.tool == "team_ask":
                kwargs = {
                    "question": args.question,
                    "scope_paths": args.scope_paths or [],
                    "allow_web_search": args.allow_web_search,
                }
            elif args.tool == "team_validate":
                kwargs = {
                    "scope": args.scope,
                    "spec_original": spec_original,
                    "selftest": args.selftest,
                }
            elif args.tool == "team_epic":
                kwargs = {
                    "plan": epic_plan, "budget": args.budget,
                    "update_docs": args.update_docs, "kb_path": args.kb_path,
                }

            await _run_workflow(router, ledger, config, args.tool, kwargs)
        finally:
            await router.aclose()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
