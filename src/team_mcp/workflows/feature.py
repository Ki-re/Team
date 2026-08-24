"""team_feature: fan-out + consenso + crítica premium + reparación.

Pendiente de Fase 3/4 del plan (engine/consensus.py, critic.py, repair.py).
Stub deliberado: falla de forma clara en vez de fingir que ejecutó el
pipeline completo.
"""

from __future__ import annotations

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import Manifest
from team_mcp.providers.router import Router

_WORKFLOW = "team_feature"


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    spec: str,
    target_paths: list[str],
    kind: str | None = None,
    repro_command: str | None = None,
) -> Manifest:
    return Manifest(
        tool=_WORKFLOW,
        tests_status="not_run",
        summary=(
            "team_feature aún no implementado (Fase 3/4 del plan: consensus.py, "
            "critic.py, repair.py). Usa team_task para cambios de 1 archivo mientras tanto."
        ),
        dry_run=config.dry_run,
    )
