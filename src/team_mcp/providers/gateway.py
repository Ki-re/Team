"""HTTP client for the LiteLLM gateway (OpenAI /chat/completions format)."""

from __future__ import annotations

import httpx

from team_mcp.config import Config


class GatewayError(RuntimeError):
    pass


class GatewayProvider:
    """Talks to a logical tier (tier-fast/coder/context/premium) via LiteLLM.

    LiteLLM already resolves round-robin between keys and the cascading
    fallbacks configured in deploy/litellm.config.yaml; this client only
    needs to point at the right model_name.
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
        """Calls /v1/responses (Responses API), not /v1/chat/completions —
        a different format, needed for tool-use via LiteLLM's MCP Gateway
        (plan Phase 7). Only use it with tiers/models that genuinely
        support tool-calling (today: tier-context); untested with
        tier-fast/tier-coder.

        Response parsing is best-effort over the standard Responses API
        shape (`output: [{type: "message", content: [{type:
        "output_text", text: ...}]}]`) — recheck if LiteLLM exposes it
        differently when verifying live.
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
                    # some backends in the pool return "text": null
                    # instead of omitting the key (seen live on
                    # 2026-08-24 against tier-context) — .get(..., "")
                    # isn't enough because the key DOES exist, it's just None.
                    texts.append(part.get("text") or "")
        joined = "\n".join(texts).strip()
        if not joined:
            raise GatewayError(f"{tier} (responses): no parseable output_text in {str(data)[:500]}")
        return joined

    async def daily_activity(self, start_date: str, end_date: str) -> dict:
        """`GET /user/daily/activity` — per-day token/spend totals with a
        breakdown by model, for everything that went through the proxy
        (never sees `agy`'s own usage — that's a local subprocess CLI,
        not a proxy call; see `cli.py`'s `usage` report for how the two
        get combined). Free on this OSS deployment, unlike
        `/global/spend/report`, which is Enterprise-only and 402s here."""
        resp = await self._client.get(
            "/user/daily/activity",
            params={"start_date": start_date, "end_date": end_date},
        )
        if resp.status_code >= 400:
            raise GatewayError(f"daily_activity -> HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def liveliness(self) -> bool:
        try:
            resp = await self._client.get("/health/liveliness", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
