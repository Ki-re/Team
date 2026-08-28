"""selftest: diagnóstico de salud de los 4 tiers (Fase 8 del plan).

No compara contra un "golden set" de respuestas exactas — los modelos
gratis varían demasiado (mismo problema que los IDs de modelo: lo que
funciona hoy puede cambiar sin aviso). En su lugar, una prueba estructural
barata por tier: ¿responde?, ¿el JSON que se le pide parsea?, ¿el código
que produce es sintácticamente válido? Cada llamada pasa por `router.*`,
así que el ledger ya registra latencia/éxito sin código adicional aquí —
esto es lo que mantiene honesta la tabla de tiers del plan.

Pipeline interno, no una tool: se alcanza vía `team_validate(selftest=True)`.
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
        raw = await router.fast(_WORKFLOW, "¿Cuánto es 12 + 30? Responde solo con el número.")
        ok = "42" in raw
        detail = "" if ok else f"esperaba '42' en la respuesta, recibí: {raw[:100]}"
    except Exception as exc:  # noqa: BLE001 — selftest nunca debe reventar, solo reportar
        ok, detail = False, str(exc)[:200]
    return {"tier": "tier-fast", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000, "detail": detail}


async def _check_coder(router: Router) -> dict:
    t0 = time.monotonic()
    prompt = (
        "Responde ÚNICAMENTE con JSON: "
        '{"code": "<una función Python válida llamada triple(n) que devuelva n*3>"}'
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
        raw = await router.context(_WORKFLOW, "Resume en una frase qué es un servidor MCP.")
        ok = len(raw.strip()) > 10
        detail = "" if ok else "respuesta vacía o demasiado corta"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)[:200]
    return {"tier": "tier-context", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000, "detail": detail}


async def _check_premium(router: Router) -> dict:
    t0 = time.monotonic()
    try:
        raw = await router.premium_review(_WORKFLOW, "Responde solo con la palabra: OK")
        ok = len(raw.strip()) > 0
        detail = f"vía {router.premium.last_used}" if ok else "respuesta vacía"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)[:200]
    return {
        "tier": "tier-premium", "ok": ok, "latency_ms": (time.monotonic() - t0) * 1000,
        "detail": detail, "provider": router.premium.last_used,
    }


async def run(router: Router) -> dict:
    """Devuelve un dict crudo (no Manifest): lo consume validate.py, que es
    quien construye el Manifest final expuesto como tool."""
    fast, coder, context, premium = await _check_fast(router), await _check_coder(router), \
        await _check_context(router), await _check_premium(router)
    results = [fast, coder, context, premium]
    all_ok = all(r["ok"] for r in results)
    return {"all_ok": all_ok, "results": results}


def render_summary(report: dict) -> str:
    lines = []
    for r in report["results"]:
        status = "OK" if r["ok"] else "FALLO"
        extra = f" ({r['detail']})" if r.get("detail") else ""
        lines.append(f"[{status}] {r['tier']}: {r['latency_ms']:.0f}ms{extra}")
    return "selftest — " + ("todos los tiers sanos" if report["all_ok"] else "hay tiers degradados") + \
        "\n" + "\n".join(lines)
