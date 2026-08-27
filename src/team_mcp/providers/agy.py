"""Adaptador del tier premium vía Antigravity CLI (`agy`), con fallback API.

Resolución del binario, en orden:
  1. TEAM_AGY_PATH (env) — ruta absoluta explícita, recomendada mientras el
     CLI esté recién instalado y no resuelva de forma fiable en el PATH de
     procesos nuevos.
  2. shutil.which("agy")
  3. Rutas conocidas de la instalación de Antigravity.

Si nada de esto funciona, o el subprocess falla/timeout, se degrada al
model group `tier-premium` del gateway (Gemini Pro) y se anota en el ledger.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from team_mcp.config import Config
from team_mcp.providers.gateway import GatewayProvider

_KNOWN_PATHS = [
    Path.home() / "AppData/Local/agy/bin/agy.exe",  # instalación confirmada en este equipo
    Path.home() / "AppData/Local/Programs/Antigravity/bin/agy.cmd",
    Path.home() / "AppData/Local/Programs/Antigravity/bin/agy.exe",
    Path.home() / ".antigravity/bin/agy.cmd",
]


class AgyUnavailable(RuntimeError):
    pass


def resolve_agy_path(config: Config) -> str | None:
    if config.agy_path and Path(config.agy_path).exists():
        return config.agy_path

    which = shutil.which("agy")
    if which:
        return which

    for candidate in _KNOWN_PATHS:
        if candidate.exists():
            return str(candidate)

    return None


class PremiumProvider:
    """Interfaz única para el tier premium: agy primero, gateway como fallback."""

    def __init__(self, config: Config, gateway: GatewayProvider) -> None:
        self._config = config
        self._gateway = gateway
        self._agy_path = resolve_agy_path(config)
        self.last_used: str | None = None  # "agy" | "fallback" — para el ledger
        self.last_error: str | None = None  # motivo del fallback, si lo hubo — para el ledger

    @property
    def agy_available(self) -> bool:
        return self._agy_path is not None

    async def probe(self) -> dict:
        """Comprobación rápida de disponibilidad, sin gastar contexto de Claude."""
        if not self._agy_path:
            return {"agy": False, "reason": "binario no resuelto (ver TEAM_AGY_PATH)"}
        try:
            proc = await asyncio.create_subprocess_exec(
                self._agy_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            ok = proc.returncode == 0
            return {
                "agy": ok,
                "path": self._agy_path,
                "output": (stdout or stderr).decode(errors="replace").strip()[:200],
            }
        except (TimeoutError, OSError) as exc:
            return {"agy": False, "path": self._agy_path, "reason": str(exc)}

    async def complete(self, prompt: str, *, timeout: float = 180.0) -> str:
        self.last_error = None
        if self._agy_path:
            try:
                return await self._run_agy(prompt, timeout=timeout)
            except (TimeoutError, OSError, RuntimeError) as exc:
                # degrada a fallback; el motivo ya no se pierde — antes solo
                # quedaba anotado en last_used ("fallback"), sin decir POR
                # QUÉ, obligando a leer código para diagnosticar en vivo.
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]

        self.last_used = "fallback"
        resp = await self._gateway.chat(
            self._config.tier_premium,
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return resp["choices"][0]["message"]["content"]

    async def _run_agy(self, prompt: str, *, timeout: float) -> str:
        # `agy --print "<prompt>"` (alias -p) = modo no interactivo, imprime
        # la respuesta y sale. No usa stdin. Confirmado con `agy --help` en
        # este equipo (agy 1.1.19) — la CLI real no tiene un subcomando `run`.
        assert self._agy_path is not None
        args = [self._agy_path, "--print", prompt, "--output-format", "text"]
        if self._config.agy_model:
            args += ["--model", self._config.agy_model]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            raise RuntimeError(f"agy exit={proc.returncode}: {stderr.decode(errors='replace')[:300]}")

        self.last_used = "agy"
        return stdout.decode(errors="replace")
