"""Documentation sub-agent.

Optional mode of team_feature/team_epic (`update_docs=True, kb_path=...`),
not a tool of its own — same pattern as `allow_web_search` on team_ask or
`selftest` on team_validate. After a successful code change, it decides
which existing knowledge-base files (see docs/KB_CONVENTION.md —
frontmatter + INDEX.md, the same pattern Claude's own memory uses) went
stale, proposes patches with the same FileEdit contract used across the
whole pipeline, validates them deterministically (frontmatter stays valid
YAML, no broken links) and applies them through the real Sandbox — just
like any other edit.

Two passes, not one — found by verifying live, not by designing on paper:
a first version only sent the index (name+description, no body) and asked
for the exact `search` directly, and the model had no way to copy text it
had never seen — it failed with "the search block doesn't appear exactly
once" on the first real test against the gateway. Now:
  1. cheap selection over the index (which files, not how to edit them).
  2. for each selected file, one call with its REAL content, same as
     task.py — with a retry if the search doesn't match, the same literal
     error feedback pattern used across the rest of the pipeline.

Deliberately narrow scope in this version: it only updates files that are
ALREADY in the index. It doesn't invent new entries — deciding where a new
doc should live and how it should be structured is a judgment call not
worth automating yet; left for a future iteration if needed.

Always best-effort: any failure (broken JSON, no KB, nothing to sync) is
reported in the result and never propagates as an exception — it must
never take down the team_feature/team_epic call that invoked it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from team_mcp.engine.frontmatter import find_local_links, list_kb_entries, split_frontmatter
from team_mcp.engine.jsonio import extract_json_dict
from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import FileEdit
from team_mcp.providers.router import Router

_WORKFLOW = "docs_sync"
_MAX_CHANGED_CONTENT_CHARS = 6000
_MAX_FILE_EXCERPT_CHARS = 3000
_MAX_EDIT_ATTEMPTS = 2  # same pattern as task.py: one retry with the literal error is enough

_SELECT_PROMPT = """\
This code change was just applied:

{change_summary}

Files that changed, current content:
{changed_content}

Project knowledge-base index (one file per topic, with its description —
NOT its full content):
{kb_index}

Which of these documentation files likely went stale because of the
change above? Don't edit them yet, just identify which ones to review. If
none apply, respond with an empty list.

Respond ONLY with JSON:
{{"affected": ["path1.md", "path2.md"]}}
"""

_EDIT_PROMPT = """\
This code change was just applied:

{change_summary}

Current FULL content of the documentation file to review
(`{path}`):
---
{content}
---
{error_context}
Does this file need to change because of the change above? If so, propose
at most one targeted edit (an exact, minimal search/replace block, do NOT
rewrite the whole file) — the "search" field must be copied LITERALLY
from the content above, character for character. If it doesn't need to
change, respond with an empty list.

Respond ONLY with JSON:
{{"edits": [{{"search": "<exact fragment from the content above>", "replace": "..."}}]}}
"""


def _validate_kb_edit(kb: Path, edit: FileEdit) -> tuple[bool, str]:
    target = Path(edit.path)
    try:
        target.resolve().relative_to(kb.resolve())
    except ValueError:
        return False, "path outside kb_path"

    if edit.search == "":
        new_content = edit.replace
    else:
        if not target.exists():
            return False, "non-empty search for a new/nonexistent file"
        current = target.read_text(encoding="utf-8", errors="replace")
        if current.count(edit.search) != 1:
            return False, "the search block doesn't appear exactly once"
        new_content = current.replace(edit.search, edit.replace, 1)

    if new_content.lstrip().startswith("---"):
        fm, _ = split_frontmatter(new_content)
        if fm is None:
            return False, "the result doesn't have valid YAML frontmatter"

    for link in find_local_links(new_content):
        if not (target.parent / link).resolve().exists():
            return False, f"broken link after the edit: {link}"

    return True, ""


async def _propose_edit_for_file(
    router: Router, kb: Path, rel_path: str, change_summary: str,
) -> tuple[FileEdit | None, str]:
    """Up to _MAX_EDIT_ATTEMPTS attempts on ONE file, with the literal
    validation error fed back on retry — same as task.py."""
    target = kb / rel_path
    if not target.exists():
        return None, f"{rel_path}: in the index but doesn't exist on disk"
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
            data = extract_json_dict(raw)
            edits = data.get("edits", [])
        except Exception as exc:  # noqa: BLE001 — one downed file shouldn't take down the rest
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            error_context = f"\nThe previous attempt failed: {last_error}\n"
            continue

        if not edits:
            return None, ""  # the model decided this file needs no changes

        full = FileEdit(path=str(target), search=edits[0].get("search", ""), replace=edits[0]["replace"])
        ok, reason = _validate_kb_edit(kb, full)
        if ok:
            return full, ""
        last_error = reason
        error_context = f"\nThe previous attempt failed verification: {last_error}\n"

    return None, f"{rel_path}: {last_error}"


async def run(
    router: Router, sandbox: Sandbox, *,
    kb_path: str, changed_files: list[str], change_summary: str,
) -> dict:
    """Returns a compact dict — `{"applied": [...], "skipped": [...],
    "note": "..."}`. Gets merged into the calling workflow's Manifest,
    never exposed as its own Manifest."""
    kb = Path(kb_path)
    index_file = kb / "INDEX.md"
    if not kb.is_dir() or not index_file.exists():
        return {"applied": [], "skipped": [], "note": f"KB has no INDEX.md at {kb_path}: nothing to sync"}

    entries = list_kb_entries(kb)
    if not entries:
        return {"applied": [], "skipped": [], "note": "KB has no entries with valid frontmatter"}

    changed_content = "\n\n".join(
        f"--- {f} ---\n{Path(f).read_text(encoding='utf-8', errors='replace')[:_MAX_FILE_EXCERPT_CHARS]}"
        for f in changed_files if Path(f).is_file()
    )
    if not changed_content:
        return {"applied": [], "skipped": [], "note": "no changed file is readable: nothing to sync"}

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
        data = extract_json_dict(raw)
        affected = [p for p in data.get("affected", []) if p in known_paths]
    except Exception as exc:  # noqa: BLE001 — docs_sync is always best-effort
        return {
            "applied": [], "skipped": [],
            "note": f"docs_sync (selection) failed: {type(exc).__name__}: {exc}"[:300],
        }

    if not affected:
        return {"applied": [], "skipped": [], "note": "no documentation changes needed"}

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
            skipped.append(f"apply failed: {exc}")

    return {
        "applied": applied, "skipped": skipped,
        "note": f"{len(applied)} KB file(s) updated, {len(skipped)} skipped",
    }
