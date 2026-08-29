from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router


def _last_note(ledger: Ledger) -> str:
    with closing(sqlite3.connect(str(ledger._db_path))) as con:
        row = con.execute("SELECT note FROM spend ORDER BY id DESC LIMIT 1").fetchone()
        return row[0]


def _last_tokens(ledger: Ledger) -> tuple[int, int]:
    with closing(sqlite3.connect(str(ledger._db_path))) as con:
        row = con.execute(
            "SELECT tokens_in, tokens_out FROM spend ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0], row[1]


async def test_call_failure_records_real_error_in_ledger_note(make_config):
    config = make_config()
    ledger = Ledger(config)
    router = Router(config, ledger)

    async def _raise(*args, **kwargs):
        raise TimeoutError("gateway didn't respond after 120.0s")

    router.gateway.chat = _raise  # monkeypatch: no real network involved

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
        return "fallback response"

    router.premium.complete = _complete
    router.premium.last_used = "fallback"
    router.premium.last_error = "RuntimeError: agy exit=1: something failed"

    await router.premium_review("wf", "prompt")

    note = _last_note(ledger)
    assert note == "RuntimeError: agy exit=1: something failed"


async def test_premium_review_records_real_token_usage_when_available(make_config):
    # regression test: premium_review used to hardcode tokens_in=0,
    # tokens_out=0 unconditionally, discarding real usage from both the
    # agy-CLI JSON path and the gateway fallback (which always had it).
    config = make_config()
    ledger = Ledger(config)
    router = Router(config, ledger)

    async def _complete(prompt, **kwargs):
        return "agy response"

    router.premium.complete = _complete
    router.premium.last_used = "agy"
    router.premium.last_usage = {"input_tokens": 321, "output_tokens": 45}

    await router.premium_review("wf", "prompt")

    tokens_in, tokens_out = _last_tokens(ledger)
    assert (tokens_in, tokens_out) == (321, 45)


async def test_premium_review_falls_back_to_zero_tokens_when_usage_unknown(make_config):
    config = make_config()
    ledger = Ledger(config)
    router = Router(config, ledger)

    async def _complete(prompt, **kwargs):
        return "response from a plain-text CLI override"

    router.premium.complete = _complete
    router.premium.last_used = "agy"
    router.premium.last_usage = None

    await router.premium_review("wf", "prompt")

    assert _last_tokens(ledger) == (0, 0)
