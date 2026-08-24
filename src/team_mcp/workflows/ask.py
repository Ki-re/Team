"""team_ask: preguntas sobre código/logs con verificación de citas.

Pendiente de Fase 4 del plan. Versión mínima: manda la pregunta y las
rutas a tier-context sin map-reduce ni verificación de citas todavía.
"""

from __future__ import annotations

from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import Manifest
from team_mcp.providers.router import Router

_WORKFLOW = "team_ask"
_MAX_CHARS = 40_000  # tope simple mientras no exista chunking map-reduce


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    question: str,
    scope_paths: list[str],
) -> Manifest:
    chunks = []
    for raw in scope_paths:
        p = Path(raw)
        if p.exists() and p.is_file():
            chunks.append(f"--- {raw} ---\n{p.read_text(encoding='utf-8', errors='replace')}")
    context = "\n\n".join(chunks)[:_MAX_CHARS]

    prompt = (
        f"Contexto:\n{context}\n\nPregunta: {question}\n\n"
        "Responde de forma concisa. Cita ruta:línea cuando afirmes algo "
        "sobre el código."
    )
    answer = await router.context(_WORKFLOW, prompt)

    return Manifest(
        tool=_WORKFLOW,
        tests_status="not_run",
        summary=answer[:4000],
        dry_run=config.dry_run,
    )
