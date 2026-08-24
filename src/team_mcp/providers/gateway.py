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

    async def respond_with_tools(
        self,
        tier: str,
        input_text: str,
        *,
        tools: list[dict],
        temperature: float = 0.2,
    ) -> str:
        """Llama a /v1/responses (Responses API), no /v1/chat/completions —
        formato distinto, necesario para tool-use vía el MCP Gateway de
        LiteLLM (Fase 7 del plan). Úsalo solo con tiers/modelos que de
        verdad soporten tool-calling (hoy: tier-context); no se ha probado
        con tier-fast/tier-coder.

        El parseo de la respuesta es best-effort sobre el formato Responses
        API estándar (`output: [{type: "message", content: [{type:
        "output_text", text: ...}]}]`) — revisar si LiteLLM lo expone
        distinto al verificar en vivo.
        """
        payload = {
            "model": tier,
            "input": input_text,
            "tools": tools,
            "temperature": temperature,
        }
        resp = await self._client.post("/v1/responses", json=payload)
        if resp.status_code >= 400:
            raise GatewayError(f"{tier} (responses) -> HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()

        texts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"):
                    # algunos backends del pool devuelven "text": null en
                    # vez de omitir la clave (visto en vivo el 2026-08-24
                    # contra tier-context) — .get(..., "") no basta porque
                    # la clave SÍ existe, solo que vale None.
                    texts.append(part.get("text") or "")
        joined = "\n".join(texts).strip()
        if not joined:
            raise GatewayError(f"{tier} (responses): sin output_text parseable en {str(data)[:500]}")
        return joined

    async def liveliness(self) -> bool:
        try:
            resp = await self._client.get("/health/liveliness", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
