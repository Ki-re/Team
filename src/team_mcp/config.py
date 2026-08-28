"""MCP server configuration, read from environment variables (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# src/team_mcp/config.py -> team_mcp -> src -> repo root. Anchored here,
# not to cwd, because an MCP server registered globally (claude mcp add
# --scope user) launches with the cwd of whatever project the coding
# agent has open at the time — almost never this repo. Without this,
# .env and the local sqlite databases would end up looking for/creating
# themselves in random places.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def load_env() -> None:
    """Loads .env from the repo root, regardless of the process's cwd."""
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
    agy_cli_args: str | None

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

    # the process's cwd always joins the whitelist, in addition to
    # whatever TEAM_SANDBOX_ROOTS says. This is what makes the MCP
    # server's global registration (claude mcp add --scope user) useful:
    # .env lives at the root of THIS repo and always loads from there
    # regardless of the open project (see load_env), so a fixed
    # TEAM_SANDBOX_ROOTS pointing only here would leave the MCP server
    # unable to write to any other project. When the coding agent
    # launches the MCP server, the subprocess's cwd is that of whatever
    # project it has open — that's the natural default sandbox.
    sandbox_roots = [Path.cwd().resolve(), *_split_paths(e.get("TEAM_SANDBOX_ROOTS", ""))]

    return Config(
        gateway_url=e.get("TEAM_GATEWAY_URL", "http://localhost:4000").rstrip("/"),
        gateway_key=e.get("TEAM_GATEWAY_KEY", ""),
        agy_path=e.get("TEAM_AGY_PATH") or None,
        agy_model=e.get("TEAM_AGY_MODEL") or None,
        agy_cli_args=e.get("TEAM_AGY_CLI_ARGS") or None,
        sandbox_roots=sandbox_roots,
        dry_run=_bool(e.get("TEAM_DRY_RUN", ""), default=False),
        token_budget_default=int(e.get("TEAM_TOKEN_BUDGET_DEFAULT", "200000")),
        ledger_db=ledger_db,
        cache_db=cache_db,
    )
