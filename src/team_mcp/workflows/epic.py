"""team_epic: orchestrates team_feature over a DAG (plan Phase 8).

Each node of the plan is `{id, spec, target_paths, kind?, repro_command?,
depends_on?}` and runs via `feature.run()` as-is, untouched. Topological
order by waves (Kahn): nodes with all their dependencies resolved run in
parallel. A node whose dependency failed gets marked `skipped`, not run
and not counted as its own failure.

Real budget: delta of `ledger.spent_tokens("team_feature")` before/after
each wave, accumulated against `budget`. Deliberately not using
`Ledger.check_budget` with a shared id — the `spend` table is global and
persistent, and another call to team_feature outside this epic would
contaminate the count if a fixed key were used.
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
        return "there are duplicate ids in the plan"
    for n in plan:
        if "id" not in n or "spec" not in n:
            return f"node missing 'id' or 'spec': {n}"
        for dep in n.get("depends_on", []):
            if dep not in ids:
                return f"node '{n['id']}' depends on '{dep}', which doesn't exist in the plan"
    return None


async def _run_node(router: Router, ledger: Ledger, config: Config, node: dict) -> Manifest:
    try:
        return await feature.run(
            router, ledger, config,
            spec=node["spec"],
            target_paths=node.get("target_paths", []),
            kind=node.get("kind"),
            repro_command=node.get("repro_command"),
        )
    except Exception as exc:  # noqa: BLE001 — one node's uncaught failure must not cancel its sibling nodes (asyncio.gather has no return_exceptions here) or crash the whole epic; defense-in-depth alongside the specific feature.py/repair.py fixes for this same bug class
        return Manifest(
            tool="team_feature", tests_status="red",
            summary=f"node '{node.get('id', '?')}' crashed: {type(exc).__name__}: {exc}"[:500],
            dry_run=config.dry_run,
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
            summary="empty plan, nothing to run", dry_run=config.dry_run,
        )

    error = _validate_plan(plan)
    if error:
        return Manifest(
            tool=_WORKFLOW, tests_status="not_run",
            summary=f"invalid plan: {error}", dry_run=config.dry_run,
        )

    # careful: "budget or default" would treat budget=0 as "not
    # specified" (0 is falsy) and fall back to the default, exactly the
    # opposite of what an explicit budget=0 asks for (exhaust immediately)
    # — seen failing in testing.
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
            break  # cycle: nothing can advance, reported below

        blocked = [nid for nid in ready if any(d in failed for d in nodes[nid].get("depends_on", []))]
        for nid in blocked:
            results[nid] = {"skipped": True, "reason": "failed dependency"}
            done.add(nid)
            remaining.discard(nid)

        runnable = [nid for nid in ready if nid not in blocked]
        if not runnable:
            continue

        if budget_exhausted or spent_total >= budget:
            budget_exhausted = True
            for nid in runnable:
                results[nid] = {"skipped": True, "reason": "budget exhausted"}
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
        results[nid] = {"skipped": True, "reason": "cycle detected in depends_on"}

    files_changed = [
        f for r in results.values() if isinstance(r, dict) for f in r.get("files_changed", []) or []
    ]
    completed = [nid for nid, r in results.items() if not r.get("skipped")]
    skipped = [nid for nid, r in results.items() if r.get("skipped")]

    # a single docs-sync call for the whole epic (plan Phase 12), not one
    # per node — avoids N redundant model calls when several nodes touch
    # the same documentation topic.
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
            f"{len(completed)}/{len(nodes)} nodes completed, {len(failed)} failed, "
            f"{len(skipped)} skipped. Spent~{spent_total} tokens (budget {budget})."
            + (" BUDGET EXHAUSTED." if budget_exhausted else "")
            + (f"\n\ndocs: {docs_note}" if docs_note else "")
        ),
        dry_run=config.dry_run,
    )
