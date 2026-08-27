"""team_epic: orquesta team_feature sobre un DAG (Fase 8 del plan).

Cada nodo del plan es `{id, spec, target_paths, kind?, repro_command?,
depends_on?}` y se ejecuta vía `feature.run()` tal cual, sin tocarlo. Orden
topológico por oleadas (Kahn): los nodos con todas sus dependencias
resueltas corren en paralelo. Un nodo cuya dependencia falló se marca
`skipped`, no se ejecuta ni se cuenta como fallo propio.

Presupuesto real: delta de `ledger.spent_tokens("team_feature")` antes/
después de cada oleada, acumulado contra `budget`. Deliberadamente no se
usa `Ledger.check_budget` con un id compartido — la tabla `spend` es
global y persistente, y otra llamada a team_feature fuera de este epic
contaminaría el conteo si se usara una clave fija.
"""

from __future__ import annotations

import asyncio

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.sandbox import Sandbox
from team_mcp.engine.schemas import Manifest
from team_mcp.providers.router import Router
from team_mcp.workflows import docs_sync, feature

_WORKFLOW = "team_epic"


def _validate_plan(plan: list[dict]) -> str | None:
    ids = {n.get("id") for n in plan}
    if len(ids) != len(plan):
        return "hay ids duplicados en el plan"
    for n in plan:
        if "id" not in n or "spec" not in n:
            return f"nodo sin 'id' o 'spec': {n}"
        for dep in n.get("depends_on", []):
            if dep not in ids:
                return f"nodo '{n['id']}' depende de '{dep}', que no existe en el plan"
    return None


async def _run_node(router: Router, ledger: Ledger, config: Config, node: dict) -> Manifest:
    return await feature.run(
        router, ledger, config,
        spec=node["spec"],
        target_paths=node.get("target_paths", []),
        kind=node.get("kind"),
        repro_command=node.get("repro_command"),
    )


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    plan: list[dict],
    budget: int | None = None,
    update_docs: bool = False,
    kb_path: str | None = None,
) -> Manifest:
    if not plan:
        return Manifest(
            tool=_WORKFLOW, tests_status="not_run",
            summary="plan vacío, nada que ejecutar", dry_run=config.dry_run,
        )

    error = _validate_plan(plan)
    if error:
        return Manifest(
            tool=_WORKFLOW, tests_status="not_run",
            summary=f"plan inválido: {error}", dry_run=config.dry_run,
        )

    # ojo: "budget or default" trataría budget=0 como "no especificado" (0
    # es falsy) y caería al default, justo lo contrario de lo que pide un
    # budget=0 explícito (agotar todo de inmediato) — visto fallar en pruebas.
    budget = config.token_budget_default if budget is None else budget
    nodes = {n["id"]: n for n in plan}
    done: set[str] = set()
    failed: set[str] = set()
    remaining: set[str] = set(nodes)
    results: dict[str, dict] = {}
    spent_total = 0
    budget_exhausted = False

    while remaining:
        ready = [nid for nid in remaining if all(d in done for d in nodes[nid].get("depends_on", []))]
        if not ready:
            break  # ciclo: nada puede avanzar, se reporta abajo

        blocked = [nid for nid in ready if any(d in failed for d in nodes[nid].get("depends_on", []))]
        for nid in blocked:
            results[nid] = {"skipped": True, "reason": "dependencia fallida"}
            done.add(nid)
            remaining.discard(nid)

        runnable = [nid for nid in ready if nid not in blocked]
        if not runnable:
            continue

        if budget_exhausted or spent_total >= budget:
            budget_exhausted = True
            for nid in runnable:
                results[nid] = {"skipped": True, "reason": "presupuesto agotado"}
                done.add(nid)
                remaining.discard(nid)
            continue

        before = ledger.spent_tokens("team_feature")
        node_manifests = await asyncio.gather(*[
            _run_node(router, ledger, config, nodes[nid]) for nid in runnable
        ])
        after = ledger.spent_tokens("team_feature")
        spent_total += max(after - before, 0)

        for nid, m in zip(runnable, node_manifests):
            results[nid] = m.model_dump()
            done.add(nid)
            remaining.discard(nid)
            if m.tests_status == "red":
                failed.add(nid)

    for nid in remaining:
        results[nid] = {"skipped": True, "reason": "ciclo detectado en depends_on"}

    files_changed = [
        f for r in results.values() if isinstance(r, dict) for f in r.get("files_changed", []) or []
    ]
    completed = [nid for nid, r in results.items() if not r.get("skipped")]
    skipped = [nid for nid, r in results.items() if r.get("skipped")]

    # una sola llamada de sincronización de docs para todo el epic (Fase 12
    # del plan), no una por nodo — evita N llamadas redundantes al modelo
    # cuando varios nodos tocan el mismo tema de documentación.
    docs_note = ""
    if update_docs and kb_path and completed:
        change_summary = "\n".join(
            f"- {nid}: {nodes[nid]['spec']}" for nid in completed
        )
        result = await docs_sync.run(
            router, Sandbox(config), kb_path=kb_path,
            changed_files=files_changed, change_summary=f"team_epic:\n{change_summary}",
        )
        files_changed = files_changed + result["applied"]
        docs_note = result["note"]

    return Manifest(
        tool=_WORKFLOW,
        files_changed=files_changed,
        tests_status="red" if failed else ("green" if completed else "not_run"),
        tokens_used={"team_feature": spent_total},
        summary=(
            f"{len(completed)}/{len(nodes)} nodos completados, {len(failed)} fallidos, "
            f"{len(skipped)} omitidos. Gasto~{spent_total} tokens (presupuesto {budget})."
            + (" PRESUPUESTO AGOTADO." if budget_exhausted else "")
            + (f"\n\ndocs: {docs_note}" if docs_note else "")
        ),
        dry_run=config.dry_run,
    )
