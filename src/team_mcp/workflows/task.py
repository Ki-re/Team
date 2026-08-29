"""team_task: small, unambiguous change to 1 file, no premium tier.

Pipeline (see plan, Layer 0/1 simplified to n=1):
  1. tier-coder proposes a single FileEdit as strict JSON.
  2. It's tested in a scratch dir (never on the real file) with verify.py.
  3. If the deterministic gate fails: 1 repair with the literal error.
  4. If it still fails after the repair: marked `escalated_from="task"`
     in the manifest instead of applying anything. team_feature (phase 3)
     is meant to pick it up — team_task never delivers worse work than promised.
  5. If it passes: atomic write via Sandbox (respects TEAM_DRY_RUN).
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
Task: {instruction}

Target file: {path}
Current content:
---
{content}
---
{error_context}
Respond ONLY with a JSON object in this exact shape, with no extra text
or markdown:
{{"search": "<EXACT fragment of the current content to replace, or \\"\\" if the file is new>",
  "replace": "<new content that replaces `search`>"}}

The `search` field must be copied literally from the current content
(indentation included). If the file doesn't exist yet, use `search: ""`
and put the new file's full content in `replace`.
"""


def _extract_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in the model's response: {raw[:200]}")
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
        try:
            raw = await router.coder(_WORKFLOW, prompt, temperature=0.4 if attempt == 1 else 0.2)
            data = _extract_json(raw)
            edit = FileEdit(path=target_path, search=data["search"], replace=data["replace"])
        except (ValueError, KeyError) as exc:
            last_error = f"invalid JSON from the worker: {exc}"
            error_context = f"\nThe previous attempt failed: {last_error}\n"
            continue
        except Exception as exc:  # noqa: BLE001 — a downed tier-coder call must not crash the whole tool call uncaught; retried the same as invalid JSON, not escalated further, since team_task's own budget is just 2 attempts
            last_error = f"tier-coder call failed: {type(exc).__name__}: {exc}"
            error_context = f"\nThe previous attempt failed: {last_error}\n"
            continue

        with tempfile.TemporaryDirectory(prefix="team_task_") as tmp:
            scratch = Path(tmp)
            scratch_file = scratch / path.name
            scratch_file.write_text(content, encoding="utf-8")

            try:
                if edit.search:
                    if content.count(edit.search) != 1:
                        raise EditConflict("search doesn't match exactly once")
                    new_content = content.replace(edit.search, edit.replace, 1)
                else:
                    new_content = edit.replace
                scratch_file.write_text(new_content, encoding="utf-8")
            except EditConflict as exc:
                last_error = str(exc)
                error_context = f"\nThe previous attempt failed: {last_error}\n"
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
                    summary=f"verified but couldn't write: {exc}",
                    dry_run=config.dry_run,
                )
            return Manifest(
                tool=_WORKFLOW,
                files_changed=changed,
                tests_status="green" if result.tests_run else "not_run",
                summary=f"change applied to {target_path} (attempt {attempt}/{_MAX_ATTEMPTS})",
                dry_run=config.dry_run,
            )

        last_error = result.error_output or "deterministic gate failed with no detail"
        error_context = f"\nThe previous attempt failed verification:\n{last_error[:800]}\n"

    return Manifest(
        tool=_WORKFLOW,
        escalated_from="task",
        files_changed=[],
        tests_status="red",
        summary=(
            f"team_task couldn't produce a valid candidate after {_MAX_ATTEMPTS} attempts: "
            f"{last_error[:300]}. Requires team_feature."
        ),
        dry_run=config.dry_run,
    )
