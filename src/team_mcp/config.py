"""Configuración del servidor MCP, leída de variables de entorno (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# src/team_mcp/config.py -> team_mcp -> src -> raíz del repo. Se ancla aquí,
# no a cwd, porque un MCP registrado en global (claude mcp add --scope
# user) se lanza con el cwd de lo que sea que Claude Code tenga abierto en
# ese momento — casi nunca este repo. Sin esto, .env y las bases sqlite
# locales terminarían buscándose/creándose en sitios aleatorios.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def load_env() -> None:
    """Carga .env desde la raíz del repo, sin importar el cwd del proceso."""
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=REPO_ROOT / ".env")


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
    if not ledger_db.is_absolute():
        ledger_db = REPO_ROOT / ledger_db
    if not cache_db.is_absolute():
        cache_db = REPO_ROOT / cache_db
    ledger_db.parent.mkdir(parents=True, exist_ok=True)
    cache_db.parent.mkdir(parents=True, exist_ok=True)

    # el cwd del proceso siempre entra en la whitelist, además de lo que
    # diga TEAM_SANDBOX_ROOTS. Es lo que hace útil el registro global del
    # MCP (claude mcp add --scope user): .env vive en la raíz de ESTE repo
    # y se carga siempre desde ahí sin importar el proyecto abierto (ver
    # load_env), así que un TEAM_SANDBOX_ROOTS fijo apuntando solo aquí
    # dejaría el MCP incapaz de escribir en cualquier otro proyecto. Cuando
    # Claude Code lanza el servidor MCP, el cwd del subproceso es el del
    # proyecto que tiene abierto — ese es el sandbox natural por defecto.
    sandbox_roots = [Path.cwd().resolve(), *_split_paths(e.get("TEAM_SANDBOX_ROOTS", ""))]

    return Config(
        gateway_url=e.get("TEAM_GATEWAY_URL", "http://203.0.113.10:4000").rstrip("/"),
        gateway_key=e.get("TEAM_GATEWAY_KEY", ""),
        agy_path=e.get("TEAM_AGY_PATH") or None,
        agy_model=e.get("TEAM_AGY_MODEL") or None,
        sandbox_roots=sandbox_roots,
        dry_run=_bool(e.get("TEAM_DRY_RUN", ""), default=False),
        token_budget_default=int(e.get("TEAM_TOKEN_BUDGET_DEFAULT", "200000")),
        ledger_db=ledger_db,
        cache_db=cache_db,
    )
