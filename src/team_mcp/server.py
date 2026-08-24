"""Servidor MCP "Team": 5 entrypoints graduados por complejidad para Claude.

Regla dura del proyecto: nunca se devuelven resultados intermedios a Claude.
Cada tool es un pipeline completo que termina en un Manifest compacto.
Los pipelines internos (quick, digest, implement, refactor, fix, investigate,
review, epic, validate, selftest) viven en workflows/ y no se exponen
directamente — ver la sección "Superficie expuesta a Claude" del plan.

Nota de API: el SDK `mcp` instalado (2.0.0) expone `mcp.server.mcpserver.
MCPServer`, el sucesor de FastMCP — no la API de decoradores
`@Server().list_tools()/call_tool()` de mcp 1.x. Los tipos de cada función
decorada con `@mcp.tool()` generan el JSON Schema automáticamente.
"""

from __future__ import annotations

import logging
from typing import Literal

from mcp.server.mcpserver import MCPServer

from team_mcp.config import load_config, load_env
from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router
from team_mcp.workflows import ask, epic, feature, task, validate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("team_mcp")

load_env()
_config = load_config()
_ledger = Ledger(_config)
_router = Router(_config, _ledger)

mcp = MCPServer(name="team")


@mcp.tool(
    description=(
        "Cambio pequeño y sin ambigüedad en 1 archivo: formateo, regex, "
        "renombrado, docstring, fix trivial. Sin tier premium. Si el gate "
        "determinista falla 2 veces, escala sola a team_feature."
    )
)
async def team_task(instruction: str, target_path: str) -> dict:
    manifest = await task.run(_router, _ledger, _config, instruction=instruction, target_path=target_path)
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Unidad de trabajo real: implementar (kind=new, default), "
        "refactorizar (kind=refactor, preserva comportamiento vía tests de "
        "caracterización), arreglar un bug (kind=fix, requiere "
        "repro_command: un comando que hoy falla y debe pasar a salir con "
        "código 0), o revisar código sin generarlo (kind=review). Fan-out "
        "de N workers + consenso por validación cruzada + crítica premium "
        "+ reparación acotada."
    )
)
async def team_feature(
    spec: str,
    target_paths: list[str],
    kind: Literal["new", "refactor", "fix", "review"] | None = None,
    repro_command: str | None = None,
) -> dict:
    manifest = await feature.run(
        _router, _ledger, _config,
        spec=spec, target_paths=target_paths, kind=kind, repro_command=repro_command,
    )
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Plan multi-tarea con dependencias (DAG). Orquesta team_feature "
        "sobre cada nodo en orden topológico, paralelizando ramas "
        "independientes, con presupuesto de tokens global."
    )
)
async def team_epic(plan: list[dict], budget: int | None = None) -> dict:
    manifest = await epic.run(_router, _ledger, _config, plan=plan, budget=budget)
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Pregunta sobre código o logs, sin escribir nada. Map-reduce en "
        "tier-context con verificación de citas (ruta:línea). scope_paths "
        "acepta archivos y directorios. Si la pregunta necesita contexto "
        "externo actual (versión de una librería, docs de una API externa, "
        "algo que no está en scope_paths), pasa allow_web_search=True para "
        "que use búsqueda web real de verdad, no memoria del modelo."
    )
)
async def team_ask(question: str, scope_paths: list[str], allow_web_search: bool = False) -> dict:
    manifest = await ask.run(
        _router, _ledger, _config,
        question=question, scope_paths=scope_paths, allow_web_search=allow_web_search,
    )
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Cierre: veredicto GO/NO-GO sobre el estado real del workspace "
        "(build, tests, lint, secretos, trazabilidad de requisitos)."
    )
)
async def team_validate(scope: str, spec_original: str | None = None, selftest: bool = False) -> dict:
    manifest = await validate.run(
        _router, _ledger, _config, scope=scope, spec_original=spec_original, selftest=selftest,
    )
    return manifest.model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
