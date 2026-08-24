"""team_epic: orquesta team_feature sobre un DAG. Pendiente de Fase 5."""

from __future__ import annotations

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import Manifest
from team_mcp.providers.router import Router

_WORKFLOW = "team_epic"


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    plan: list[dict],
    budget: int | None = None,
) -> Manifest:
    return Manifest(
        tool=_WORKFLOW,
        tests_status="not_run",
        summary="team_epic aún no implementado (Fase 5 del plan). Depende de team_feature.",
        dry_run=config.dry_run,
    )
