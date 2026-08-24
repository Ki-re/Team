"""Punto único de acceso a los 4 tiers para el resto del código del MCP.

Los pipelines no hablan con GatewayProvider/PremiumProvider directamente:
piden `router.fast(...)`, `router.coder(...)`, etc. Esto mantiene el ledger
y el logging de proveedor centralizados en un solo lugar.
"""

from __future__ import annotations

import time

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger, SpendEvent
from team_mcp.providers.agy import PremiumProvider
from team_mcp.providers.gateway import GatewayProvider


class Router:
    def __init__(self, config: Config, ledger: Ledger) -> None:
        self._config = config
        self._ledger = ledger
        self.gateway = GatewayProvider(config)
        self.premium = PremiumProvider(config, self.gateway)

    async def aclose(self) -> None:
        await self.gateway.aclose()

    async def _call(
        self, workflow: str, tier: str, messages: list[dict],
        *, temperature: float, max_tokens: int | None = None,
    ) -> str:
        t0 = time.monotonic()
        try:
            resp = await self.gateway.chat(
                tier, messages, temperature=temperature, max_tokens=max_tokens
            )
        except Exception:
            self._ledger.record(SpendEvent(
                workflow=workflow, tier=tier, model=tier,
                tokens_in=0, tokens_out=0,
                latency_ms=(time.monotonic() - t0) * 1000, ok=False,
            ))
            raise

        usage = resp.get("usage", {})
        self._ledger.record(SpendEvent(
            workflow=workflow, tier=tier, model=resp.get("model", tier),
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=(time.monotonic() - t0) * 1000, ok=True,
        ))
        return resp["choices"][0]["message"]["content"]

    async def fast(self, workflow: str, prompt: str, *, temperature: float = 0.3) -> str:
        return await self._call(
            workflow, self._config.tier_fast, [{"role": "user", "content": prompt}],
            temperature=temperature,
        )

    async def coder(
        self, workflow: str, prompt: str, *, temperature: float = 0.7,
    ) -> str:
        return await self._call(
            workflow, self._config.tier_coder, [{"role": "user", "content": prompt}],
            temperature=temperature,
        )

    async def context(self, workflow: str, prompt: str, *, temperature: float = 0.2) -> str:
        return await self._call(
            workflow, self._config.tier_context, [{"role": "user", "content": prompt}],
            temperature=temperature,
        )

    async def premium_review(self, workflow: str, prompt: str) -> str:
        t0 = time.monotonic()
        result = await self.premium.complete(prompt)
        self._ledger.record(SpendEvent(
            workflow=workflow, tier=self._config.tier_premium,
            model=f"agy:{self.premium.last_used}",
            tokens_in=0, tokens_out=0,
            latency_ms=(time.monotonic() - t0) * 1000, ok=True,
        ))
        return result
