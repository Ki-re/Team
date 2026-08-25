"""Fixtures compartidos para la suite de tests unitarios de team-mcp.

Todo lo que hay aquí es lógica determinista/local — nada llama al gateway
real. Los caminos con modelos en vivo (fan-out, crítica, map-reduce) se
verifican manualmente contra el gateway real durante el desarrollo, no en
esta suite (ver el plan, Fase 9 — mockear httpx o gastar cuota real en
cada corrida de tests sería el trade-off equivocado para este proyecto).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from team_mcp.config import Config


@pytest.fixture
def make_config(tmp_path: Path):
    def _make(*, sandbox_roots: list[Path] | None = None, dry_run: bool = False) -> Config:
        return Config(
            gateway_url="http://example.invalid:4000",
            gateway_key="test-key",
            agy_path=None,
            agy_model=None,
            sandbox_roots=sandbox_roots if sandbox_roots is not None else [tmp_path],
            dry_run=dry_run,
            token_budget_default=200_000,
            ledger_db=tmp_path / "ledger.sqlite3",
            cache_db=tmp_path / "cache.sqlite3",
        )

    return _make
