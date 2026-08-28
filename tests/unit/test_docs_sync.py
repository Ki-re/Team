from __future__ import annotations

import json
from pathlib import Path

from team_mcp.engine.sandbox import Sandbox
from team_mcp.engine.schemas import FileEdit
from team_mcp.workflows import docs_sync
from team_mcp.workflows.docs_sync import _validate_kb_edit

# --- _validate_kb_edit (pure, deterministic) -------------------------------


def test_validate_kb_edit_rejects_path_outside_kb(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    outside = tmp_path / "outside.md"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(outside), search="", replace="x"))
    assert ok is False
    assert "outside kb_path" in reason


def test_validate_kb_edit_accepts_new_file_with_valid_frontmatter(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "new.md"
    content = "---\nname: new\ndescription: something\n---\ncontent\n"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="", replace=content))
    assert ok is True
    assert reason == ""


def test_validate_kb_edit_rejects_new_file_with_broken_frontmatter(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "new.md"
    content = "---\nname: [unclosed\n---\ncontent\n"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="", replace=content))
    assert ok is False
    assert "frontmatter" in reason


def test_validate_kb_edit_rejects_dangling_link_in_result(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "new.md"
    content = "see [broken](does_not_exist.md)\n"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="", replace=content))
    assert ok is False
    assert "broken link" in reason


def test_validate_kb_edit_accepts_search_replace_on_existing_file(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "existing.md"
    target.write_text("---\nname: existing\ndescription: old\n---\nold content\n")
    ok, reason = _validate_kb_edit(
        kb, FileEdit(path=str(target), search="old content", replace="new content"),
    )
    assert ok is True
    assert reason == ""


def test_validate_kb_edit_rejects_search_that_does_not_match_existing_file(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "existing.md"
    target.write_text("real content\n")
    ok, reason = _validate_kb_edit(
        kb, FileEdit(path=str(target), search="this isn't in the file", replace="x"),
    )
    assert ok is False
    assert "doesn't appear exactly once" in reason


def test_validate_kb_edit_rejects_search_on_nonexistent_file(tmp_path: Path):
    kb = tmp_path / "kb"
    kb.mkdir()
    target = kb / "does_not_exist.md"
    ok, reason = _validate_kb_edit(kb, FileEdit(path=str(target), search="something", replace="x"))
    assert ok is False
    assert "new/nonexistent" in reason


# --- run() end-to-end over a test KB, with a fake router -------------------
#
# The real flow is two passes (cheap selection over the index, then a
# per-file edit with its real content) — found necessary while verifying
# live: a single pass with only descriptions didn't give the model the
# real text to copy into "search". The fake router tells the two calls
# apart by the prompt's content, the same way they'd be told apart in
# real telemetry.


class _FakeRouter:
    def __init__(self, *, select: str | Exception = '{"affected": []}', edit=None):
        self._select = select
        self._edit = edit if edit is not None else '{"edits": []}'
        self._edit_calls = 0

    async def context(self, workflow, prompt, temperature=0.2):
        if "Project knowledge-base index" in prompt:
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
    (kb / "INDEX.md").write_text("- [topic](topic.md) — topic description\n", encoding="utf-8")
    (kb / "topic.md").write_text(
        "---\nname: topic\ndescription: topic description\nlast_verified: 2026-08-01\n---\n"
        "The current limit is 10.\n",
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
        kb_path=str(tmp_path / "nonexistent_kb"), changed_files=[str(changed)],
        change_summary="raised the limit to 20",
    )

    assert result["applied"] == []
    assert "nothing to sync" in result["note"]


async def test_docs_sync_run_applies_valid_proposed_edit(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    select = json.dumps({"affected": ["topic.md"]})
    edit = json.dumps({"edits": [{"search": "The current limit is 10.", "replace": "The current limit is 20."}]})

    result = await docs_sync.run(
        _FakeRouter(select=select, edit=edit), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="raised the limit to 20",
    )

    assert len(result["applied"]) == 1
    assert (kb / "topic.md").read_text(encoding="utf-8") == (
        "---\nname: topic\ndescription: topic description\nlast_verified: 2026-08-01\n---\n"
        "The current limit is 20.\n"
    )


async def test_docs_sync_run_ignores_affected_path_outside_known_entries(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    # the model "hallucinates" a file that's not in the KB's index
    select = json.dumps({"affected": ["file_that_does_not_exist.md"]})

    result = await docs_sync.run(
        _FakeRouter(select=select), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="raised the limit to 20",
    )

    assert result["applied"] == []
    assert "no documentation changes needed" in result["note"]


async def test_docs_sync_run_retries_edit_after_validation_failure_and_succeeds(tmp_path, make_config):
    # the real case that motivated the redesign to two passes: the first
    # "search" attempt doesn't match (seen failing live), the retry does.
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    select = json.dumps({"affected": ["topic.md"]})
    bad = json.dumps({"edits": [{"search": "text that matches nothing", "replace": "x"}]})
    good = json.dumps({"edits": [{"search": "The current limit is 10.", "replace": "The current limit is 20."}]})

    result = await docs_sync.run(
        _FakeRouter(select=select, edit=[bad, good]), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="raised the limit to 20",
    )

    assert len(result["applied"]) == 1


async def test_docs_sync_run_skips_when_edit_never_matches_after_retries(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    select = json.dumps({"affected": ["topic.md"]})
    bad = json.dumps({"edits": [{"search": "text that doesn't exist in the file", "replace": "x"}]})

    result = await docs_sync.run(
        _FakeRouter(select=select, edit=bad), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="raised the limit to 20",
    )

    assert result["applied"] == []
    assert len(result["skipped"]) == 1
    assert (kb / "topic.md").read_text(encoding="utf-8").startswith("---\nname: topic")  # untouched


async def test_docs_sync_run_survives_selection_failure_without_raising(tmp_path, make_config):
    kb = _make_kb(tmp_path)
    config = make_config(sandbox_roots=[tmp_path])
    sandbox = Sandbox(config)
    changed = tmp_path / "changed.py"
    changed.write_text("LIMIT = 20\n")

    result = await docs_sync.run(
        _FakeRouter(select=TimeoutError("after 120s")), sandbox,
        kb_path=str(kb), changed_files=[str(changed)], change_summary="raised the limit to 20",
    )

    assert result["applied"] == []
    assert "TimeoutError" in result["note"]
