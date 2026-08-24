"""Verificación determinista: siempre antes de gastar tokens en juzgar código.

Un candidato que no parsea o no lint-a se descarta gratis, sin involucrar a
ningún modelo. Esto es lo primero que corre sobre cada CandidateSolution del
fan-out de tier-coder.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

from team_mcp.engine.schemas import VerificationResult


@dataclass
class VerifyTarget:
    candidate_id: str
    workdir: Path
    py_files: list[str]
    test_command: list[str] | None = None  # ej. ["pytest", "-q"]
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
            ["ruff", "check", "."],
            cwd=workdir, capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return True, f"ruff no disponible/timeout, se omite: {exc}"
    return proc.returncode == 0, proc.stdout[-2000:]


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
        return 0, 0, f"timeout tras {timeout_s}s: {exc}"

    output = proc.stdout + proc.stderr
    run, passed = _parse_pytest_summary(output)
    return run, passed, output[-3000:]


def _parse_pytest_summary(output: str) -> tuple[int, int]:
    """Extrae contadores de la línea final de pytest (best-effort)."""
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
