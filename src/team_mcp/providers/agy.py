"""Premium-tier adapter via a subscription coding-agent CLI ("agy" by
default), with an API fallback.

Why a CLI at all, instead of just another API key in the gateway: a paid
coding-agent subscription (a flat monthly plan, not metered per-token
billing) usually has far more real usage headroom than a free-tier API
key sharing quota across the whole farm. Routing the premium tier through
whichever subscription CLI you already pay for lets you spend THAT
capacity on the hardest tasks (final critique, last-resort repair) instead
of burning shared free-tier quota on them.

"agy" (Antigravity CLI) is the reference implementation, not a hard
requirement — this module is a thin, swappable subprocess wrapper. To use
a different subscription CLI (Codex CLI, Claude Code's own `claude -p`,
Hermes, or anything else that takes a prompt on argv and prints a plain
text answer), you don't need to fork this file at all in most cases: set
TEAM_AGY_PATH to that CLI's binary and TEAM_AGY_CLI_ARGS to its actual
flag layout (see _run_agy's docstring below for the exact template
syntax). Only fork this module if your tool needs something this
"binary + argv template, capture stdout" shape can't express (e.g. stdin
piping, a login handshake, JSON output that needs parsing instead of
plain text).

Binary resolution order:
  1. TEAM_AGY_PATH (env) — explicit absolute path, recommended while a
     freshly installed CLI doesn't yet resolve reliably via PATH in new
     processes.
  2. shutil.which("agy")
  3. Known install paths for the Antigravity CLI specifically (only
     relevant if you're actually using agy — irrelevant, and harmless,
     for any other CLI, since TEAM_AGY_PATH will already have resolved it
     in step 1 first).

If none of this resolves, or the subprocess fails/times out, it degrades
to the gateway's `tier-premium` model group and notes why in the ledger.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from team_mcp.config import Config
from team_mcp.providers.gateway import GatewayProvider

_KNOWN_PATHS = [
    Path.home() / "AppData/Local/agy/bin/agy.exe",
    Path.home() / "AppData/Local/Programs/Antigravity/bin/agy.cmd",
    Path.home() / "AppData/Local/Programs/Antigravity/bin/agy.exe",
    Path.home() / ".antigravity/bin/agy.cmd",
]

# Default template = agy's real invocation, confirmed with `agy --help`
# (agy 1.1.19): `agy --print "<prompt>" --output-format text`, non-
# interactive mode, doesn't use stdin, has no `run` subcommand. The
# literal token "{prompt}" gets replaced with the prompt as ONE single
# argv element (never interpolated into a shell string, so a prompt with
# quotes/spaces/newlines needs no special escaping) — see _build_argv.
_DEFAULT_CLI_ARGS = "--print|{prompt}|--output-format|text"


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
    """Single interface for the premium tier: the configured CLI first
    (agy by default), the gateway as fallback."""

    def __init__(self, config: Config, gateway: GatewayProvider) -> None:
        self._config = config
        self._gateway = gateway
        self._agy_path = resolve_agy_path(config)
        self.last_used: str | None = None  # "agy" | "fallback" — for the ledger
        self.last_error: str | None = None  # fallback reason, if any — for the ledger

    @property
    def agy_available(self) -> bool:
        return self._agy_path is not None

    async def probe(self) -> dict:
        """Quick availability check, without spending the orchestrator's own context."""
        if not self._agy_path:
            return {"agy": False, "reason": "binary not resolved (see TEAM_AGY_PATH)"}
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
                # degrades to fallback; the reason isn't lost anymore — it
                # used to just show up as last_used == "fallback" with no
                # indication of WHY, forcing a code read to diagnose live.
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]

        self.last_used = "fallback"
        resp = await self._gateway.chat(
            self._config.tier_premium,
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return resp["choices"][0]["message"]["content"]

    def _build_argv(self, prompt: str) -> list[str]:
        """Builds the argv from TEAM_AGY_CLI_ARGS (or agy's default if
        unset). Format: tokens separated by "|", where the literal token
        "{prompt}" gets replaced with the prompt as ONE SINGLE argv
        element — never interpolated into a string and handed to a shell,
        so a prompt with spaces/quotes/newlines needs no special escaping.

        Examples for other CLIs (edit TEAM_AGY_CLI_ARGS, not this code):
          Codex CLI:   "exec|{prompt}"
          claude -p:   "-p|{prompt}|--output-format|text"
        Also point TEAM_AGY_PATH at that CLI's actual binary."""
        assert self._agy_path is not None
        template = self._config.agy_cli_args or _DEFAULT_CLI_ARGS
        args = [prompt if tok == "{prompt}" else tok for tok in template.split("|")]
        if self._config.agy_model:
            args += ["--model", self._config.agy_model]
        return [self._agy_path, *args]

    async def _run_agy(self, prompt: str, *, timeout: float) -> str:
        args = self._build_argv(prompt)
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
