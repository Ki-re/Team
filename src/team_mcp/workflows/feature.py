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

Fase 4 añade `kind="refactor"` y `kind="fix"`, reordenando estas mismas
primitivas (ver docstrings de _run_refactor/_run_fix). `kind="review"` no
genera código: vive en engine/critic.py + esta misma función `run`, con
3 pasadas de crítica en paralelo (rúbricas distintas) y deduplicación.
"""

from __future__ import annotations

import asyncio
import shlex
import sys
import tempfile
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.consensus import ConsensusCandidate, run_consensus
from team_mcp.engine.critic import review as critic_review
from team_mcp.engine.jsonio import extract_json
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.repair import repair_loop
from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import CriticFinding, FeatureKind, FileEdit, Manifest, Severity
from team_mcp.engine.verify import VerifyTarget, verify_candidate
from team_mcp.providers.router import Router

_WORKFLOW = "team_feature"
_N_WORKERS = 3
_TEST_COMMAND = [sys.executable, "-m", "pytest", "-q"]  # nunca depender de "pytest" en el PATH
_MAX_CHAR_TEST_ATTEMPTS = 2
_MAX_LOCALIZE_ATTEMPTS = 2

_IMPLEMENT_PROMPT = """\
Implementa esto:

Spec: {spec}

Contenido actual, uno por archivo (vacío si son archivos nuevos):
{content}

Usa EXACTAMENTE los mismos nombres de archivo que ves arriba en el campo
"path" de tus edits — sin carpetas, tal cual aparecen tras "---".

Escribe también tests con pytest para tu implementación, en un archivo
llamado exactamente `test_solution.py` (mismo nombre para todos los
workers, así se pueden cruzar implementaciones y tests entre candidatos).

Responde ÚNICAMENTE con JSON:
{{"edits": [{{"path": "...", "search": "<vacío si es archivo nuevo>", "replace": "..."}}],
  "test_edits": [{{"path": "test_solution.py", "search": "", "replace": "..."}}],
  "rationale": "<1 frase>"}}
"""

_CHARACTERIZE_PROMPT = """\
Escribe tests de pytest que describan el comportamiento ACTUAL de este
código, TAL COMO ESTÁ, sin juzgar si es correcto ni intentar mejorarlo.
Objetivo: capturar qué hace hoy, para poder detectar si un refactor lo
rompe. Cubre las rutas principales que uses/veas ejercitadas por el código.

Archivos:
{content}

Responde ÚNICAMENTE con JSON:
{{"test_edits": [{{"path": "test_characterization.py", "search": "", "replace": "..."}}]}}
"""

_REFACTOR_PROMPT = """\
Refactoriza este código según el objetivo, SIN cambiar su comportamiento
observable (hay tests de caracterización que deben seguir en verde).

Objetivo del refactor: {goal}

Archivos:
{content}

Responde ÚNICAMENTE con JSON:
{{"edits": [{{"path": "...", "search": "...", "replace": "..."}}], "rationale": "<1 frase>"}}
"""

_LOCALIZE_PROMPT = """\
Hay un bug. Descríbelo brevemente en qué archivo/línea es más probable que
esté, con tu justificación. No lo arregles todavía.

Descripción del bug: {bug}

Archivos:
{content}

Responde ÚNICAMENTE con JSON:
{{"candidates": [{{"path": "...", "line": <número o null>, "justification": "..."}}]}}
"""

_FIX_PROMPT = """\
Arregla este bug. Tras tu fix, este comando debe salir con código 0 (hoy
falla, es la prueba de que el bug existe): `{repro_command}`

Descripción del bug: {bug}
{localization}

Archivos:
{content}

