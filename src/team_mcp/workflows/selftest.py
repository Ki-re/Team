"""selftest: health diagnostic for the 4 tiers (plan Phase 8).

Doesn't compare against a "golden set" of exact answers — free models
vary too much (same problem as model IDs: what works today can change
without notice). Instead, one cheap structural test per tier: does it
respond?, does the JSON it's asked for parse?, is the code it produces
syntactically valid? Every call goes through `router.*`, so the ledger
already records latency/success with no extra code here — this is what
keeps the plan's tier table honest.

Internal pipeline, not a tool: reached via `team_validate(selftest=True)`.
"""

from __future__ import annotations

import ast
import time

from team_mcp.engine.jsonio import extract_json_dict
from team_mcp.providers.router import Router

_WORKFLOW = "selftest"


async def _check_fast(router: Router) -> dict:
    t0 = time.monotonic()
    try:
        raw = await router.fast(_WORKFLOW, "What's 12 + 30? Answer with only the number.")
        ok = "42" in raw
        detail = "" if ok else f"expected '42' in the answer, got: {raw[:100]}"
    except Exception as exc:  # noqa: BLE001 — selftest must never crash, only report
        ok, detail = False, str(exc)[:200]
    return {"tier": "tier-fast", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000, "detail": detail}


async def _check_coder(router: Router) -> dict:
    t0 = time.monotonic()
    prompt = (
        "Respond ONLY with JSON: "
        '{"code": "<a valid Python function called triple(n) that returns n*3>"}'
    )
    try:
        raw = await router.coder(_WORKFLOW, prompt, temperature=0.3)
        data = extract_json_dict(raw)
        code = data["code"]
        ast.parse(code)
        ok, detail = True, ""
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)[:200]
    return {"tier": "tier-coder", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000, "detail": detail}


async def _check_context(router: Router) -> dict:
    t0 = time.monotonic()
    try:
        raw = await router.context(_WORKFLOW, "Summarize in one sentence what an MCP server is.")
        ok = len(raw.strip()) > 10
        detail = "" if ok else "empty or too-short response"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)[:200]
    return {"tier": "tier-context", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000, "detail": detail}


async def _check_premium(router: Router) -> dict:
    t0 = time.monotonic()
    try:
        raw = await router.premium_review(_WORKFLOW, "Respond with only the word: OK")
        ok = len(raw.strip()) > 0
        detail = f"via {router.premium.last_used}" if ok else "empty response"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)[:200]
    return {
        "tier": "tier-premium", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000,
        "detail": detail, "provider": router.premium.last_used,
    }


async def run(router: Router) -> dict:
    """Returns a raw dict (not a Manifest): consumed by validate.py, which
    builds the final Manifest exposed as a tool."""
    fast, coder, context, premium = await _check_fast(router), await _check_coder(router), \
        await _check_context(router), await _check_premium(router)
    results = [fast, coder, context, premium]
    all_ok = all(r["ok"] for r in results)
    return {"all_ok": all_ok, "results": results}


def render_summary(report: dict) -> str:
    lines = []
    for r in report["results"]:
        status = "OK" if r["ok"] else "FAIL"
        extra = f" ({r['detail']})" if r.get("detail") else ""
        lines.append(f"[{status}] {r['tier']}: {r['latency_ms']:.0f}ms{extra}")
    return "selftest — " + ("all tiers healthy" if report["all_ok"] else "some tiers degraded") + \
        "\n" + "\n".join(lines)
