"""team_feature: fan-out + consenso + crítica premium + reparación acotada.

Pipeline de `kind="new"` (primitiva #3 del plan — team_implement):
  1. lee el contenido actual de target_paths (contrato de contexto simple;
     el map-reduce completo de tier-context es una mejora de Fase 4).
  2. fan-out N x tier-coder (temp alta, prompts variados) -> candidatos con
     implementación + tests.
  3. gate determinista (verify.py) descarta candidatos que no parsean/lintan.
  4. consenso por validación cruzada (consensus.py) sobre los supervivientes.
  5. crítica adversarial (critic.py, tier-premium) sobre el ganador.
  6. si hay tests rojos o hallazgos bloqueantes: repair_loop acotado.
  7. escritura atómica vía Sandbox + Manifest.

`kind` != "new" (refactor/fix/review) queda para Fase 4: cada uno reordena
estos mismos pasos de forma distinta (ver plan), pero reutiliza exactamente
estas primitivas.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.consensus import ConsensusCandidate, run_consensus
from team_mcp.engine.critic import review as critic_review
from team_mcp.engine.jsonio import extract_json
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.repair import repair_loop
from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import FeatureKind, FileEdit, Manifest, Severity
from team_mcp.providers.router import Router

_WORKFLOW = "team_feature"
_N_WORKERS = 3
_TEST_COMMAND = ["pytest", "-q"]

_IMPLEMENT_PROMPT = """\
Implementa esto:

Spec: {spec}

Archivos objetivo: {paths}
Contenido actual (vacío si son archivos nuevos):
{content}

Escribe también tests con pytest para tu implementación, en un archivo
llamado exactamente `test_solution.py` (mismo nombre para todos los
workers, así se pueden cruzar implementaciones y tests entre candidatos).

Responde ÚNICAMENTE con JSON:
{{"edits": [{{"path": "...", "search": "<vacío si es archivo nuevo>", "replace": "..."}}],
  "test_edits": [{{"path": "test_solution.py", "search": "", "replace": "..."}}],
  "rationale": "<1 frase>"}}
"""


async def _read_base_files(target_paths: list[str]) -> dict[str, str]:
    base: dict[str, str] = {}
    for raw in target_paths:
        p = Path(raw)
        base[p.name] = p.read_text(encoding="utf-8") if p.exists() else ""
    return base


async def _generate_candidate(
    router: Router, worker_id: str, spec: str, target_paths: list[str], base_files: dict[str, str],
) -> ConsensusCandidate | None:
    content = "\n\n".join(f"--- {name} ---\n{c}" for name, c in base_files.items())
    prompt = _IMPLEMENT_PROMPT.format(spec=spec, paths=", ".join(target_paths), content=content)
    target_dir = Path(target_paths[0]).parent if target_paths else Path()
    try:
        raw = await router.coder(_WORKFLOW, prompt, temperature=0.8)
        data = extract_json(raw)
        edits = [FileEdit(**e) for e in data["edits"]]
        test_edits = [FileEdit(**e) for e in data.get("test_edits", [])]
        # el prompt exige el mismo nombre de archivo de test para todos los
        # workers (necesario para cruzar impl_i x tests_j en consensus.py);
        # el directorio se fuerza aquí en vez de confiar en que el modelo lo
        # acierte, porque no siempre lo hace (visto en pruebas: devolvía
        # "test_solution.py" a secas, fuera de la whitelist del sandbox real).
        test_edits = [
            e.model_copy(update={"path": str(target_dir / Path(e.path).name)})
            for e in test_edits
        ]
    except Exception:  # noqa: BLE001 — un worker caído no debe tumbar el fan-out
        return None
    return ConsensusCandidate(id=worker_id, model="tier-coder", edits=edits, test_edits=test_edits)


async def _run_new(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
) -> Manifest:
    base_files = await _read_base_files(target_paths)

    results = await asyncio.gather(*[
        _generate_candidate(router, f"w{i}", spec, target_paths, base_files)
        for i in range(1, _N_WORKERS + 1)
    ])
    candidates = [c for c in results if c is not None]

    if not candidates:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.new, tests_status="red",
            summary="ningún worker de tier-coder produjo un candidato válido (JSON roto o error de API)",
            dry_run=config.dry_run,
        )

    sandbox = Sandbox(config)
    consensus = await run_consensus(
        _WORKFLOW, sandbox, base_files, candidates, test_command=_TEST_COMMAND,
    )

    if consensus.winner_id is None:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.new, tests_status="red",
            summary=(
                f"consenso sin ganador claro entre {len(candidates)} candidatos "
                f"(scores={consensus.scores}). Requiere síntesis manual o team_epic."
            ),
            dry_run=config.dry_run,
        )

    winner = next(c for c in candidates if c.id == consensus.winner_id)
    edits = winner.edits + winner.test_edits

    critic_report = await critic_review(router, _WORKFLOW, spec, winner.edits)
    blocking = critic_report.blocking(Severity.high)

    tests_ok = True
    if consensus.matrix:
        self_cell = next(
            (c for c in consensus.matrix if c.impl_id == winner.id and c.tests_id == winner.id), None
        )
        tests_ok = self_cell is not None and self_cell.total > 0 and self_cell.passed == self_cell.total
    provider_used = {"tier_premium": "agy" if router.premium.last_used != "fallback" else "fallback"}

    if not tests_ok or blocking:
        error_summary = "; ".join(f"[{f.severity}] {f.claim}: {f.failure_scenario}" for f in blocking)
        if not tests_ok:
            error_summary = f"tests propios en rojo. {error_summary}".strip()

        outcome = await repair_loop(
            router, _WORKFLOW, sandbox, base_files, spec, edits, error_summary,
            test_command=_TEST_COMMAND,
        )
        if not outcome.success:
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.new, tests_status="red",
                critic_findings_open=len(blocking),
                provider_used=provider_used,
                summary=(
                    f"tras {len(outcome.iterations)} reparaciones sigue fallando "
                    f"({'estancado' if outcome.stagnated else 'sin converger'}): "
                    f"{outcome.last_error[:300]}"
                ),
                dry_run=config.dry_run,
            )
        edits = outcome.final_edits

    try:
        changed = sandbox.apply_edits(edits)
    except (SandboxViolation, EditConflict) as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.new, tests_status="green",
            summary=f"verificado pero no se pudo escribir en el sandbox: {exc}",
            dry_run=config.dry_run,
        )

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.new,
        files_changed=changed, tests_status="green",
        critic_findings_open=0,
        provider_used=provider_used,
        summary=(
            f"implementado con {len(candidates)} candidatos "
            f"(ganador={winner.id}, score={consensus.scores.get(winner.id, 0):.2f})"
        ),
        dry_run=config.dry_run,
    )


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    spec: str,
    target_paths: list[str],
    kind: str | None = None,
    repro_command: str | None = None,
) -> Manifest:
    resolved_kind = kind or "new"

    if resolved_kind != "new":
        return Manifest(
            tool=_WORKFLOW,
            tests_status="not_run",
            summary=(
                f"team_feature(kind={resolved_kind}) pendiente de Fase 4. "
                "Solo kind=new (o sin especificar) está implementado."
            ),
            dry_run=config.dry_run,
        )

    return await _run_new(router, ledger, config, spec, target_paths)
