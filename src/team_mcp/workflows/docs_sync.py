"""Subagente de documentación (Fase 12 del plan).

Modo opcional de team_feature/team_epic (`update_docs=True, kb_path=...`),
no una tool propia — mismo patrón que `allow_web_search` en team_ask o
`selftest` en team_validate. Tras un cambio de código exitoso, decide qué
archivos existentes del knowledge-base (ver docs/KB_CONVENTION.md —
frontmatter + INDEX.md, el mismo patrón que la propia memoria de Claude)
quedaron desactualizados, propone parches con el contrato FileEdit ya
usado en todo el pipeline, los valida de forma determinista (frontmatter
sigue siendo YAML válido, sin links rotos) y los aplica vía el Sandbox
real — igual que cualquier otro edit.

Dos pasadas, no una — encontrado al verificar en vivo, no al diseñar en
papel: una primera versión mandaba solo el índice (nombre+descripción, sin
cuerpo) y pedía el `search` exacto directamente, y el modelo no tenía forma
de copiar texto que nunca vio — fallaba con "el bloque search no aparece
exactamente una vez" en la primera prueba real contra el gateway. Ahora:
  1. selección barata sobre el índice (qué archivos, no cómo editarlos).
  2. por cada archivo seleccionado, una llamada con su contenido REAL,
     igual que task.py — con un reintento si el search no encaja, mismo
     patrón de error literal de vuelta que usa el resto del pipeline.

Alcance deliberadamente acotado en esta versión: solo actualiza archivos
YA existentes en el índice. No inventa entradas nuevas — decidir dónde
vive un doc nuevo y con qué estructura es un juicio que no vale la pena
automatizar todavía; queda para una iteración futura si hace falta.

Siempre best-effort: cualquier fallo (JSON roto, sin KB, nada que
sincronizar) se reporta en el resultado y nunca propaga una excepción —
nunca debe tumbar el team_feature/team_epic que lo llamó.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from team_mcp.engine.frontmatter import find_local_links, list_kb_entries, split_frontmatter
from team_mcp.engine.jsonio import extract_json
from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import FileEdit
from team_mcp.providers.router import Router

_WORKFLOW = "docs_sync"
_MAX_CHANGED_CONTENT_CHARS = 6000
_MAX_FILE_EXCERPT_CHARS = 3000
_MAX_EDIT_ATTEMPTS = 2  # mismo patrón que task.py: un reintento con el error literal basta

_SELECT_PROMPT = """\
Este cambio de código se acaba de aplicar:

{change_summary}

Archivos que cambiaron, contenido actual:
{changed_content}

Índice del knowledge-base del proyecto (un archivo por tema, con su
descripción — NO su contenido completo):
{kb_index}

¿Cuáles de estos archivos de documentación probablemente quedaron
desactualizados por el cambio de arriba? No los edites todavía, solo
identifica cuáles revisar. Si ninguno aplica, responde con lista vacía.

Responde ÚNICAMENTE con JSON:
{{"affected": ["ruta1.md", "ruta2.md"]}}
"""

_EDIT_PROMPT = """\
Este cambio de código se acaba de aplicar:

{change_summary}

Contenido ACTUAL completo del archivo de documentación a revisar
(`{path}`):
---
{content}
---
{error_context}
¿Este archivo necesita cambiar por el cambio de código de arriba? Si sí,
propón como mucho una edición puntual (bloque search/replace exacto y
mínimo, NO reescribas el archivo entero) — el campo "search" debe
copiarse LITERALMENTE del contenido de arriba, carácter por carácter. Si
no necesita cambiar, responde con una lista vacía.

