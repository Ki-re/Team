"""Shared fixtures for team-mcp's unit test suite.

Everything here is deterministic/local logic — nothing calls the real
gateway. Code paths that call live models (fan-out, critique, map-reduce)
are verified manually against the real gateway during development, not in
this suite (mocking httpx or spending real quota on every test run would
be the wrong trade-off for this project).
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
            agy_cli_args=None,
            sandbox_roots=sandbox_roots if sandbox_roots is not None else [tmp_path],
            dry_run=dry_run,
            token_budget_default=200_000,
            ledger_db=tmp_path / "ledger.sqlite3",
            cache_db=tmp_path / "cache.sqlite3",
        )

    return _make
