"""Cache hash(prompt+modelo+params) -> resultado.

Los bucles de reparación acotados (engine/repair.py) repiten variaciones
pequeñas del mismo prompt contra el mismo modelo; esto evita pagar tokens
dos veces por el mismo trabajo dentro de una misma ejecución de pipeline.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing

from team_mcp.config import Config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);
"""

_DEFAULT_TTL_SECONDS = 24 * 3600


def make_key(model: str, messages: list[dict], temperature: float) -> str:
    blob = json.dumps({"model": model, "messages": messages, "temperature": temperature}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class Cache:
    def __init__(self, config: Config, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._db_path = str(config.cache_db)
        self._ttl = ttl_seconds
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get(self, key: str) -> str | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT value, ts FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        value, ts = row
        if time.time() - ts > self._ttl:
            return None
        return value

    def set(self, key: str, value: str) -> None:
        with closing(self._connect()) as con:
            con.execute(
                "INSERT INTO cache (key, value, ts) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, ts = excluded.ts",
                (key, value, time.time()),
            )
            con.commit()
