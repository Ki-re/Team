"""team_validate: GO/NO-GO verdict (plan Phase 8).

Deterministics rule: any of them in the red forces NO-GO without
consulting any model. Reuses what's already built instead of reinventing:
`ast.parse` (same criterion as engine/verify.py), pytest via
sys.executable (same pattern as feature.py::_TEST_COMMAND), `ask.run()`
as-is for requirement traceability (inherits the citation verification
from Phase 4), and `critic.review()` as-is for the architecture review
(same pattern as feature.py::_run_review).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.critic import review as critic_review
from team_mcp.engine.frontmatter import (
    check_dangling_links,
    check_stale,
    list_kb_entries,
    split_frontmatter,
)
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import FileEdit, Manifest
from team_mcp.engine.verify import compress_log
from team_mcp.providers.router import Router
from team_mcp.workflows import ask
from team_mcp.workflows import selftest as selftest_pipeline

_WORKFLOW = "team_validate"

# deliberately not exhaustive — this isn't a full secret scanner, just the
# most obvious and cheap-to-detect patterns.
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI-style / many providers
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"""(?i)password\s*=\s*['"][^'"]{8,}['"]"""),
]


def _collect_py_files(scope: Path) -> list[Path]:
    if scope.is_file():
        return [scope] if scope.suffix == ".py" else []
    return sorted(scope.rglob("*.py"))


def _check_syntax(py_files: list[Path]) -> tuple[bool, str]:
    errors = []
    for f in py_files:
        try:
            ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
        except SyntaxError as exc:
            errors.append(f"{f}: {exc}")
    return not errors, "; ".join(errors[:10])


def _check_tests(scope: Path) -> tuple[bool | None, str]:
    """None = there were no tests to run (doesn't block)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(scope)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"couldn't run pytest: {exc}"

    output = proc.stdout + proc.stderr
    if "no tests ran" in output.lower() or "no tests collected" in output.lower():
        return None, "no tests in scope"
    return proc.returncode == 0, compress_log(output, max_chars=2000)


def _check_secrets(files: list[Path]) -> tuple[bool, str]:
    hits = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in _SECRET_PATTERNS:
            for m in pattern.finditer(content):
                line = content[: m.start()].count("\n") + 1
                hits.append(f"{f}:{line}")
    return not hits, "; ".join(hits[:10])


def _check_git(scope: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(scope if scope.is_dir() else scope.parent), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "git not available"
    if proc.returncode != 0:
        return "scope is not a git repo (or is outside one) — skipped"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return f"{len(lines)} file(s) with uncommitted changes" if lines else "clean working tree"


def _check_lint(py_files: list[Path], scope: Path) -> str:
    if not py_files:
        return "no .py files to lint"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scope)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ruff not available: {exc}"
    return "no findings" if proc.returncode == 0 else proc.stdout[-1000:]


def _check_kb_frontmatter(kb_path: Path) -> tuple[bool, str]:
    """Plan Phase 12: blocking just like _check_syntax, but only for files
    that clearly intend to have frontmatter (start with `---`) — a random
    .md with no frontmatter isn't an error, and INDEX.md doesn't require
    one either."""
    errors = []
    for f in sorted(kb_path.rglob("*.md")):
        if f.name.upper() == "INDEX.MD":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if not text.lstrip().startswith("---"):
            continue
        fm, _ = split_frontmatter(text)
        if fm is None:
            errors.append(str(f))
    return not errors, "; ".join(errors[:10])


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    scope: str,
    spec_original: str | None = None,
    selftest: bool = False,
) -> Manifest:
    if selftest:
        report = await selftest_pipeline.run(router)
        return Manifest(
            tool=_WORKFLOW, tests_status="green" if report["all_ok"] else "red",
            summary=selftest_pipeline.render_summary(report), dry_run=config.dry_run,
        )

    scope_path = Path(scope)
    if not scope_path.exists():
        return Manifest(
            tool=_WORKFLOW, tests_status="not_run",
            summary=f"scope '{scope}' doesn't exist", dry_run=config.dry_run,
        )

    py_files = _collect_py_files(scope_path)
    all_files = [scope_path] if scope_path.is_file() else sorted(
        p for p in scope_path.rglob("*") if p.is_file()
    )

    syntax_ok, syntax_detail = _check_syntax(py_files)
    tests_ok, tests_detail = _check_tests(scope_path)
    secrets_ok, secrets_detail = _check_secrets(all_files)
    git_status = _check_git(scope_path)
    lint_detail = _check_lint(py_files, scope_path)

    blockers = []
    if not syntax_ok:
        blockers.append(f"syntax: {syntax_detail}")
    if tests_ok is False:
        blockers.append(f"tests in the red: {tests_detail[:500]}")
    if not secrets_ok:
        blockers.append(f"possible hardcoded secrets: {secrets_detail}")

    warnings = [f"lint: {lint_detail}"] if lint_detail != "no findings" else []
    warnings.append(f"git: {git_status}")

    # Plan Phase 12: if scope looks like a knowledge-base directory (has
    # INDEX.md), apply the same deterministic checks as docs_sync.py —
    # free, without consulting any model.
    kb_dir = scope_path if scope_path.is_dir() else None
    if kb_dir and (kb_dir / "INDEX.md").exists():
        kb_ok, kb_detail = _check_kb_frontmatter(kb_dir)
        if not kb_ok:
            blockers.append(f"invalid KB frontmatter: {kb_detail}")
        dangling = check_dangling_links(kb_dir)
        if dangling:
            warnings.append("KB, broken links: " + "; ".join(dangling[:10]))
        stale = check_stale(list_kb_entries(kb_dir))
        if stale:
            warnings.append("KB, stale entries: " + "; ".join(stale[:10]))

    if spec_original:
        try:
            ask_manifest = await ask.run(
                router, ledger, config,
                question=(
                    f"For each requirement in this spec, say whether it's "
                    f"implemented and where (path:line). Spec:\n{spec_original}"
                ),
                scope_paths=[str(scope_path)],
            )
            warnings.append(f"requirement traceability:\n{ask_manifest.summary}")
        except Exception as exc:  # noqa: BLE001 — informational, shouldn't sink validate
            warnings.append(f"requirement traceability unavailable: {exc}")

        try:
            edits = [
                FileEdit(path=str(f.relative_to(scope_path.parent if scope_path.is_file() else scope_path)), search="", replace=f.read_text(encoding="utf-8", errors="replace"))
                for f in py_files[:20]  # cap: architecture review doesn't need an entire huge repo
            ]
            critic_report = await critic_review(
                router, _WORKFLOW,
                f"Assess whether the code's architecture matches this spec:\n{spec_original}",
                edits,
                focus="Focus ONLY on architecture: does the code structure reflect what the spec asks for? Are pieces missing? Does anything contradict the requested design?",
            )
            if critic_report.findings:
                lines = [f"[{f.severity}] {f.file}: {f.claim}" for f in critic_report.findings[:10]]
                warnings.append("architecture review:\n" + "\n".join(lines))
        except Exception as exc:  # noqa: BLE001 — informational, shouldn't sink validate
            warnings.append(f"architecture review unavailable: {exc}")

    verdict = "NO-GO" if blockers else "GO"
    summary_lines = [f"verdict: {verdict}"]
    if blockers:
        summary_lines.append("blockers:\n- " + "\n- ".join(blockers))
    if warnings:
        summary_lines.append("warnings:\n- " + "\n- ".join(warnings))

    return Manifest(
        tool=_WORKFLOW,
        tests_status="red" if blockers else ("green" if tests_ok else "not_run"),
        critic_findings_open=sum(1 for w in warnings if w.startswith("architecture review")),
        summary="\n\n".join(summary_lines)[:4000],
        dry_run=config.dry_run,
    )
