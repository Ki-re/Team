from __future__ import annotations

from team_mcp.providers.agy import PremiumProvider


class _FakeGateway:
    async def chat(self, tier, messages, *, temperature=0.2, max_tokens=None, response_format=None):
        return {"choices": [{"message": {"content": "respuesta del fallback"}}]}


async def test_complete_records_last_error_when_agy_fails_and_falls_back(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    async def _raise_run_agy(prompt, *, timeout):
        raise RuntimeError("agy exit=1: cuota agotada")

    provider._run_agy = _raise_run_agy

    result = await provider.complete("prompt")

    assert result == "respuesta del fallback"
    assert provider.last_used == "fallback"
    assert provider.last_error == "RuntimeError: agy exit=1: cuota agotada"


async def test_complete_clears_last_error_on_success(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    async def _ok_run_agy(prompt, *, timeout):
        provider.last_used = "agy"  # el _run_agy real también lo marca al salir bien
        return "respuesta de agy"

    provider._run_agy = _ok_run_agy
    provider.last_error = "residuo de una llamada anterior"

    result = await provider.complete("prompt")

    assert result == "respuesta de agy"
    assert provider.last_used == "agy"
    assert provider.last_error is None
