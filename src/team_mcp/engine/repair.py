"""Bucle de reparación acotado (primitiva #4 del plan).

Máximo N iteraciones (default 3). Cada iteración recibe el error LITERAL
(stack trace, diff de assertion, hallazgo del crítico), nunca "no
funciona". Temperatura baja (reparar es precisión, no diversidad). Si dos
iteraciones seguidas producen el mismo diff, se corta la reparación por
tier-coder — seguir intentando sería ruido.

Último recurso antes de rendirse: un intento vía `agy` (tier-premium). No
es solo el crítico — aquí genera/repara código de verdad. `agy` corre
sobre la suscripción Google Pro del usuario, con muchísima más cuota que
el pool gratuito de tier-coder; tiene sentido gastarla precisamente en el
caso que ya demostró ser duro (2026-08-26: el usuario reportó problemas
de cuota en Gemini vía API y pidió que las tareas pesadas prioricen agy,
que sí tiene margen real).
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from team_mcp.engine.jsonio import extract_json_dict
from team_mcp.engine.sandbox import EditConflict, Sandbox
from team_mcp.engine.schemas import FileEdit, RepairAttempt
from team_mcp.engine.verify import VerifyTarget, verify_candidate

_MAX_ITERATIONS_DEFAULT = 3

_PROMPT = """\
Tu implementación anterior falló. Corrígela.

Spec original:
{spec}

Código actual:
{code}

Error concreto a resolver:
{error}

Responde ÚNICAMENTE con JSON: {{"edits": [{{"path": "...", "replace": "<contenido COMPLETO del archivo ya corregido>"}}]}}
Para cada archivo que toques, `replace` debe ser el archivo ENTERO, no un
fragmento ni un diff — se sobrescribe tal cual. Incluye TODOS los archivos
mostrados arriba en tu respuesta, aunque no cambies alguno. Usa EXACTAMENTE
los mismos nombres de archivo que ves arriba en "path" — sin carpetas,
tal cual aparecen tras "---", aunque el error mencione una ruta distinta.
"""

_PREMIUM_PROMPT = """\
Varios intentos de reparación automática han fallado con este código.
Necesito que lo arregles tú directamente — se te pide porque el problema
ha resultado más difícil de lo normal.

Spec original:
{spec}

Código actual (último intento, todavía roto):
{code}

Error concreto a resolver:
{error}

