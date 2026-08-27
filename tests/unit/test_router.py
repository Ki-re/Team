from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router


def _last_note(ledger: Ledger) -> str:
    with closing(sqlite3.connect(str(ledger._db_path))) as con:  # noqa: SLF001 — test interno
        row = con.execute("SELECT note FROM spend ORDER BY id DESC LIMIT 1").fetchone()
        return row[0]


async def test_call_failure_records_real_error_in_ledger_note(make_config):
    config = make_config()
    ledger = Ledger(config)
    router = Router(config, ledger)

    async def _raise(*args, **kwargs):
        raise TimeoutError("gateway no respondió tras 120.0s")

    router.gateway.chat = _raise  # monkeypatch: sin tocar la red real

    with pytest.raises(TimeoutError):
        await router.fast("wf", "prompt")

    note = _last_note(ledger)
    assert "TimeoutError" in note
    assert "120.0s" in note


async def test_premium_review_records_agy_fallback_reason_in_note(make_config):
    config = make_config()
    ledger = Ledger(config)
    router = Router(config, ledger)

    async def _complete(prompt, **kwargs):
        return "respuesta del fallback"

    router.premium.complete = _complete
    router.premium.last_used = "fallback"
    router.premium.last_error = "RuntimeError: agy exit=1: algo falló"

    await router.premium_review("wf", "prompt")

    note = _last_note(ledger)
    assert note == "RuntimeError: agy exit=1: algo falló"
