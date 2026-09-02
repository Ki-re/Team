"""team_feature: fan-out + cross-validation consensus + premium critique + bounded repair.

`kind="new"` pipeline (primitive #3 of the plan — team_implement):
  1. reads the current content of target_paths (simple context contract;
     tier-context's full map-reduce is a later improvement).
  2. fan-out N x tier-coder (high temp, varied prompts) -> candidates with
     implementation + tests.
  3. deterministic gate (verify.py) discards candidates that don't parse/lint.
  4. cross-validation consensus (consensus.py) over the survivors.
  5. adversarial critique (critic.py, tier-premium) on the winner.
  6. if there are red tests or blocking findings: bounded repair_loop.
  7. atomic write via Sandbox + Manifest.

`kind="refactor"` and `kind="fix"` reorder these same primitives (see
_run_refactor/_run_fix docstrings). `kind="review"` doesn't generate
code: it lives in engine/critic.py + this same `run` function, with
3 parallel critique passes (different rubrics) and deduplication.
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
from team_mcp.engine.jsonio import extract_json_dict
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.repair import repair_loop
from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import (
    CriticFinding,
    CriticReport,
    FeatureKind,
    FileEdit,
    Manifest,
    Severity,
)
from team_mcp.engine.verify import VerifyTarget, compress_log, verify_candidate
from team_mcp.providers.router import Router
from team_mcp.workflows import docs_sync

_WORKFLOW = "team_feature"
_N_WORKERS = 3
_TEST_COMMAND = [sys.executable, "-m", "pytest", "-q"]  # never rely on "pytest" being on PATH
_MAX_CHAR_TEST_ATTEMPTS = 2
_MAX_LOCALIZE_ATTEMPTS = 2

_IMPLEMENT_PROMPT = """\
Implement this:

Spec: {spec}

Current content, one per file (empty if these are new files):
{content}

Use EXACTLY the same file names you see above in the "path" field of your
edits — no folders, exactly as they appear after "---".

Also write pytest tests for your implementation, in a file named exactly
`test_solution.py` (same name for every worker, so implementations and
tests can be cross-checked between candidates).

Respond ONLY with JSON:
{{"edits": [{{"path": "...", "search": "<empty if this is a new file>", "replace": "..."}}],
  "test_edits": [{{"path": "test_solution.py", "search": "", "replace": "..."}}],
  "rationale": "<1 sentence>"}}
"""

_CHARACTERIZE_PROMPT = """\
Write pytest tests that describe the CURRENT behavior of this code, AS IT
IS, without judging whether it's correct or trying to improve it. Goal:
capture what it does today, so a refactor breaking it can be detected.
Cover the main paths you use/see exercised by the code.

Files:
{content}

Use EXACTLY the same file names you see above in the "path" field — no
folders, exactly as they appear after "---".

Respond ONLY with JSON:
{{"test_edits": [{{"path": "test_characterization.py", "search": "", "replace": "..."}}]}}
"""

_REFACTOR_PROMPT = """\
Refactor this code according to the goal, WITHOUT changing its observable
behavior (there are characterization tests that must stay green).

Refactor goal: {goal}

Files:
{content}

Use EXACTLY the same file names you see above in the "path" field of your
edits — no folders, exactly as they appear after "---".

Respond ONLY with JSON:
{{"edits": [{{"path": "...", "search": "...", "replace": "..."}}], "rationale": "<1 sentence>"}}
"""

_LOCALIZE_PROMPT = """\
There's a bug. Briefly describe which file/line is most likely to be at
fault, with your reasoning. Don't fix it yet.

Bug description: {bug}

Files:
{content}

