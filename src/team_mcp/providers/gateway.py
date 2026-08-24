"""Cliente HTTP hacia el gateway LiteLLM (formato OpenAI /chat/completions)."""

from __future__ import annotations

import httpx

from team_mcp.config import Config


class GatewayError(RuntimeError):
    pass


class GatewayProvider:
    """Habla con un tier lógico (tier-fast/coder/context/premium) vía LiteLLM.

    LiteLLM ya resuelve el round-robin entre keys y los fallbacks en
    cascada configurados en deploy/litellm.config.yaml; este cliente solo
    necesita apuntar al model_name correcto.
    """

    def __init__(self, config: Config, timeout: float = 120.0) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.gateway_url,
            headers={"Authorization": f"Bearer {config.gateway_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        tier: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> dict:
        payload: dict = {
            "model": tier,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        resp = await self._client.post("/v1/chat/completions", json=payload)
        if resp.status_code >= 400:
            raise GatewayError(f"{tier} -> HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def liveliness(self) -> bool:
        try:
            resp = await self._client.get("/health/liveliness", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