Responde ÚNICAMENTE con JSON: {{"edits": [{{"path": "...", "replace": "<contenido COMPLETO del archivo ya corregido>"}}]}}
Cada `replace` es el archivo ENTERO, no un diff. Incluye todos los
archivos mostrados arriba, aunque no cambies alguno. Usa EXACTAMENTE los
mismos nombres de archivo que ves arriba en "path" — sin carpetas, tal
cual aparecen tras "---", aunque el error mencione una ruta distinta.
"""


@dataclass
class RepairOutcome:
    success: bool
    final_edits: list[FileEdit]
    iterations: list[RepairAttempt] = field(default_factory=list)
    stagnated: bool = False
    last_error: str = ""


def _edits_signature(edits: list[FileEdit]) -> str:
    blob = "\n".join(f"{e.path}:{e.search}:{e.replace}" for e in sorted(edits, key=lambda x: x.path))
    return hashlib.sha256(blob.encode()).hexdigest()


def _materialize_to_dict(base_files: dict[str, str], edits: list[FileEdit]) -> dict[str, str]:
    """Reconstruye el contenido REAL por archivo tras aplicar `edits` sobre
    `base_files`, en memoria. Necesario porque `edits` puede contener
    fragmentos search/replace parciales (no el archivo completo), y el
    prompt de reparación necesita ver el archivo tal cual quedó, no un
    recorte — si no, el modelo repara a ciegas."""
    state = dict(base_files)
    for e in edits:
        current = state.get(e.path, "")
        if e.search == "" or current.count(e.search) != 1:
            state[e.path] = e.replace
        else:
            state[e.path] = current.replace(e.search, e.replace, 1)
    return state


def _render_code(base_files: dict[str, str], edits: list[FileEdit]) -> str:
    state = _materialize_to_dict(base_files, edits)
    return "\n\n".join(f"--- {path} ---\n{content}" for path, content in state.items())


async def _verify_edits_in_scratch(
    sandbox: Sandbox, base_files: dict[str, str], edits: list[FileEdit],
    *, test_command: list[str] | None, timeout_s: float, candidate_id: str,
):
    with tempfile.TemporaryDirectory(prefix="team_repair_") as tmp:
        scratch = Path(tmp)
        for rel, content in base_files.items():
            p = scratch / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        sandbox.materialize_edits(edits, scratch)
        return await verify_candidate(VerifyTarget(
            candidate_id=candidate_id, workdir=scratch,
            py_files=[e.path for e in edits], test_command=test_command, timeout_s=timeout_s,
        ))


def _force_basename(edits: list[FileEdit]) -> list[FileEdit]:
    """Los `base_files`/scratch dirs de este módulo viven en "espacio de
    basename" plano (igual que en workflows/feature.py, ver su propio
    _force_basename) — pero nada obliga al modelo a devolver un path sin
    carpetas, y el prompt de reparación suele incluir el error literal
    (que a menudo SÍ menciona una ruta con subcarpeta). Visto en vivo:
    un worker de kind=fix hizo justo eso y tumbó la verificación en
    scratch con un EditConflict "no existe" en vez de un fallo limpio.
    Se normaliza aquí, en el límite donde el output del modelo entra al
    espacio interno."""
    return [
        e if Path(e.path).name == e.path else e.model_copy(update={"path": Path(e.path).name})
        for e in edits
    ]


def _parse_repair_edits(raw: str) -> list[FileEdit]:
    data = extract_json_dict(raw)
    # search se fuerza a "" pase lo que pase: la reparación siempre es
    # reescritura completa del archivo, nunca un diff. Un search/replace
    # exacto es justo lo que un modelo pequeño falla a mitad de una
    # reparación bajo presión (visto en pruebas: "apariciones=0" contra
    # el código real).
    edits = [FileEdit(path=e["path"], search="", replace=e["replace"]) for e in data["edits"]]
    return _force_basename(edits)


async def _try_premium_repair(
    router, workflow: str, sandbox: Sandbox, base_files: dict[str, str],
    spec: str, edits: list[FileEdit], error: str,
    *, test_command: list[str] | None, timeout_s: float,
) -> list[FileEdit] | None:
    prompt = _PREMIUM_PROMPT.format(spec=spec, code=_render_code(base_files, edits), error=error[:1500])
    try:
        raw = await router.premium_review(workflow, prompt)
        new_edits = _parse_repair_edits(raw)
    except Exception:  # noqa: BLE001 — último recurso: si falla, se rinde, no propaga
        return None

    try:
        result = await _verify_edits_in_scratch(
            sandbox, base_files, new_edits, test_command=test_command,
            timeout_s=timeout_s, candidate_id="repair-premium",
        )
    except EditConflict:
        return None

    tests_ok = (not test_command) or (result.tests_run > 0 and result.tests_passed == result.tests_run)
    return new_edits if (result.passes_gate and tests_ok) else None


async def repair_loop(
    router,
    workflow: str,
    sandbox: Sandbox,
    base_files: dict[str, str],
    spec: str,
    edits: list[FileEdit],
    initial_error: str,
    *,
    test_command: list[str] | None = None,
    max_iterations: int = _MAX_ITERATIONS_DEFAULT,
    timeout_s: float = 60.0,
    use_premium_fallback: bool = True,
) -> RepairOutcome:
    current_edits = edits
    current_error = initial_error
    attempts: list[RepairAttempt] = []
    last_sig: str | None = None
    stagnated = False

    for i in range(1, max_iterations + 1):
        prompt = _PROMPT.format(
            spec=spec, code=_render_code(base_files, current_edits), error=current_error[:1500],
        )
        raw = await router.coder(workflow, prompt, temperature=0.2)

        try:
            new_edits = _parse_repair_edits(raw)
        except (ValueError, KeyError, TypeError) as exc:
            current_error = f"JSON inválido en la reparación: {exc}"
            attempts.append(RepairAttempt(iteration=i, edits=[], based_on_error=current_error))
            continue

        sig = _edits_signature(new_edits)
        if sig == last_sig:
            stagnated = True
            break
        last_sig = sig

        try:
            result = await _verify_edits_in_scratch(
                sandbox, base_files, new_edits, test_command=test_command,
                timeout_s=timeout_s, candidate_id=f"repair-{i}",
            )
        except EditConflict as exc:
            current_error = f"conflicto aplicando el edit: {exc}"
            attempts.append(RepairAttempt(iteration=i, edits=new_edits, based_on_error=current_error))
            current_edits = new_edits
            continue

        attempts.append(RepairAttempt(iteration=i, edits=new_edits, based_on_error=current_error))
        current_edits = new_edits

        tests_ok = (not test_command) or (result.tests_run > 0 and result.tests_passed == result.tests_run)
        if result.passes_gate and tests_ok:
            return RepairOutcome(success=True, final_edits=new_edits, iterations=attempts)

        current_error = result.error_output or "gate determinista falló sin detalle"

    if use_premium_fallback:
        premium_edits = await _try_premium_repair(
            router, workflow, sandbox, base_files, spec, current_edits, current_error,
            test_command=test_command, timeout_s=timeout_s,
        )
        if premium_edits is not None:
            attempts.append(RepairAttempt(iteration=len(attempts) + 1, edits=premium_edits, based_on_error="agy"))
            return RepairOutcome(success=True, final_edits=premium_edits, iterations=attempts)

    return RepairOutcome(
        success=False, final_edits=current_edits, iterations=attempts,
        stagnated=stagnated, last_error=current_error,
    )
