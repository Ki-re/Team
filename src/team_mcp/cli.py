"""CLI de diagnóstico: `python -m team_mcp.cli probe|run`.

No es la vía de producción (esa es server.py vía stdio/MCP) — existe para
poder verificar cada pieza del sistema de forma aislada durante el
desarrollo, tal como describe la sección "Verificación end-to-end" del plan.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from team_mcp.config import load_config, load_env
from team_mcp.engine.ledger import Ledger
from team_mcp.providers.router import Router


async def _probe_agy(router: Router) -> None:
    result = await router.premium.probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def _probe_gateway(router: Router) -> None:
    alive = await router.gateway.liveliness()
    print(json.dumps({"gateway_alive": alive}, ensure_ascii=False))


async def _run_workflow(router: Router, ledger: Ledger, config, tool: str, kwargs: dict) -> None:
    from team_mcp.workflows import ask, epic, feature, task, validate

    # cada workflow.run tiene una firma distinta (kwargs distintos) — Any es
    # deliberado aquí, es un despachador dinámico por diseño, no un olvido.
    handlers: dict[str, Callable[..., Any]] = {
        "team_task": task.run,
        "team_feature": feature.run,
        "team_epic": epic.run,
        "team_ask": ask.run,
        "team_validate": validate.run,
    }
    handler = handlers.get(tool)
    if handler is None:
        print(f"tool desconocida: {tool}. Opciones: {list(handlers)}", file=sys.stderr)
        raise SystemExit(2)

    manifest = await handler(router, ledger, config, **kwargs)
    print(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2))


def main() -> None:
    # los manifiestos pueden contener texto generado por modelos con
    # cualquier carácter Unicode (visto crashear con "≈" contra la consola
    # cp1252 de Windows) — la consola por defecto no lo soporta, forzamos
    # utf-8 con fallback seguro en vez de confiar en la codificación del SO.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(prog="team_mcp.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="comprobar disponibilidad de un provider")
    p_probe.add_argument("--provider", choices=["agy", "gateway"], required=True)

    p_run = sub.add_parser("run", help="ejecutar un workflow directamente")
    p_run.add_argument("tool", choices=["team_task", "team_feature", "team_epic", "team_ask", "team_validate"])
    p_run.add_argument("--spec")
    p_run.add_argument("--instruction")
    p_run.add_argument("--target-path")
    p_run.add_argument("--target-paths", nargs="*", default=None)
    p_run.add_argument("--kind", choices=["new", "refactor", "fix", "review"])
    p_run.add_argument("--repro-command")
    p_run.add_argument("--scope")
    p_run.add_argument("--scope-paths", nargs="*", default=None)
    p_run.add_argument("--question")
    p_run.add_argument("--spec-file")
    p_run.add_argument("--allow-web-search", action="store_true")
    p_run.add_argument("--plan-file", help="JSON con la lista de nodos para team_epic")
    p_run.add_argument("--budget", type=int, default=None)
    p_run.add_argument("--selftest", action="store_true")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--update-docs", action="store_true")
    p_run.add_argument("--kb-path")

    args = parser.parse_args()

    load_env()

    if args.cmd == "run" and args.dry_run:
        import os
        os.environ["TEAM_DRY_RUN"] = "true"

    config = load_config()
    ledger = Ledger(config)

    spec_original = None
    if args.cmd == "run" and args.tool == "team_validate" and args.spec_file:
        with open(args.spec_file, encoding="utf-8") as f:
            spec_original = f.read()

    epic_plan: list[dict] = []
    if args.cmd == "run" and args.tool == "team_epic" and args.plan_file:
        with open(args.plan_file, encoding="utf-8") as f:
            epic_plan = json.load(f)

    async def _main() -> None:
        router = Router(config, ledger)
        try:
            if args.cmd == "probe":
                if args.provider == "agy":
                    await _probe_agy(router)
                else:
                    await _probe_gateway(router)
                return

            kwargs: dict = {}
            if args.tool == "team_task":
                kwargs = {"instruction": args.instruction, "target_path": args.target_path}
            elif args.tool == "team_feature":
                kwargs = {
                    "spec": args.spec,
                    "target_paths": args.target_paths or [],
                    "kind": args.kind,
                    "repro_command": args.repro_command,
                    "update_docs": args.update_docs,
                    "kb_path": args.kb_path,
                }
            elif args.tool == "team_ask":
                kwargs = {
                    "question": args.question,
                    "scope_paths": args.scope_paths or [],
                    "allow_web_search": args.allow_web_search,
                }
            elif args.tool == "team_validate":
                kwargs = {
                    "scope": args.scope,
                    "spec_original": spec_original,
                    "selftest": args.selftest,
                }
            elif args.tool == "team_epic":
                kwargs = {
                    "plan": epic_plan, "budget": args.budget,
                    "update_docs": args.update_docs, "kb_path": args.kb_path,
                }

            await _run_workflow(router, ledger, config, args.tool, kwargs)
        finally:
            await router.aclose()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