Responde ÚNICAMENTE con JSON:
{{"edits": [{{"search": "<fragmento exacto del contenido de arriba>", "replace": "..."}}]}}
"""


def _validate_kb_edit(kb: Path, edit: FileEdit) -> tuple[bool, str]:
    target = Path(edit.path)
    try:
        target.resolve().relative_to(kb.resolve())
    except ValueError:
        return False, "ruta fuera del kb_path"

    if edit.search == "":
        new_content = edit.replace
    else:
        if not target.exists():
            return False, "search no vacío para un archivo nuevo/inexistente"
        current = target.read_text(encoding="utf-8", errors="replace")
        if current.count(edit.search) != 1:
            return False, "el bloque search no aparece exactamente una vez"
        new_content = current.replace(edit.search, edit.replace, 1)

    if new_content.lstrip().startswith("---"):
        fm, _ = split_frontmatter(new_content)
        if fm is None:
            return False, "el resultado no tiene frontmatter YAML válido"

    for link in find_local_links(new_content):
        if not (target.parent / link).resolve().exists():
            return False, f"link roto tras el edit: {link}"

    return True, ""


async def _propose_edit_for_file(
    router: Router, kb: Path, rel_path: str, change_summary: str,
) -> tuple[FileEdit | None, str]:
    """Hasta _MAX_EDIT_ATTEMPTS intentos sobre UN archivo, con el error de
    validación literal realimentado en el reintento — igual que task.py."""
    target = kb / rel_path
    if not target.exists():
        return None, f"{rel_path}: en el índice pero no existe en disco"
    content = target.read_text(encoding="utf-8", errors="replace")

    error_context = ""
    last_error = ""
    for _ in range(_MAX_EDIT_ATTEMPTS):
        prompt = _EDIT_PROMPT.format(
            change_summary=change_summary[:1500], path=rel_path,
            content=content[:_MAX_FILE_EXCERPT_CHARS], error_context=error_context,
        )
        try:
            raw = await router.context(_WORKFLOW, prompt)
            data = extract_json(raw)
            edits = data.get("edits", [])
        except Exception as exc:  # noqa: BLE001 — un archivo caído no debe tumbar el resto
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            error_context = f"\nEl intento anterior falló: {last_error}\n"
            continue

        if not edits:
            return None, ""  # el modelo decidió que este archivo no necesita cambios

        full = FileEdit(path=str(target), search=edits[0].get("search", ""), replace=edits[0]["replace"])
        ok, reason = _validate_kb_edit(kb, full)
        if ok:
            return full, ""
        last_error = reason
        error_context = f"\nEl intento anterior falló la verificación: {last_error}\n"

    return None, f"{rel_path}: {last_error}"


async def run(
    router: Router, sandbox: Sandbox, *,
    kb_path: str, changed_files: list[str], change_summary: str,
) -> dict:
    """Devuelve un dict compacto — `{"applied": [...], "skipped": [...],
    "note": "..."}`. Se fusiona en el Manifest del workflow que lo llamó,
    nunca se expone como su propio Manifest."""
    kb = Path(kb_path)
    index_file = kb / "INDEX.md"
    if not kb.is_dir() or not index_file.exists():
        return {"applied": [], "skipped": [], "note": f"KB sin INDEX.md en {kb_path}: nada que sincronizar"}

    entries = list_kb_entries(kb)
    if not entries:
        return {"applied": [], "skipped": [], "note": "KB sin entradas con frontmatter válido"}

    changed_content = "\n\n".join(
        f"--- {f} ---\n{Path(f).read_text(encoding='utf-8', errors='replace')[:_MAX_FILE_EXCERPT_CHARS]}"
        for f in changed_files if Path(f).is_file()
    )
    if not changed_content:
        return {"applied": [], "skipped": [], "note": "ningún archivo cambiado es legible: nada que sincronizar"}

    known_paths = {e["path"] for e in entries}
    kb_index_text = "\n".join(
        f"- {e['path']}: {e.get('name', '?')} — {e.get('description', '')}" for e in entries
    )
    select_prompt = _SELECT_PROMPT.format(
        change_summary=change_summary[:1500],
        changed_content=changed_content[:_MAX_CHANGED_CONTENT_CHARS],
        kb_index=kb_index_text,
    )

    try:
        raw = await router.context(_WORKFLOW, select_prompt)
        data = extract_json(raw)
        affected = [p for p in data.get("affected", []) if p in known_paths]
    except Exception as exc:  # noqa: BLE001 — docs_sync es siempre best-effort
        return {
            "applied": [], "skipped": [],
            "note": f"docs_sync (selección) falló: {type(exc).__name__}: {exc}"[:300],
        }

    if not affected:
        return {"applied": [], "skipped": [], "note": "sin cambios de documentación necesarios"}

    results = await asyncio.gather(*[
        _propose_edit_for_file(router, kb, rel, change_summary) for rel in affected
    ])
    valid_edits = [edit for edit, _ in results if edit is not None]
    skipped = [reason for _, reason in results if reason]

    applied: list[str] = []
    if valid_edits:
        try:
            applied = sandbox.apply_edits(valid_edits)
        except (SandboxViolation, EditConflict) as exc:
            skipped.append(f"aplicación fallida: {exc}")

    return {
        "applied": applied, "skipped": skipped,
        "note": f"{len(applied)} archivo(s) de KB actualizados, {len(skipped)} omitidos",
    }
