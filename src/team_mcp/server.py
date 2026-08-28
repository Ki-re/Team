"""MCP "Team" server: 5 entrypoints graded by complexity for the orchestrator.

Hard rule of the project: intermediate results are never returned to the
orchestrating agent. Every tool is a complete pipeline that ends in a
compact Manifest. The internal pipelines (quick, digest, implement,
refactor, fix, investigate, review, epic, validate, selftest) live in
workflows/ and aren't exposed directly — see the "Surface exposed to the
orchestrator" section of the plan.

API note: the installed `mcp` SDK (2.0.0) exposes `mcp.server.mcpserver.
MCPServer`, the successor to FastMCP — not the 1.x decorator API
(`@Server().list_tools()/call_tool()`). The types of each function
decorated with `@mcp.tool()` generate the JSON Schema automatically.
"""

from __future__ import annotations

import logging
from typing import Literal

from mcp.server.mcpserver import MCPServer

from team_mcp.config import load_config, load_env
from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router
from team_mcp.workflows import ask, epic, feature, task, validate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("team_mcp")

load_env()
_config = load_config()
_ledger = Ledger(_config)
_router = Router(_config, _ledger)

mcp = MCPServer(name="team")


@mcp.tool(
    description=(
        "Small, unambiguous change to 1 file: formatting, regex, "
        "rename, docstring, trivial fix. No premium tier. If the "
        "deterministic gate fails twice, escalates on its own to team_feature."
    )
)
async def team_task(instruction: str, target_path: str) -> dict:
    manifest = await task.run(_router, _ledger, _config, instruction=instruction, target_path=target_path)
    return manifest.model_dump()


@mcp.tool(
    description=(
        "A real unit of work: implement (kind=new, default), "
        "refactor (kind=refactor, preserves behavior via characterization "
        "tests), fix a bug (kind=fix, requires "
        "repro_command: a command that currently fails and must end up "
        "exiting with code 0), or review code without generating it "
        "(kind=review). Fan-out of N workers + cross-validation consensus "
        "+ premium critique + bounded repair. If the project has a "
        "markdown knowledge-base (a folder with INDEX.md + one file per "
        "topic with frontmatter, same convention as Claude's own memory — "
        "see docs/KB_CONVENTION.md), pass update_docs=True and kb_path to "
        "that folder so that, after a successful change, the EXISTING KB "
        "files that went stale get updated (it never creates new "
        "entries). If the KB lives in a dedicated repo instead of a "
        "folder of this project, its local path must be in "
        "TEAM_SANDBOX_ROOTS or the write will be rejected by the sandbox."
    )
)
async def team_feature(
    spec: str,
    target_paths: list[str],
    kind: Literal["new", "refactor", "fix", "review"] | None = None,
    repro_command: str | None = None,
    update_docs: bool = False,
    kb_path: str | None = None,
) -> dict:
    manifest = await feature.run(
        _router, _ledger, _config,
        spec=spec, target_paths=target_paths, kind=kind, repro_command=repro_command,
        update_docs=update_docs, kb_path=kb_path,
    )
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Multi-task plan with dependencies (DAG). Orchestrates team_feature "
        "over each node in topological order, parallelizing independent "
        "branches, with a global token budget. update_docs/kb_path: same "
        "as in team_feature, but ONE documentation sync at the end of the "
        "whole epic (not one per node)."
    )
)
async def team_epic(
    plan: list[dict], budget: int | None = None,
    update_docs: bool = False, kb_path: str | None = None,
) -> dict:
    manifest = await epic.run(
        _router, _ledger, _config, plan=plan, budget=budget,
        update_docs=update_docs, kb_path=kb_path,
    )
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Question about code or logs, without writing anything. "
        "Map-reduce on tier-context with citation verification "
        "(path:line). scope_paths accepts files and directories. If the "
        "question needs current external context (a library version, an "
        "external API's docs, something not in scope_paths), pass "
        "allow_web_search=True so it uses real web search, not the "
        "model's memory."
    )
)
async def team_ask(question: str, scope_paths: list[str], allow_web_search: bool = False) -> dict:
    manifest = await ask.run(
        _router, _ledger, _config,
        question=question, scope_paths=scope_paths, allow_web_search=allow_web_search,
    )
    return manifest.model_dump()


@mcp.tool(
    description=(
        "Closure: GO/NO-GO verdict on the real state of the workspace "
        "(build, tests, lint, secrets, requirement traceability)."
    )
)
async def team_validate(scope: str, spec_original: str | None = None, selftest: bool = False) -> dict:
    manifest = await validate.run(
        _router, _ledger, _config, scope=scope, spec_original=spec_original, selftest=selftest,
    )
    return manifest.model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
