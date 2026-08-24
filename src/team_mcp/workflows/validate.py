"""team_validate: veredicto GO/NO-GO. Pendiente de Fase 5."""

from __future__ import annotations

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import Manifest
from team_mcp.providers.router import Router

_WORKFLOW = "team_validate"


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    scope: str,
    spec_original: str | None = None,
    selftest: bool = False,
) -> Manifest:
    return Manifest(
        tool=_WORKFLOW,
        tests_status="not_run",
        summary="team_validate aún no implementado (Fase 5 del plan).",
        dry_run=config.dry_run,
    )
