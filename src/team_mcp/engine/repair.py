"""Bounded repair loop (primitive #4 of the plan).

Max N iterations (default 3). Each iteration gets the LITERAL error
(stack trace, assertion diff, critic finding), never "it doesn't work".
Low temperature (repairing is about precision, not diversity). If two
consecutive iterations produce the same diff, the tier-coder repair gets
cut short — continuing would just be noise.

Last resort before giving up: one attempt via `agy` (tier-premium). Not
just as critic — here it actually generates/repairs code for real. `agy`
runs on the operator's own paid subscription, with far more real headroom
than the free tier-coder pool; it makes sense to spend it precisely on
the case that already proved hard.
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
Your previous implementation failed. Fix it.

Original spec:
{spec}

Current code:
{code}

Concrete error to resolve:
{error}

Respond ONLY with JSON: {{"edits": [{{"path": "...", "replace": "<FULL content of the already-fixed file>"}}]}}
For each file you touch, `replace` must be the WHOLE file, not a fragment
or a diff — it gets overwritten as-is. Include ALL the files shown above
in your response, even ones you don't change. Use EXACTLY the same file
names you see above in "path" — no folders, exactly as they appear after
"---", even if the error mentions a different path.
"""

_PREMIUM_PROMPT = """\
Several automatic repair attempts have failed on this code. I need you to
fix it directly — you're being asked because the problem turned out to be
harder than usual.

Original spec:
{spec}

Current code (last attempt, still broken):
{code}

Concrete error to resolve:
{error}

Respond ONLY with JSON: {{"edits": [{{"path": "...", "replace": "<FULL content of the already-fixed file>"}}]}}
Each `replace` is the WHOLE file, not a diff. Include all the files shown
above, even ones you don't change. Use EXACTLY the same file names you
see above in "path" — no folders, exactly as they appear after "---",
even if the error mentions a different path.
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
    """Rebuilds the REAL per-file content after applying `edits` on top of
    `base_files`, in memory. Needed because `edits` may contain partial
    search/replace fragments (not the whole file), and the repair prompt
    needs to see the file as it actually ended up, not a snippet — otherwise
    the model repairs blind."""
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
    """This module's `base_files`/scratch dirs live in flat "basename
    space" (same as workflows/feature.py, see its own _force_basename) —
    but nothing forces the model to return a folder-free path, and the
    repair prompt usually includes the literal error (which often DOES
    mention a path with a subfolder). Seen live: a kind=fix worker did
    exactly that and broke scratch verification with an EditConflict
    "doesn't exist" instead of a clean failure. Normalized here, at the
    boundary where the model's output enters the internal space."""
    return [
        e if Path(e.path).name == e.path else e.model_copy(update={"path": Path(e.path).name})
        for e in edits
    ]


def _parse_repair_edits(raw: str) -> list[FileEdit]:
    data = extract_json_dict(raw)
    # search is forced to "" no matter what: repair is always a full file
    # rewrite, never a diff. An exact search/replace is exactly what a
    # small model fails at midway through a repair under pressure (seen in
    # testing: "occurrences=0" against the real code).
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
    except Exception:  # noqa: BLE001 — last resort: if it fails, give up, don't propagate
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
        try:
            raw = await router.coder(workflow, prompt, temperature=0.2)
        except Exception as exc:  # noqa: BLE001 — a downed tier-coder call must not crash the whole repair loop (and everything that called it) uncaught; found live as the likely cause of team_feature crashing during exactly the consensus-failure rescue this loop provides, while tier-coder was genuinely flaky (ReadTimeouts confirmed in the ledger)
            current_error = f"tier-coder call failed: {type(exc).__name__}: {exc}"[:300]
            attempts.append(RepairAttempt(iteration=i, edits=[], based_on_error=current_error))
            continue

        try:
            new_edits = _parse_repair_edits(raw)
        except (ValueError, KeyError, TypeError) as exc:
            current_error = f"invalid JSON in the repair: {exc}"
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
            current_error = f"conflict applying the edit: {exc}"
            attempts.append(RepairAttempt(iteration=i, edits=new_edits, based_on_error=current_error))
            current_edits = new_edits
            continue

        attempts.append(RepairAttempt(iteration=i, edits=new_edits, based_on_error=current_error))
        current_edits = new_edits

        tests_ok = (not test_command) or (result.tests_run > 0 and result.tests_passed == result.tests_run)
        if result.passes_gate and tests_ok:
            return RepairOutcome(success=True, final_edits=new_edits, iterations=attempts)

        current_error = result.error_output or "deterministic gate failed with no detail"

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
