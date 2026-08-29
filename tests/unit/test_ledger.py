from __future__ import annotations

import sqlite3
import time
from contextlib import closing

from team_mcp.engine.ledger import Ledger, SpendEvent


def _record(ledger: Ledger, *, model: str, tokens_in: int, tokens_out: int) -> None:
    ledger.record(SpendEvent(
        workflow="wf", tier="tier-premium", model=model,
        tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=1.0, ok=True,
    ))


def test_spend_summary_sums_not_averages_tokens_per_model(make_config):
    ledger = Ledger(make_config())
    _record(ledger, model="agy:agy", tokens_in=100, tokens_out=10)
    _record(ledger, model="agy:agy", tokens_in=200, tokens_out=20)

    summary = ledger.spend_summary(since_seconds=3600)

    assert summary["agy:agy"] == {"tokens_in": 300, "tokens_out": 30, "requests": 2}


def test_spend_summary_filters_by_model_prefix(make_config):
    ledger = Ledger(make_config())
    _record(ledger, model="agy:agy", tokens_in=100, tokens_out=10)
    _record(ledger, model="gemini/gemini-3.1-flash-lite", tokens_in=500, tokens_out=50)

    summary = ledger.spend_summary(since_seconds=3600, model_prefix="agy:")

    assert set(summary) == {"agy:agy"}


def test_spend_summary_excludes_events_outside_the_time_window(make_config):
    ledger = Ledger(make_config())
    _record(ledger, model="agy:agy", tokens_in=100, tokens_out=10)

    with closing(sqlite3.connect(str(ledger._db_path))) as con:
        con.execute("UPDATE spend SET ts = ?", (time.time() - 999_999,))
        con.commit()

    summary = ledger.spend_summary(since_seconds=3600)

    assert summary == {}


def test_spend_summary_empty_ledger_returns_empty_dict(make_config):
    ledger = Ledger(make_config())
    assert ledger.spend_summary(since_seconds=3600) == {}