Responde ÚNICAMENTE con JSON:
{{"edits": [{{"path": "...", "search": "...", "replace": "..."}}], "rationale": "<1 frase>"}}
"""

_REVIEW_RUBRICS: dict[str, str] = {
    "correctness": (
        "Enfócate SOLO en corrección: ¿el código hace lo que dice que hace, "
        "en todos los casos, incluyendo edge cases? Ignora estilo y seguridad."
    ),
    "security": (
        "Enfócate SOLO en seguridad: inyección, path traversal, "
        "deserialización insegura, secretos hardcodeados, validación de "
        "entrada. Ignora estilo y estructura."
    ),
    "simplicity": (
        "Enfócate SOLO en simplicidad: complejidad injustificada, "
        "duplicación, abstracciones prematuras para lo que pide el código. "
        "Ignora corrección funcional."
    ),
}


async def _read_base_files(target_paths: list[str]) -> dict[str, str]:
    base: dict[str, str] = {}
    for raw in target_paths:
        p = Path(raw)
        base[p.name] = p.read_text(encoding="utf-8") if p.exists() else ""
    return base


def _to_target_paths(edits: list[FileEdit], target_paths: list[str]) -> list[FileEdit]:
    """ÚNICA traducción de "espacio de basename" (como lo ve el modelo) a
    rutas reales de destino, justo antes de sandbox.apply_edits(). Un
    archivo cuyo basename coincide con uno de target_paths va a esa ruta
    exacta; cualquier otro (p.ej. un archivo de test nuevo) va al mismo
    directorio que el primer target_path."""
    by_name = {Path(p).name: p for p in target_paths}
    target_dir = Path(target_paths[0]).parent if target_paths else Path()
    result = []
    for e in edits:
        real_path = by_name.get(Path(e.path).name, str(target_dir / Path(e.path).name))
        result.append(e if real_path == e.path else e.model_copy(update={"path": real_path}))
    return result


async def _run_repro(cmd: list[str], workdir: Path, timeout_s: float = 60.0) -> tuple[bool, str]:
    """Ejecuta el repro_command del usuario tal cual, sin interpretarlo como
    pytest. El criterio de aceptación es el código de salida: 0 = pasa."""
    import subprocess

    def _sync_run() -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return False, f"timeout tras {timeout_s}s: {exc}"
        except OSError as exc:
            return False, f"no se pudo ejecutar repro_command: {exc}"
        return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]

    return await asyncio.to_thread(_sync_run)


async def _verify_in_scratch(
    sandbox: Sandbox, base_files: dict[str, str], edits: list[FileEdit],
    *, test_command: list[str] | None, py_files: list[str],
):
    with tempfile.TemporaryDirectory(prefix="team_feature_") as tmp:
        scratch = Path(tmp)
        for rel, content in base_files.items():
            p = scratch / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        sandbox.materialize_edits(edits, scratch)
        return await verify_candidate(VerifyTarget(
            candidate_id="scratch", workdir=scratch, py_files=py_files, test_command=test_command,
        ))


async def _generate_candidate(
    router: Router, worker_id: str, spec: str, target_paths: list[str], base_files: dict[str, str],
) -> ConsensusCandidate | None:
    # todo lo interno (base_files, edits, scratch dirs, la matriz de
    # consenso) vive en "espacio de basename" plano, igual que las claves de
    # base_files. La traducción a las rutas reales de destino pasa UNA sola
    # vez, en _to_target_paths, justo antes de escribir de verdad — nunca a
    # medio pipeline, porque mezclar ambos espacios ahí es lo que rompía la
    # escritura final (visto fallar en pruebas).
    content = "\n\n".join(f"--- {name} ---\n{c}" for name, c in base_files.items())
    prompt = _IMPLEMENT_PROMPT.format(spec=spec, content=content)
    try:
        raw = await router.coder(_WORKFLOW, prompt, temperature=0.8)
        data = extract_json(raw)
        edits = [FileEdit(**e) for e in data["edits"]]
        test_edits = [FileEdit(**e) for e in data.get("test_edits", [])]
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
        changed = sandbox.apply_edits(_to_target_paths(edits, target_paths))
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


async def _run_refactor(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
) -> Manifest:
    """kind=refactor: preservar comportamiento es la regla dura, no una más.

    1. Tests de caracterización del comportamiento ACTUAL, deben pasar
       contra el código sin tocar (si no pasan, se regeneran — es gratis
       comprobar que se entendió el código antes de tocarlo).
    2. Fan-out de refactors evaluados TODOS contra los mismos tests fijos.
    3. Re-verificación final antes de escribir: sin apelación.
    """
    base_files = await _read_base_files(target_paths)
    content = "\n\n".join(f"--- {n} ---\n{c}" for n, c in base_files.items())
    sandbox = Sandbox(config)

    char_edits: list[FileEdit] = []
    for _ in range(_MAX_CHAR_TEST_ATTEMPTS):
        try:
            raw = await router.coder(_WORKFLOW, _CHARACTERIZE_PROMPT.format(content=content), temperature=0.3)
            data = extract_json(raw)
            candidate = [FileEdit(**e) for e in data["test_edits"]]
        except Exception:  # noqa: BLE001, S112 — reintentamos, no propagamos
            continue
        result = await _verify_in_scratch(
            sandbox, base_files, candidate, test_command=_TEST_COMMAND,
            py_files=[e.path for e in candidate],
        )
        if result.tests_run > 0 and result.tests_passed == result.tests_run:
            char_edits = candidate
            break

    if not char_edits:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="not_run",
            summary=(
                f"no se pudieron generar tests de caracterización que pasen contra el "
                f"código actual tras {_MAX_CHAR_TEST_ATTEMPTS} intentos — abortado sin tocar nada"
            ),
            dry_run=config.dry_run,
        )

    async def _one(worker_id: str) -> ConsensusCandidate | None:
        try:
            raw = await router.coder(_WORKFLOW, _REFACTOR_PROMPT.format(goal=spec, content=content), temperature=0.7)
            data = extract_json(raw)
            edits = [FileEdit(**e) for e in data["edits"]]
        except Exception:  # noqa: BLE001 — un worker caído no debe tumbar el fan-out
            return None
        return ConsensusCandidate(id=worker_id, model="tier-coder", edits=edits, test_edits=[])

    results = await asyncio.gather(*[_one(f"w{i}") for i in range(1, _N_WORKERS + 1)])
    candidates = [c for c in results if c is not None]
    if not candidates:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="red",
            summary="ningún worker produjo un refactor válido (JSON roto o error de API)",
            dry_run=config.dry_run,
        )

    winner: ConsensusCandidate | None = None
    for c in candidates:
        r = await _verify_in_scratch(
            sandbox, base_files, c.edits + char_edits, test_command=_TEST_COMMAND,
            py_files=[e.path for e in c.edits] + [e.path for e in char_edits],
        )
        if r.passes_gate and r.tests_run > 0 and r.tests_passed == r.tests_run:
            winner = c
            break

    if winner is None:
        first = candidates[0]
        outcome = await repair_loop(
            router, _WORKFLOW, sandbox, base_files, spec, first.edits + char_edits,
            "el refactor rompió los tests de caracterización del comportamiento original",
            test_command=_TEST_COMMAND,
        )
        if not outcome.success:
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="red",
                summary=(
                    f"ningún candidato preservó el comportamiento original, ni tras reparación "
                    f"({'estancado' if outcome.stagnated else 'sin converger'}): {outcome.last_error[:300]}"
                ),
                dry_run=config.dry_run,
            )
        final_edits = outcome.final_edits
    else:
        # regla dura repetida a propósito: re-verificar el ganador una última
        # vez antes de escribir, sin excepciones ni atajos.
        final_check = await _verify_in_scratch(
            sandbox, base_files, winner.edits + char_edits, test_command=_TEST_COMMAND,
            py_files=[e.path for e in winner.edits] + [e.path for e in char_edits],
        )
        if not (final_check.tests_run > 0 and final_check.tests_passed == final_check.tests_run):
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="red",
                summary="rechazo automático: el candidato no pasó la re-verificación final de caracterización",
                dry_run=config.dry_run,
            )
        final_edits = winner.edits + char_edits

    try:
        changed = sandbox.apply_edits(_to_target_paths(final_edits, target_paths))
    except (SandboxViolation, EditConflict) as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="green",
            summary=f"verificado pero no se pudo escribir en el sandbox: {exc}",
            dry_run=config.dry_run,
        )

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.refactor, files_changed=changed, tests_status="green",
        summary=f"refactor aplicado, comportamiento preservado ({len(candidates)} candidatos evaluados)",
        dry_run=config.dry_run,
    )


async def _run_fix(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
    repro_command: str | None,
) -> Manifest:
    """kind=fix: el `repro_command` del usuario es la verdad, no algo que un
    modelo reescribe. Sin rojo->verde real en ese comando no hay entrega."""
    if not repro_command:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="not_run",
            summary="kind=fix requiere repro_command (comando que hoy falla y debe pasar a salir con código 0)",
            dry_run=config.dry_run,
        )

    try:
        repro_argv = shlex.split(repro_command)
    except ValueError as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="not_run",
            summary=f"repro_command no se pudo parsear: {exc}", dry_run=config.dry_run,
        )

    base_files = await _read_base_files(target_paths)
    content = "\n\n".join(f"--- {n} ---\n{c}" for n, c in base_files.items())
    sandbox = Sandbox(config)

    with tempfile.TemporaryDirectory(prefix="team_fix_baseline_") as tmp:
        baseline_dir = Path(tmp)
        for rel, c in base_files.items():
            p = baseline_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c, encoding="utf-8")
        baseline_ok, baseline_output = await _run_repro(repro_argv, baseline_dir)

    if baseline_ok:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="not_run",
            summary=(
                "repro_command ya pasa (código 0) contra el código SIN tocar — no se confirma "
                "el bug, abortado sin cambios. Revisa el repro_command o los target_paths."
            ),
            dry_run=config.dry_run,
        )

    localization = "(localización automática no disponible)"
    try:
        raw = await router.context(_WORKFLOW, _LOCALIZE_PROMPT.format(bug=spec, content=content))
        data = extract_json(raw)
        localization = "Localización sugerida: " + "; ".join(
            f"{c.get('path')}:{c.get('line')} — {c.get('justification', '')}"
            for c in data.get("candidates", [])
        )
    except Exception:  # noqa: BLE001, S110 — puramente informativo, no bloquea el fix
        pass

    async def _one(worker_id: str) -> ConsensusCandidate | None:
        prompt = _FIX_PROMPT.format(
            repro_command=repro_command, bug=spec, localization=localization, content=content,
        )
        try:
            raw = await router.coder(_WORKFLOW, prompt, temperature=0.6)
            data = extract_json(raw)
            edits = [FileEdit(**e) for e in data["edits"]]
        except Exception:  # noqa: BLE001 — un worker caído no debe tumbar el fan-out
            return None
        return ConsensusCandidate(id=worker_id, model="tier-coder", edits=edits, test_edits=[])

    results = await asyncio.gather(*[_one(f"w{i}") for i in range(1, _N_WORKERS + 1)])
    candidates = [c for c in results if c is not None]
    if not candidates:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="red",
            summary="ningún worker produjo un parche válido (JSON roto o error de API)",
            dry_run=config.dry_run,
        )

    async def _check(edits: list[FileEdit]) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="team_fix_") as tmp:
            scratch = Path(tmp)
            for rel, c in base_files.items():
                p = scratch / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(c, encoding="utf-8")
            sandbox.materialize_edits(edits, scratch)
            return await _run_repro(repro_argv, scratch)

    winner: ConsensusCandidate | None = None
    for c in candidates:
        ok, _ = await _check(c.edits)
        if ok:
            winner = c
            break

    if winner is None:
        first = candidates[0]
        _, err_output = await _check(first.edits)
        outcome = await repair_loop(
            router, _WORKFLOW, sandbox, base_files, spec, first.edits,
            f"repro_command `{repro_command}` sigue fallando:\n{err_output[:1200]}\n{baseline_output[:500]}",
            test_command=repro_argv,
        )
        if not outcome.success:
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="red",
                summary=(
                    f"ningún parche resolvió el bug, ni tras reparación "
                    f"({'estancado' if outcome.stagnated else 'sin converger'}): {outcome.last_error[:300]}"
                ),
                dry_run=config.dry_run,
            )
        final_edits = outcome.final_edits
    else:
        final_edits = winner.edits

    critic_report = await critic_review(router, _WORKFLOW, spec, final_edits)
    blocking = critic_report.blocking(Severity.high)
    provider_used = {"tier_premium": "agy" if router.premium.last_used != "fallback" else "fallback"}

    try:
        changed = sandbox.apply_edits(_to_target_paths(final_edits, target_paths))
    except (SandboxViolation, EditConflict) as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="green",
            provider_used=provider_used,
            summary=f"bug corregido y verificado pero no se pudo escribir: {exc}",
            dry_run=config.dry_run,
        )

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.fix, files_changed=changed, tests_status="green",
        critic_findings_open=len(blocking), provider_used=provider_used,
        summary=(
            f"bug corregido: repro_command pasa a salir con código 0 "
            f"({len(candidates)} parches evaluados)"
        ),
        dry_run=config.dry_run,
    )


def _dedupe_findings(findings: list[CriticFinding]) -> list[CriticFinding]:
    seen: set[tuple[str, int | None, str]] = set()
    deduped: list[CriticFinding] = []
    for f in findings:
        key = (f.file, f.line, f.claim[:60].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    order = [Severity.critical, Severity.high, Severity.medium, Severity.low]
    deduped.sort(key=lambda f: order.index(f.severity))
    return deduped


async def _run_review(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
) -> Manifest:
    """kind=review: no genera código. 3 pasadas de crítica en paralelo con
    rúbricas distintas (correctness/security/simplicity), deduplicadas."""
    base_files = await _read_base_files(target_paths)
    if not any(base_files.values()):
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.review, tests_status="not_run",
            summary="ninguno de los target_paths existe o tiene contenido que revisar",
            dry_run=config.dry_run,
        )

    edits = [FileEdit(path=name, search="", replace=content) for name, content in base_files.items()]

    reports = await asyncio.gather(*[
        critic_review(router, _WORKFLOW, spec or "revisión general de calidad", edits, focus=focus)
        for focus in _REVIEW_RUBRICS.values()
    ])
    all_findings = [f for report in reports for f in report.findings]
    deduped = _dedupe_findings(all_findings)

    provider_used = {"tier_premium": "agy" if router.premium.last_used != "fallback" else "fallback"}
    if not deduped:
        summary = "revisión completa (3 pasadas: correctness/security/simplicity): sin hallazgos confirmados"
    else:
        lines = [
            f"[{f.severity}] {f.file}:{f.line or '?'} — {f.claim} (escenario: {f.failure_scenario})"
            for f in deduped[:15]
        ]
        summary = f"{len(deduped)} hallazgos confirmados:\n" + "\n".join(lines)

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.review,
        tests_status="not_run", critic_findings_open=len(deduped),
        provider_used=provider_used, summary=summary[:4000],
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

    if resolved_kind == "new":
        return await _run_new(router, ledger, config, spec, target_paths)
    if resolved_kind == "refactor":
        return await _run_refactor(router, ledger, config, spec, target_paths)
    if resolved_kind == "fix":
        return await _run_fix(router, ledger, config, spec, target_paths, repro_command)
    if resolved_kind == "review":
        return await _run_review(router, ledger, config, spec, target_paths)

    return Manifest(
        tool=_WORKFLOW, tests_status="not_run",
        summary=f"kind desconocido: {resolved_kind}. Válidos: new, refactor, fix, review.",
        dry_run=config.dry_run,
    )
