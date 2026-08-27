from __future__ import annotations

import json
from pathlib import Path

from team_mcp.engine.schemas import FileEdit
from team_mcp.engine.sandbox import Sandbox
from team_mcp.workflows import docs_sync
from team_mcp.workflows.docs_sync import _validate_kb_edit

# --- _validate_kb_edit (puro, determinista) -------------------------------


def test_validate_kb_edit_rejects_path_outside_kb(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    outside = tmp_path / "fuera.md"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(outside), search="", replace="x"))
    assert ok is False
    assert "fuera del kb_path" in reason


def test_validate_kb_edit_accepts_new_file_with_valid_frontmatter(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "nuevo.md"
    content = "---\nname: nuevo\ndescription: algo\n---\ncontenido\n"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="", replace=content))
    assert ok is True
    assert reason == ""


def test_validate_kb_edit_rejects_new_file_with_broken_frontmatter(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "nuevo.md"
    content = "---\nname: [sin cerrar\n---\ncontenido\n"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="", replace=content))
    assert ok is False
    assert "frontmatter" in reason


def test_validate_kb_edit_rejects_dangling_link_in_result(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "nuevo.md"
    content = "ver [roto](no_existe.md)\n"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="", replace=content))
    assert ok is False
    assert "link roto" in reason


def test_validate_kb_edit_accepts_search_replace_on_existing_file(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "existente.md"
    target.write_text("---\nname: existente\ndescription: vieja\n---\ncontenido viejo\n")
    ok, reason = _validate_kb_edit(
        kb, FileEdit(path=str(target), search="contenido viejo", replace="contenido nuevo"),
    )
    assert ok is True
    assert reason == ""


def test_validate_kb_edit_rejects_search_that_does_not_match_existing_file(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "existente.md"
    target.write_text("contenido real\n")
    ok, reason = _validate_kb_edit(
        kb, FileEdit(path=str(target), search="esto no está en el archivo", replace="x"),
    )
    assert ok is False
    assert "no aparece exactamente una vez" in reason


def test_validate_kb_edit_rejects_search_on_nonexistent_file(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "no_existe.md"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="algo", replace="x"))
    assert ok is False
    assert "nuevo/inexistente" in reason


# --- run() end-to-end sobre un KB de prueba, con router falso -------------
#
# El flujo real es de dos pasadas (selección barata sobre el índice, luego
# edición por archivo con su contenido real) — encontrado necesario al
# verificar en vivo: una sola pasada solo con descripciones no le daba al
# modelo el texto real que copiar en "search". El fake router distingue
# ambas llamadas por el contenido del prompt, igual que se distinguirían
# en la telemetría real.


class _FakeRouter:
    def __init__(self, *, select: str | Exception = '{"affected": []}', edit=None):
        self._select = select
        self._edit = edit if edit is not None else '{"edits": []}'
        self._edit_calls = 0

    async def context(self, workflow, prompt, temperature=0.2):
        if "Índice del knowledge-base" in prompt:
            if isinstance(self._select, Exception):
                raise self._select
            return self._select
        response = self._edit[self._edit_calls] if isinstance(self._edit, list) else self._edit
        self._edit_calls += 1
        if isinstance(response, Exception):
            raise response
        return response


def _make_kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "INDEX.md").write_text("- [tema](tema.md) — descripción de tema\n", encoding="utf-8")
    (kb / "tema.md").write_text(
        "---\nname: tema\ndescription: descripción de tema\nlast_verified: 2026-08-01\n---\n"
        "El límite actual es 10.\n",
        encoding="utf-8",
    )
    return kb


async def test_docs_sync_run_without_index_reports_nothing_to_sync(tmp_path, make_config):
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    result = await docs_sync.run(
        _FakeRouter(), sandbox,
        kb_path=str(tmp_path / "kb_inexistente"), changed_files=[str(changed)],
        change_summary="subí el límite a 20",
    )

    assert result["applied"] == []
    assert "nada que sincronizar" in result["note"]


async def test_docs_sync_run_applies_valid_proposed_edit(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    select = json.dumps({"affected": ["tema.md"]})
    edit = json.dumps({"edits": [{"search": "El límite actual es 10.", "replace": "El límite actual es 20."}]})

    result = await docs_sync.run(
        _FakeRouter(select=select, edit=edit), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="subí el límite a 20",
    )

    assert len(result["applied"]) == 1
    assert (kb / "tema.md").read_text(encoding="utf-8") == (
        "---\nname: tema\ndescription: descripción de tema\nlast_verified: 2026-08-01\n---\n"
        "El límite actual es 20.\n"
    )


async def test_docs_sync_run_ignores_affected_path_outside_known_entries(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    # el modelo "alucina" un archivo que no está en el índice del KB
    select = json.dumps({"affected": ["archivo_que_no_existe.md"]})

    result = await docs_sync.run(
        _FakeRouter(select=select), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="subí el límite a 20",
    )

    assert result["applied"] == []
    assert "sin cambios de documentación necesarios" in result["note"]


async def test_docs_sync_run_retries_edit_after_validation_failure_and_succeeds(tmp_path, make_config):
    # el caso real que motivó el rediseño a dos pasadas: el primer intento
    # de "search" no coincide (visto fallar en vivo), el reintento sí.
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    select = json.dumps({"affected": ["tema.md"]})
    bad = json.dumps({"edits": [{"search": "texto que no coincide con nada", "replace": "x"}]})
    good = json.dumps({"edits": [{"search": "El límite actual es 10.", "replace": "El límite actual es 20."}]})

    result = await docs_sync.run(
        _FakeRouter(select=select, edit=[bad, good]), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="subí el límite a 20",
    )

    assert len(result["applied"]) == 1


async def test_docs_sync_run_skips_when_edit_never_matches_after_retries(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    select = json.dumps({"affected": ["tema.md"]})
    bad = json.dumps({"edits": [{"search": "texto que no existe en el archivo", "replace": "x"}]})

    result = await docs_sync.run(
        _FakeRouter(select=select, edit=bad), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="subí el límite a 20",
    )

    assert result["applied"] == []
    assert len(result["skipped"]) == 1
    assert (kb / "tema.md").read_text(encoding="utf-8").startswith("---\nname: tema")  # sin tocar


async def test_docs_sync_run_survives_selection_failure_without_raising(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    result = await docs_sync.run(
        _FakeRouter(select=TimeoutError("tras 120s")), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="subí el límite a 20",
    )

    assert result["applied"] == []
    assert "TimeoutError" in result["note"]
