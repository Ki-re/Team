"""Servidor MCP "Team": 5 entrypoints graduados por complejidad para Claude.

Regla dura del proyecto: nunca se devuelven resultados intermedios a Claude.
Cada tool es un pipeline completo que termina en un Manifest compacto.
Los pipelines internos (quick, digest, implement, refactor, fix, investigate,
review, epic, validate, selftest) viven en workflows/ y no se exponen
directamente — ver la sección "Superficie expuesta a Claude" del plan.
"""

from __future__ import annotations

import json
import logging

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from team_mcp.config import load_config
from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("team_mcp")

app = Server("team")

load_dotenv()
_config = load_config()
_ledger = Ledger(_config)
_router = Router(_config, _ledger)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="team_task",
            description=(
                "Cambio pequeño y sin ambigüedad en 1 archivo: formateo, regex, "
                "renombrado, docstring, fix trivial. Sin tier premium. Si el gate "
                "determinista falla 2 veces, escala solo a team_feature."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "instruction": {"type": "string"},
                    "target_path": {"type": "string"},
                },
                "required": ["instruction", "target_path"],
            },
        ),
        Tool(
            name="team_feature",
            description=(
                "Unidad de trabajo real: implementar, refactorizar, arreglar un bug "
                "o revisar código. Fan-out de N workers + consenso por validación "
                "cruzada + crítica premium + reparación acotada."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "spec": {"type": "string"},
                    "target_paths": {"type": "array", "items": {"type": "string"}},
                    "kind": {
                        "type": "string",
                        "enum": ["new", "refactor", "fix", "review"],
                    },
                    "repro_command": {"type": "string"},
                },
                "required": ["spec", "target_paths"],
            },
        ),
        Tool(
            name="team_epic",
            description=(
                "Plan multi-tarea con dependencias (DAG). Orquesta team_feature "
                "sobre cada nodo en orden topológico, paralelizando ramas "
                "independientes, con presupuesto de tokens global."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "spec": {"type": "string"},
                                "target_paths": {"type": "array", "items": {"type": "string"}},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "spec", "target_paths"],
                        },
                    },
                    "budget": {"type": "integer"},
                },
                "required": ["plan"],
            },
        ),
        Tool(
            name="team_ask",
            description=(
                "Pregunta sobre código o logs, sin escribir nada. Map-reduce en "
                "tier-context con verificación de citas (ruta:línea)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "scope_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "scope_paths"],
            },
        ),
        Tool(
            name="team_validate",
            description=(
                "Cierre: veredicto GO/NO-GO sobre el estado real del workspace "
                "(build, tests, lint, secretos, trazabilidad de requisitos)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": "string"},
                    "spec_original": {"type": "string"},
                    "selftest": {"type": "boolean"},
                },
                "required": ["scope"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    from team_mcp.workflows import ask, epic, feature, task, validate

    handlers = {
        "team_task": task.run,
        "team_feature": feature.run,
        "team_epic": epic.run,
        "team_ask": ask.run,
        "team_validate": validate.run,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValueError(f"tool desconocida: {name}")

    manifest = await handler(_router, _ledger, _config, **arguments)
    return [TextContent(type="text", text=json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2))]


async def _amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    import anyio

    anyio.run(_amain)


if __name__ == "__main__":
    main()
