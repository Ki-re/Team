"""team_task: cambio pequeño en 1 archivo, sin ambigüedad, sin tier premium.

Pipeline (ver plan, Capa 0/1 simplificada a n=1):
  1. tier-coder propone un único FileEdit en JSON estricto.
  2. Se prueba en un scratch dir (nunca sobre el archivo real) con verify.py.
  3. Si falla el gate determinista: 1 reparación con el error literal.
  4. Si sigue fallando tras la reparación: se marca `escalated_from="task"`
     en el manifiesto en vez de aplicar nada. team_feature (fase 3) es quien
     debe recogerlo — team_task nunca entrega trabajo peor de lo prometido.
  5. Si pasa: escritura atómica vía Sandbox (respeta TEAM_DRY_RUN).
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import FileEdit, Manifest
from team_mcp.engine.verify import VerifyTarget, verify_candidate
from team_mcp.providers.router import Router

_WORKFLOW = "team_task"
_MAX_ATTEMPTS = 2

_PROMPT = """\
Tarea: {instruction}

Archivo objetivo: {path}
Contenido actual:
---
{content}
---
{error_context}
Responde ÚNICAMENTE con un objeto JSON con esta forma exacta, sin texto
adicional ni markdown:
{{"search": "<fragmento EXACTO del contenido actual a reemplazar, o \\"\\" si el archivo es nuevo>",
  "replace": "<contenido nuevo que sustituye a `search`>"}}

El campo `search` debe copiarse literalmente del contenido actual (incluida
indentación). Si el archivo no existe todavía, usa `search: ""` y pon el
contenido completo del archivo nuevo en `replace`.
"""


def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"sin JSON en la respuesta del modelo: {raw[:200]}")
    return json.loads(match.group(0))


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    instruction: str,
    target_path: str,
) -> Manifest:
    path = Path(target_path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""

    error_context = ""
    last_error = ""

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        prompt = _PROMPT.format(
            instruction=instruction, path=target_path, content=content,
            error_context=error_context,
        )
        raw = await router.coder(_WORKFLOW, prompt, temperature=0.4 if attempt == 1 else 0.2)

        try:
            data = _extract_json(raw)
            edit = FileEdit(path=target_path, search=data["search"], replace=data["replace"])
        except (ValueError, KeyError) as exc:
            last_error = f"JSON inválido del worker: {exc}"
            error_context = f"\nEl intento anterior falló: {last_error}\n"
            continue

        with tempfile.TemporaryDirectory(prefix="team_task_") as tmp:
            scratch = Path(tmp)
            scratch_file = scratch / path.name
            scratch_file.write_text(content, encoding="utf-8")

            try:
                if edit.search:
                    if content.count(edit.search) != 1:
                        raise EditConflict("search no coincide exactamente una vez")
                    new_content = content.replace(edit.search, edit.replace, 1)
                else:
                    new_content = edit.replace
                scratch_file.write_text(new_content, encoding="utf-8")
            except EditConflict as exc:
                last_error = str(exc)
                error_context = f"\nEl intento anterior falló: {last_error}\n"
                continue

            result = await verify_candidate(VerifyTarget(
                candidate_id="task-1", workdir=scratch, py_files=[path.name],
            ))

        if result.passes_gate:
            try:
                sandbox = Sandbox(config)
                changed = sandbox.apply_edits([edit])
            except (SandboxViolation, EditConflict) as exc:
                return Manifest(
                    tool=_WORKFLOW, files_changed=[], tests_status="not_run",
                    summary=f"verificado pero no se pudo escribir: {exc}",
                    dry_run=config.dry_run,
                )
            return Manifest(
                tool=_WORKFLOW,
                files_changed=changed,
                tests_status="green" if result.tests_run else "not_run",
                summary=f"cambio aplicado en {target_path} (intento {attempt}/{_MAX_ATTEMPTS})",
                dry_run=config.dry_run,
            )

        last_error = result.error_output or "gate determinista falló sin detalle"
        error_context = f"\nEl intento anterior falló la verificación:\n{last_error[:800]}\n"

    return Manifest(
        tool=_WORKFLOW,
        escalated_from="task",
        files_changed=[],
        tests_status="red",
        summary=(
            f"team_task no logró un candidato válido tras {_MAX_ATTEMPTS} intentos: "
            f"{last_error[:300]}. Requiere team_feature."
        ),
        dry_run=config.dry_run,
    )
