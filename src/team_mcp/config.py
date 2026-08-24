"""Configuración del servidor MCP, leída de variables de entorno (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _split_paths(raw: str) -> list[Path]:
    if not raw.strip():
        return []
    sep = ";" if os.name == "nt" else ":"
    return [Path(p).resolve() for p in raw.split(sep) if p.strip()]


def _bool(raw: str, default: bool) -> bool:
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    gateway_url: str
    gateway_key: str

    agy_path: str | None
    agy_model: str | None

    sandbox_roots: list[Path]
    dry_run: bool

    token_budget_default: int
    ledger_db: Path
    cache_db: Path

    tier_fast: str = "tier-fast"
    tier_coder: str = "tier-coder"
    tier_context: str = "tier-context"
    tier_premium: str = "tier-premium"


def load_config(env: dict[str, str] | None = None) -> Config:
    e = env if env is not None else os.environ

    ledger_db = Path(e.get("TEAM_LEDGER_DB", ".team_sandbox/ledger.sqlite3"))
    cache_db = Path(e.get("TEAM_CACHE_DB", ".team_sandbox/cache.sqlite3"))
    ledger_db.parent.mkdir(parents=True, exist_ok=True)
    cache_db.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        gateway_url=e.get("TEAM_GATEWAY_URL", "http://203.0.113.10:4000").rstrip("/"),
        gateway_key=e.get("TEAM_GATEWAY_KEY", ""),
        agy_path=e.get("TEAM_AGY_PATH") or None,
        agy_model=e.get("TEAM_AGY_MODEL") or None,
        sandbox_roots=_split_paths(e.get("TEAM_SANDBOX_ROOTS", "")),
        dry_run=_bool(e.get("TEAM_DRY_RUN", ""), default=False),
        token_budget_default=int(e.get("TEAM_TOKEN_BUDGET_DEFAULT", "200000")),
        ledger_db=ledger_db,
        cache_db=cache_db,
    )
