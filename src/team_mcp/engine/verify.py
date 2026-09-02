"""Deterministic verification: always before spending tokens judging code.

A candidate that doesn't parse or doesn't lint gets discarded for free,
without involving any model. This is the first thing that runs on every
CandidateSolution from the tier-coder fan-out.
"""

from __future__ import annotations

import ast
import asyncio
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from team_mcp.engine.schemas import VerificationResult

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def compress_log(text: str, max_chars: int = 3000) -> str:
    """Strips ANSI color codes and collapses runs of identical consecutive
    lines (progress bars, repeated retry noise) before truncating to the
    tail, so the character budget goes to the actual failure/traceback
    instead of whatever happened to be last. A cheap, regex-only version
    of what OmniRoute calls its "RTK" log-compression engine — every
    subprocess-output truncation site in this codebase used to just slice
    the raw tail. Deliberately skips OmniRoute's prose ("Caveman") half:
    team-mcp's prompts are already terse templates plus real code, not
    verbose human prose, so a filler-word pass has nothing worth cutting
    there and risks mangling meaning for no real savings."""
    text = _ANSI_RE.sub("", text)
    deduped: list[str] = []
    last: str | None = None
    for line in text.splitlines():
        if line == last:
            continue
        last = line
        deduped.append(line)
    return "\n".join(deduped)[-max_chars:]


@dataclass
class VerifyTarget:
    candidate_id: str
    workdir: Path
    py_files: list[str]
    test_command: list[str] | None = None  # e.g. ["pytest", "-q"]
    timeout_s: float = 60.0


def _parses(py_files: list[str], workdir: Path) -> tuple[bool, str]:
    for rel in py_files:
        path = workdir / rel
        if not path.exists() or path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            return False, f"{rel}: {exc}"
    return True, ""


def _lint(workdir: Path, timeout_s: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],  # never depend on "ruff" being on PATH
            cwd=workdir, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return True, f"ruff unavailable/timeout, skipped: {exc}"
    return proc.returncode == 0, compress_log(proc.stdout, max_chars=2000)


async def verify_candidate(target: VerifyTarget) -> VerificationResult:
    parses, parse_err = _parses(target.py_files, target.workdir)
    if not parses:
        return VerificationResult(
            candidate_id=target.candidate_id, parses=False, lint_ok=False,
            error_output=parse_err,
        )

    lint_ok, lint_out = await asyncio.to_thread(_lint, target.workdir, target.timeout_s)

    tests_run = tests_passed = 0
    test_output = ""
    if target.test_command:
        tests_run, tests_passed, test_output = await asyncio.to_thread(
            _run_tests, target.test_command, target.workdir, target.timeout_s
        )

    return VerificationResult(
        candidate_id=target.candidate_id,
        parses=True,
        lint_ok=lint_ok,
        tests_run=tests_run,
        tests_passed=tests_passed,
        error_output=(lint_out if not lint_ok else "") + test_output,
    )


def _run_tests(cmd: list[str], workdir: Path, timeout_s: float) -> tuple[int, int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return 0, 0, f"timeout after {timeout_s}s: {exc}"

    output = proc.stdout + proc.stderr
    run, passed = _parse_pytest_summary(output)
    return run, passed, compress_log(output)


def _parse_pytest_summary(output: str) -> tuple[int, int]:
    """Extracts counters from pytest's final summary line (best-effort)."""
    import re

    passed = len(re.findall(r"(?m)^PASSED", output)) or 0
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    failed = 0
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    return passed + failed, passed
