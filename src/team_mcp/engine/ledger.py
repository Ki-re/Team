"""Token counters per tier/workflow, hard cap, telemetry in SQLite.

Exists for two things: (1) so team_epic can stop cleanly when the budget
runs out, including the spend from task->feature auto-escalations, and
(2) feeding `selftest` real cost/latency data per model.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

from team_mcp.config import Config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    workflow TEXT NOT NULL,
    tier TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    ok INTEGER NOT NULL,
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_spend_workflow ON spend(workflow);
CREATE INDEX IF NOT EXISTS idx_spend_model ON spend(model);
"""


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: int, budget: int) -> None:
        super().__init__(f"budget exhausted: {spent}/{budget} tokens")
        self.spent = spent
        self.budget = budget


@dataclass
class SpendEvent:
    workflow: str
    tier: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    ok: bool
    note: str = ""


class Ledger:
    def __init__(self, config: Config) -> None:
        self._db_path = str(config.ledger_db)
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def record(self, event: SpendEvent) -> None:
        with closing(self._connect()) as con:
            con.execute(
                "INSERT INTO spend (ts, workflow, tier, model, tokens_in, tokens_out, "
                "latency_ms, ok, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(), event.workflow, event.tier, event.model,
                    event.tokens_in, event.tokens_out, event.latency_ms,
                    int(event.ok), event.note,
                ),
            )
            con.commit()

    def spent_tokens(self, workflow_run_id: str) -> int:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM spend "
                "WHERE workflow = ?",
                (workflow_run_id,),
            ).fetchone()
            return int(row[0])

    def check_budget(self, workflow_run_id: str, budget: int) -> None:
        spent = self.spent_tokens(workflow_run_id)
        if spent >= budget:
            raise BudgetExceeded(spent, budget)

    def model_stats(self, since_seconds: float = 7 * 24 * 3600) -> list[dict]:
        cutoff = time.time() - since_seconds
        with closing(self._connect()) as con:
            rows = con.execute(
                "SELECT model, COUNT(*), AVG(ok), AVG(latency_ms), "
                "AVG(tokens_in + tokens_out) FROM spend WHERE ts >= ? GROUP BY model",
                (cutoff,),
            ).fetchall()
        return [
            {
                "model": r[0], "calls": r[1], "pass_rate": r[2],
                "avg_latency_ms": r[3], "avg_tokens": r[4],
            }
            for r in rows
        ]
