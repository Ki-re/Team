"""Single access point to the 4 tiers for the rest of the MCP's code.

The pipelines don't talk to GatewayProvider/PremiumProvider directly:
they ask for `router.fast(...)`, `router.coder(...)`, etc. This keeps the
ledger and provider logging centralized in one place.
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
        except Exception as exc:
            # `note` used to always stay empty: a timeout, a 429, and
            # broken JSON were indistinguishable in the ledger, forcing a
            # code read to diagnose instead of using the telemetry itself.
            self._ledger.record(SpendEvent(
                workflow=workflow, tier=tier, model=tier,
                tokens_in=0, tokens_out=0,
                latency_ms=(time.monotonic() - t0) * 1000, ok=False,
                note=f"{type(exc).__name__}: {exc}"[:500],
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

    async def context_with_tools(self, workflow: str, prompt: str) -> str:
        """Like `context()`, but with access to the MCP tools registered
        on the gateway (today: only Tavily — search/extract/map/crawl —,
        see plan Phase 7). tier-context only: it's where tool-calling is
        reliable with the currently available free models."""
        t0 = time.monotonic()
        # require_approval: "never" is required — without it LiteLLM
        # returns the function_call pending human approval instead of
        # executing it, and the response never reaches final text
        # (confirmed live on 2026-08-24: with the flag, LiteLLM actually
        # executes the tool, see tool_execution_results in the response).
        tools = [{
            "type": "mcp", "server_url": "litellm_proxy",
            "server_label": "tavily", "require_approval": "never",
        }]
        try:
            result = await self.gateway.respond_with_tools(
                self._config.tier_context, prompt, tools=tools,
            )
        except Exception as exc:
            self._ledger.record(SpendEvent(
                workflow=workflow, tier=self._config.tier_context, model="responses+tools",
                tokens_in=0, tokens_out=0,
                latency_ms=(time.monotonic() - t0) * 1000, ok=False,
                note=f"{type(exc).__name__}: {exc}"[:500],
            ))
            raise

        self._ledger.record(SpendEvent(
            workflow=workflow, tier=self._config.tier_context, model="responses+tools",
            tokens_in=0, tokens_out=0,
            latency_ms=(time.monotonic() - t0) * 1000, ok=True,
        ))
        return result

    async def premium_review(self, workflow: str, prompt: str) -> str:
        t0 = time.monotonic()
        result = await self.premium.complete(prompt)
        # real numbers when available (agy's --output-format json, or the
        # gateway fallback, which always has them) — 0,0 only when truly
        # unknown (a plain-text CLI swapped in via TEAM_AGY_CLI_ARGS),
        # never a fabricated placeholder passed off as a real reading.
        usage = self.premium.last_usage or {}
        self._ledger.record(SpendEvent(
            workflow=workflow, tier=self._config.tier_premium,
            model=f"agy:{self.premium.last_used}",
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
            latency_ms=(time.monotonic() - t0) * 1000, ok=True,
            # if agy failed and degraded to the fallback, the reason ends
            # up here instead of getting lost — before, `last_used="fallback"`
            # didn't say why.
            note=self.premium.last_error or "",
        ))
        return result