Respond ONLY with JSON:
{{"candidates": [{{"path": "...", "line": <number or null>, "justification": "..."}}]}}
"""

_FIX_PROMPT = """\
Fix this bug. After your fix, this command must exit with code 0 (it
fails today, that's the proof the bug exists): `{repro_command}`

Bug description: {bug}
{localization}

Files:
{content}

Use EXACTLY the same file names you see above in the "path" field of your
edits — no folders, exactly as they appear after "---". Even if the
repro_command or the description mentions a path with subfolders (e.g.
"src/foo.py"), "path" gets ONLY the file name ("foo.py").

Respond ONLY with JSON:
{{"edits": [{{"path": "...", "search": "...", "replace": "..."}}], "rationale": "<1 sentence>"}}
"""

_REVIEW_RUBRICS: dict[str, str] = {
    "correctness": (
        "Focus ONLY on correctness: does the code do what it claims, in "
        "every case, including edge cases? Ignore style and security."
    ),
    "security": (
        "Focus ONLY on security: injection, path traversal, insecure "
        "deserialization, hardcoded secrets, input validation. Ignore "
        "style and structure."
    ),
    "simplicity": (
        "Focus ONLY on simplicity: unjustified complexity, duplication, "
        "premature abstractions for what the code needs to do. Ignore "
        "functional correctness."
    ),
}


async def _read_base_files(target_paths: list[str]) -> dict[str, str]:
    base: dict[str, str] = {}
    for raw in target_paths:
        p = Path(raw)
        base[p.name] = p.read_text(encoding="utf-8") if p.exists() else ""
    return base


def _validate_target_paths(target_paths: list[str]) -> str | None:
    """Returns an error message if any target_path is a directory, else
    None. `Path.exists()` is true for directories too, so
    `_read_base_files` used to hand them straight to `read_text()` —
    which raises (PermissionError on Windows, IsADirectoryError on
    POSIX), uncaught anywhere upstream, crashing the whole tool call with
    no Manifest at all. Found investigating a report of team_feature
    "erroring out on every directory-shaped target_paths call". Checked
    once, up front, for all four `kind`s — a directory target is a
    caller mistake worth reporting clearly, not something to silently
    reinterpret as an empty new file."""
    dirs = [p for p in target_paths if Path(p).is_dir()]
    if dirs:
        return f"target_paths must be files, not directories: {dirs}"
    return None


def _force_basename(edits: list[FileEdit]) -> list[FileEdit]:
    """The internal pipeline (consensus, scratch dirs) lives in a flat
    "basename space" — but nothing forces the model to respect it, and in
    production a kind=fix worker was seen returning a `path` with folders
    (the repro_command mentioned a subfolder, the model copied it), which
    broke scratch verification with an EditConflict "doesn't exist" BEFORE
    reaching _to_target_paths (that function does normalize, but it's only
    called right before the final write, not during intermediate
    verification). The basename is forced here, at the boundary where the
    model's output enters the internal space, instead of trusting the
    model to follow the convention."""
    return [
        e if Path(e.path).name == e.path else e.model_copy(update={"path": Path(e.path).name})
        for e in edits
    ]


def _to_target_paths(edits: list[FileEdit], target_paths: list[str]) -> list[FileEdit]:
    """THE ONE translation from "basename space" (as the model sees it) to
    real destination paths, right before sandbox.apply_edits(). A file
    whose basename matches one of target_paths goes to that exact path;
    anything else (e.g. a new test file) goes to the same directory as the
    first target_path."""
    by_name = {Path(p).name: p for p in target_paths}
    target_dir = Path(target_paths[0]).parent if target_paths else Path()
    result = []
    for e in edits:
        real_path = by_name.get(Path(e.path).name, str(target_dir / Path(e.path).name))
        result.append(e if real_path == e.path else e.model_copy(update={"path": real_path}))
    return result


async def _maybe_sync_docs(
    router: Router, sandbox: Sandbox, *,
    update_docs: bool, kb_path: str | None, changed_files: list[str], change_summary: str,
) -> tuple[list[str], str]:
    """Optional, best-effort mode. Does nothing unless the caller asks for
    update_docs=True and gives a kb_path; any failure inside docs_sync
    already comes back as a note instead of an exception (see
    docs_sync.py), so no separate try/except is needed here."""
    if not (update_docs and kb_path) or not changed_files:
        return [], ""
    result = await docs_sync.run(
        router, sandbox, kb_path=kb_path, changed_files=changed_files, change_summary=change_summary,
    )
    return result["applied"], result["note"]


async def _run_repro(cmd: list[str], workdir: Path, timeout_s: float = 60.0) -> tuple[bool, str]:
    """Runs the user's repro_command as-is, without interpreting it as
    pytest. The acceptance criterion is the exit code: 0 = passes.

    KNOWN LIMITATION (found live, not fixed yet): `cmd` runs with
    cwd=workdir, and workdir (baseline_dir/scratch in _run_fix) only
    contains files in flat "basename space" — never the real subfolder
    structure of target_paths. A repro_command that references a path
    with a subfolder the way the user would actually see it (e.g.
    `pytest playground/test_x.py` instead of `pytest test_x.py`) fails
    with "file or directory not found" even when the fix itself is
    correct — it's not a failure of the fix, it's that the repro_command
    can't find the file in the flattened layout. The correct fix would be
    for _run_fix to materialize its scratch dirs preserving real paths
    (not just basenames), but that touches the "basename space" convention
    shared by _run_new/_run_refactor/_run_fix — deliberately not done
    without supervision; it needs careful design and live verification."""
    import subprocess

    def _sync_run() -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return False, f"timeout after {timeout_s}s: {exc}"
        except OSError as exc:
            return False, f"could not run repro_command: {exc}"
        return proc.returncode == 0, compress_log(proc.stdout + proc.stderr)

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
) -> tuple[ConsensusCandidate | None, str | None]:
    # everything internal (base_files, edits, scratch dirs, the consensus
    # matrix) lives in flat "basename space", same as base_files' keys.
    # The translation to real destination paths happens ONE single time,
    # in _to_target_paths, right before the actual write — never mid
    # pipeline, because mixing both spaces there is what broke the final
    # write (seen failing in tests).
    content = "\n\n".join(f"--- {name} ---\n{c}" for name, c in base_files.items())
    prompt = _IMPLEMENT_PROMPT.format(spec=spec, content=content)
    try:
        raw = await router.coder(_WORKFLOW, prompt, temperature=0.8)
        data = extract_json_dict(raw)
        edits = _force_basename([FileEdit(**e) for e in data["edits"]])
        test_edits = _force_basename([FileEdit(**e) for e in data.get("test_edits", [])])
    except Exception as exc:  # noqa: BLE001 — a downed worker shouldn't take down the fan-out
        # the reason used to be discarded (timeout, 429, broken JSON,
        # missing field all became indistinguishable as "None") — forced
        # a manual trip to the ledger to find out which one it really was.
        return None, f"{worker_id}: {type(exc).__name__}: {exc}"[:300]
    return ConsensusCandidate(id=worker_id, model="tier-coder", edits=edits, test_edits=test_edits), None


async def _run_new(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
    *, update_docs: bool = False, kb_path: str | None = None,
) -> Manifest:
    base_files = await _read_base_files(target_paths)

    results = await asyncio.gather(*[
        _generate_candidate(router, f"w{i}", spec, target_paths, base_files)
        for i in range(1, _N_WORKERS + 1)
    ])
    candidates = [c for c, _ in results if c is not None]

    if not candidates:
        errors = "; ".join(e for _, e in results if e)
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.new, tests_status="red",
            summary=f"no tier-coder worker produced a valid candidate: {errors}",
            dry_run=config.dry_run,
        )

    sandbox = Sandbox(config)
    consensus = await run_consensus(
        _WORKFLOW, sandbox, base_files, candidates, test_command=_TEST_COMMAND,
    )

    if consensus.winner_id is None:
        # no candidate satisfied even one other candidate's tests — before
        # giving up, give the premium tier a shot at synthesis, the same
        # rescue _run_refactor/_run_fix already fall back to when THEIR
        # own winner-selection comes up empty. consensus.py has computed
        # exactly this signal (escalate_to_premium) since it was written,
        # but nothing ever read it — found investigating a report of
        # team_feature returning "no consensus" with no attempt at the
        # premium-tier rescue the design always intended for this case.
        first = candidates[0]
        outcome = await repair_loop(
            router, _WORKFLOW, sandbox, base_files, spec, first.edits + first.test_edits,
            f"no consensus: {len(candidates)} candidates disagreed enough that none "
            f"passed even one other candidate's tests (scores={consensus.scores})",
            test_command=_TEST_COMMAND,
        )
        if not outcome.success:
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.new, tests_status="red",
                summary=(
                    f"no consensus among {len(candidates)} candidates (scores={consensus.scores}), "
                    f"and the premium-tier rescue also failed "
                    f"({'stagnated' if outcome.stagnated else 'did not converge'}): {outcome.last_error[:300]}"
                ),
                dry_run=config.dry_run,
            )
        edits = outcome.final_edits
        tests_ok = True  # repair_loop already verified this (test_command=_TEST_COMMAND)
        blocking: list[CriticFinding] = []
        winner_label = "premium-rescue"
    else:
        winner = next(c for c in candidates if c.id == consensus.winner_id)
        edits = winner.edits + winner.test_edits
        winner_label = f"{winner.id}, score={consensus.scores.get(winner.id, 0):.2f}"

        try:
            critic_report = await critic_review(router, _WORKFLOW, spec, winner.edits)
            blocking = critic_report.blocking(Severity.high)
        except Exception:  # noqa: BLE001 — an unreachable critic must not crash a winner whose own tests already passed; proceed without the critique instead of losing the whole result
            blocking = []

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
            error_summary = f"own tests are red. {error_summary}".strip()

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
                    f"still failing after {len(outcome.iterations)} repairs "
                    f"({'stagnated' if outcome.stagnated else 'did not converge'}): "
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
            summary=f"verified but could not write to the sandbox: {exc}",
            dry_run=config.dry_run,
        )

    docs_changed, docs_note = await _maybe_sync_docs(
        router, sandbox, update_docs=update_docs, kb_path=kb_path,
        changed_files=changed, change_summary=f"team_feature kind=new: {spec}",
    )

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.new,
        files_changed=changed + docs_changed, tests_status="green",
        critic_findings_open=0,
        provider_used=provider_used,
        summary=(
            f"implemented with {len(candidates)} candidates (winner={winner_label})"
            + (f"\n\ndocs: {docs_note}" if docs_note else "")
        ),
        dry_run=config.dry_run,
    )


async def _run_refactor(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
    *, update_docs: bool = False, kb_path: str | None = None,
) -> Manifest:
    """kind=refactor: preserving behavior is the hard rule, not just one
    of them.

    1. Characterization tests of the CURRENT behavior must pass against
       the untouched code (if they don't, they get regenerated — a free
       check that the code was actually understood before touching it).
    2. Fan-out of refactors, ALL evaluated against the same fixed tests.
    3. Final re-verification before writing: no appeal.
    """
    base_files = await _read_base_files(target_paths)
    content = "\n\n".join(f"--- {n} ---\n{c}" for n, c in base_files.items())
    sandbox = Sandbox(config)

    char_edits: list[FileEdit] = []
    char_last_error = ""
    for _ in range(_MAX_CHAR_TEST_ATTEMPTS):
        try:
            raw = await router.coder(_WORKFLOW, _CHARACTERIZE_PROMPT.format(content=content), temperature=0.3)
            data = extract_json_dict(raw)
            candidate = _force_basename([FileEdit(**e) for e in data["test_edits"]])
        except Exception as exc:  # noqa: BLE001 — we retry, don't propagate
            char_last_error = f"{type(exc).__name__}: {exc}"[:300]
            continue
        result = await _verify_in_scratch(
            sandbox, base_files, candidate, test_command=_TEST_COMMAND,
            py_files=[e.path for e in candidate],
        )
        if result.tests_run > 0 and result.tests_passed == result.tests_run:
            char_edits = candidate
            break
        char_last_error = result.error_output or "generated characterization tests didn't pass green"

    if not char_edits:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="not_run",
            summary=(
                f"could not generate characterization tests that pass against "
                f"the current code after {_MAX_CHAR_TEST_ATTEMPTS} attempts — aborted without touching anything. "
                f"Last error: {char_last_error[:300]}"
            ),
            dry_run=config.dry_run,
        )

    async def _one(worker_id: str) -> tuple[ConsensusCandidate | None, str | None]:
        try:
            raw = await router.coder(_WORKFLOW, _REFACTOR_PROMPT.format(goal=spec, content=content), temperature=0.7)
            data = extract_json_dict(raw)
            edits = _force_basename([FileEdit(**e) for e in data["edits"]])
        except Exception as exc:  # noqa: BLE001 — a downed worker shouldn't take down the fan-out
            return None, f"{worker_id}: {type(exc).__name__}: {exc}"[:300]
        return ConsensusCandidate(id=worker_id, model="tier-coder", edits=edits, test_edits=[]), None

    results = await asyncio.gather(*[_one(f"w{i}") for i in range(1, _N_WORKERS + 1)])
    candidates = [c for c, _ in results if c is not None]
    if not candidates:
        errors = "; ".join(e for _, e in results if e)
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="red",
            summary=f"no worker produced a valid refactor: {errors}",
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
            "the refactor broke the characterization tests of the original behavior",
            test_command=_TEST_COMMAND,
        )
        if not outcome.success:
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="red",
                summary=(
                    f"no candidate preserved the original behavior, even after repair "
                    f"({'stagnated' if outcome.stagnated else 'did not converge'}): {outcome.last_error[:300]}"
                ),
                dry_run=config.dry_run,
            )
        final_edits = outcome.final_edits
    else:
        # hard rule repeated on purpose: re-verify the winner one last time
        # before writing, no exceptions, no shortcuts.
        final_check = await _verify_in_scratch(
            sandbox, base_files, winner.edits + char_edits, test_command=_TEST_COMMAND,
            py_files=[e.path for e in winner.edits] + [e.path for e in char_edits],
        )
        if not (final_check.tests_run > 0 and final_check.tests_passed == final_check.tests_run):
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="red",
                summary="automatic rejection: the candidate did not pass the final characterization re-verification",
                dry_run=config.dry_run,
            )
        final_edits = winner.edits + char_edits

    try:
        changed = sandbox.apply_edits(_to_target_paths(final_edits, target_paths))
    except (SandboxViolation, EditConflict) as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.refactor, tests_status="green",
            summary=f"verified but could not write to the sandbox: {exc}",
            dry_run=config.dry_run,
        )

    docs_changed, docs_note = await _maybe_sync_docs(
        router, sandbox, update_docs=update_docs, kb_path=kb_path,
        changed_files=changed, change_summary=f"team_feature kind=refactor: {spec}",
    )

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.refactor, files_changed=changed + docs_changed, tests_status="green",
        summary=(
            f"refactor applied, behavior preserved ({len(candidates)} candidates evaluated)"
            + (f"\n\ndocs: {docs_note}" if docs_note else "")
        ),
        dry_run=config.dry_run,
    )


async def _run_fix(
    router: Router, ledger: Ledger, config: Config, spec: str, target_paths: list[str],
    repro_command: str | None,
    *, update_docs: bool = False, kb_path: str | None = None,
) -> Manifest:
    """kind=fix: the user's `repro_command` is the source of truth, not
    something a model gets to rewrite. No real red->green on that command
    means no delivery."""
    if not repro_command:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="not_run",
            summary="kind=fix requires repro_command (a command that fails today and must exit with code 0)",
            dry_run=config.dry_run,
        )

    try:
        repro_argv = shlex.split(repro_command)
    except ValueError as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="not_run",
            summary=f"could not parse repro_command: {exc}", dry_run=config.dry_run,
        )

    base_files = await _read_base_files(target_paths)
    content = "\n\n".join(f"--- {n} ---\n{c}" for n, c in base_files.items())
    sandbox = Sandbox(config)

    with tempfile.TemporaryDirectory(prefix="team_fix_baseline_") as tmp:
        baseline_dir = Path(tmp)
        for rel, file_content in base_files.items():
            p = baseline_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(file_content, encoding="utf-8")
        baseline_ok, baseline_output = await _run_repro(repro_argv, baseline_dir)

    if baseline_ok:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="not_run",
            summary=(
                "repro_command already passes (code 0) against the untouched code — the bug "
                "isn't confirmed, aborted without changes. Check repro_command or target_paths."
            ),
            dry_run=config.dry_run,
        )

    localization = "(automatic localization not available)"
    try:
        raw = await router.context(_WORKFLOW, _LOCALIZE_PROMPT.format(bug=spec, content=content))
        data = extract_json_dict(raw)
        localization = "Suggested localization: " + "; ".join(
            f"{cand.get('path')}:{cand.get('line')} — {cand.get('justification', '')}"
            for cand in data.get("candidates", [])
        )
    except Exception:  # noqa: BLE001, S110 — purely informative, doesn't block the fix
        pass

    async def _one(worker_id: str) -> tuple[ConsensusCandidate | None, str | None]:
        prompt = _FIX_PROMPT.format(
            repro_command=repro_command, bug=spec, localization=localization, content=content,
        )
        try:
            raw = await router.coder(_WORKFLOW, prompt, temperature=0.6)
            data = extract_json_dict(raw)
            edits = _force_basename([FileEdit(**e) for e in data["edits"]])
        except Exception as exc:  # noqa: BLE001 — a downed worker shouldn't take down the fan-out
            return None, f"{worker_id}: {type(exc).__name__}: {exc}"[:300]
        return ConsensusCandidate(id=worker_id, model="tier-coder", edits=edits, test_edits=[]), None

    results = await asyncio.gather(*[_one(f"w{i}") for i in range(1, _N_WORKERS + 1)])
    candidates = [c for c, _ in results if c is not None]
    if not candidates:
        errors = "; ".join(e for _, e in results if e)
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="red",
            summary=f"no worker produced a valid patch: {errors}",
            dry_run=config.dry_run,
        )

    async def _check(edits: list[FileEdit]) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="team_fix_") as tmp:
            scratch = Path(tmp)
            for rel, file_content in base_files.items():
                p = scratch / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(file_content, encoding="utf-8")
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
            f"repro_command `{repro_command}` is still failing:\n{err_output[:1200]}\n{baseline_output[:500]}",
            test_command=repro_argv,
        )
        if not outcome.success:
            return Manifest(
                tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="red",
                summary=(
                    f"no patch fixed the bug, even after repair "
                    f"({'stagnated' if outcome.stagnated else 'did not converge'}): {outcome.last_error[:300]}"
                ),
                dry_run=config.dry_run,
            )
        final_edits = outcome.final_edits
    else:
        final_edits = winner.edits

    try:
        critic_report = await critic_review(router, _WORKFLOW, spec, final_edits)
        blocking = critic_report.blocking(Severity.high)
    except Exception:  # noqa: BLE001 — an unreachable critic must not crash a fix whose repro_command already passed; proceed without the critique instead of losing the whole result
        blocking = []
    provider_used = {"tier_premium": "agy" if router.premium.last_used != "fallback" else "fallback"}

    try:
        changed = sandbox.apply_edits(_to_target_paths(final_edits, target_paths))
    except (SandboxViolation, EditConflict) as exc:
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.fix, tests_status="green",
            provider_used=provider_used,
            summary=f"bug fixed and verified but could not write: {exc}",
            dry_run=config.dry_run,
        )

    docs_changed, docs_note = await _maybe_sync_docs(
        router, sandbox, update_docs=update_docs, kb_path=kb_path,
        changed_files=changed, change_summary=f"team_feature kind=fix: {spec}",
    )

    return Manifest(
        tool=_WORKFLOW, kind=FeatureKind.fix, files_changed=changed + docs_changed, tests_status="green",
        critic_findings_open=len(blocking), provider_used=provider_used,
        summary=(
            f"bug fixed: repro_command now exits with code 0 "
            f"({len(candidates)} patches evaluated)"
            + (f"\n\ndocs: {docs_note}" if docs_note else "")
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
    """kind=review: doesn't generate code. 3 parallel critique passes with
    different rubrics (correctness/security/simplicity), deduplicated."""
    base_files = await _read_base_files(target_paths)
    if not any(base_files.values()):
        return Manifest(
            tool=_WORKFLOW, kind=FeatureKind.review, tests_status="not_run",
            summary="none of the target_paths exist or have content to review",
            dry_run=config.dry_run,
        )

    edits = [FileEdit(path=name, search="", replace=content) for name, content in base_files.items()]

    async def _pass(name: str, focus: str) -> tuple[CriticReport, str | None]:
        try:
            return await critic_review(router, _WORKFLOW, spec or "general quality review", edits, focus=focus), None
        except Exception as exc:  # noqa: BLE001 — one downed rubric pass must not crash (or cancel, via gather) the other two
            return CriticReport(findings=[]), f"{name}: {type(exc).__name__}: {exc}"[:200]

    results = await asyncio.gather(*[
        _pass(name, focus) for name, focus in _REVIEW_RUBRICS.items()
    ])
    reports = [r for r, _ in results]
    pass_errors = [e for _, e in results if e]
    all_findings = [f for report in reports for f in report.findings]
    deduped = _dedupe_findings(all_findings)

    provider_used = {"tier_premium": "agy" if router.premium.last_used != "fallback" else "fallback"}
    if not deduped:
        summary = "review complete (3 passes: correctness/security/simplicity): no confirmed findings"
    else:
        lines = [
            f"[{f.severity}] {f.file}:{f.line or '?'} — {f.claim} (scenario: {f.failure_scenario})"
            for f in deduped[:15]
        ]
        summary = f"{len(deduped)} confirmed findings:\n" + "\n".join(lines)
    if pass_errors:
        summary += f"\n\n({len(pass_errors)}/3 review passes unavailable: {'; '.join(pass_errors)})"

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
    update_docs: bool = False,
    kb_path: str | None = None,
) -> Manifest:
    resolved_kind = kind or "new"

    path_error = _validate_target_paths(target_paths)
    if path_error:
        return Manifest(
            tool=_WORKFLOW, tests_status="not_run",
            summary=path_error, dry_run=config.dry_run,
        )

    if resolved_kind == "new":
        return await _run_new(
            router, ledger, config, spec, target_paths, update_docs=update_docs, kb_path=kb_path,
        )
    if resolved_kind == "refactor":
        return await _run_refactor(
            router, ledger, config, spec, target_paths, update_docs=update_docs, kb_path=kb_path,
        )
    if resolved_kind == "fix":
        return await _run_fix(
            router, ledger, config, spec, target_paths, repro_command,
            update_docs=update_docs, kb_path=kb_path,
        )
    if resolved_kind == "review":
        return await _run_review(router, ledger, config, spec, target_paths)

    return Manifest(
        tool=_WORKFLOW, tests_status="not_run",
        summary=f"unknown kind: {resolved_kind}. Valid: new, refactor, fix, review.",
        dry_run=config.dry_run,
    )
