"""Bucle de reparación acotado (primitiva #4 del plan).

Máximo N iteraciones (default 3). Cada iteración recibe el error LITERAL
(stack trace, hallazgo del crítico), nunca "no funciona". Temperatura baja
(reparar es precisión, no diversidad). Si dos iteraciones seguidas producen
el mismo diff, se corta y se escala — seguir intentando sería ruido.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from team_mcp.engine.jsonio import extract_json
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

Responde ÚNICAMENTE con JSON: {{"edits": [{{"path": "...", "search": "...", "replace": "..."}}]}}
Cada `search` debe copiarse literalmente del código actual mostrado arriba.
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


def _render_code(edits: list[FileEdit]) -> str:
    return "\n\n".join(f"--- {e.path} ---\n{e.replace}" for e in edits)


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
) -> RepairOutcome:
    current_edits = edits
    current_error = initial_error
    attempts: list[RepairAttempt] = []
    last_sig: str | None = None

    for i in range(1, max_iterations + 1):
        prompt = _PROMPT.format(
            spec=spec, code=_render_code(current_edits), error=current_error[:1500],
        )
        raw = await router.coder(workflow, prompt, temperature=0.2)

        try:
            data = extract_json(raw)
            new_edits = [FileEdit(**e) for e in data["edits"]]
        except (ValueError, KeyError, TypeError) as exc:
            current_error = f"JSON inválido en la reparación: {exc}"
            attempts.append(RepairAttempt(iteration=i, edits=[], based_on_error=current_error))
            continue

        sig = _edits_signature(new_edits)
        if sig == last_sig:
            return RepairOutcome(
                success=False, final_edits=current_edits, iterations=attempts,
                stagnated=True, last_error=current_error,
            )
        last_sig = sig

        with tempfile.TemporaryDirectory(prefix="team_repair_") as tmp:
            scratch = Path(tmp)
            for rel, content in base_files.items():
                p = scratch / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            try:
                sandbox.materialize_edits(new_edits, scratch)
            except EditConflict as exc:
                current_error = f"conflicto aplicando el edit: {exc}"
                attempts.append(RepairAttempt(iteration=i, edits=new_edits, based_on_error=current_error))
                current_edits = new_edits
                continue

            result = await verify_candidate(VerifyTarget(
                candidate_id=f"repair-{i}", workdir=scratch,
                py_files=[e.path for e in new_edits], test_command=test_command,
                timeout_s=timeout_s,
            ))

        attempts.append(RepairAttempt(iteration=i, edits=new_edits, based_on_error=current_error))
        current_edits = new_edits

        tests_ok = (not test_command) or (result.tests_run > 0 and result.tests_passed == result.tests_run)
        if result.passes_gate and tests_ok:
            return RepairOutcome(success=True, final_edits=new_edits, iterations=attempts)

        current_error = result.error_output or "gate determinista falló sin detalle"

    return RepairOutcome(
        success=False, final_edits=current_edits, iterations=attempts, last_error=current_error,
    )
