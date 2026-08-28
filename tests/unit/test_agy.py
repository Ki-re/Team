from __future__ import annotations

from team_mcp.providers.agy import PremiumProvider


class _FakeGateway:
    async def chat(self, tier, messages, *, temperature=0.2, max_tokens=None, response_format=None):
        return {"choices": [{"message": {"content": "fallback response"}}]}


async def test_complete_records_last_error_when_agy_fails_and_falls_back(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    async def _raise_run_agy(prompt, *, timeout):
        raise RuntimeError("agy exit=1: quota exhausted")

    provider._run_agy = _raise_run_agy

    result = await provider.complete("prompt")

    assert result == "fallback response"
    assert provider.last_used == "fallback"
    assert provider.last_error == "RuntimeError: agy exit=1: quota exhausted"


async def test_complete_clears_last_error_on_success(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    async def _ok_run_agy(prompt, *, timeout):
        provider.last_used = "agy"  # the real _run_agy also sets this on success
        return "agy response"

    provider._run_agy = _ok_run_agy
    provider.last_error = "leftover from a previous call"

    result = await provider.complete("prompt")

    assert result == "agy response"
    assert provider.last_used == "agy"
    assert provider.last_error is None
