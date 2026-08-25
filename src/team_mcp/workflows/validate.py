"""team_validate: veredicto GO/NO-GO (Fase 8 del plan).

Los deterministas mandan: cualquiera en rojo fuerza NO-GO sin consultar a
ningún modelo. Reutiliza lo ya construido en vez de reinventar: `ast.parse`
(mismo criterio que engine/verify.py), pytest vía sys.executable (mismo
patrón que feature.py::_TEST_COMMAND), `ask.run()` tal cual para
trazabilidad de requisitos (hereda la verificación de citas de la Fase 4),
y `critic.review()` tal cual para la revisión de arquitectura (mismo
patrón que feature.py::_run_review).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.critic import review as critic_review
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import FileEdit, Manifest
from team_mcp.providers.router import Router
from team_mcp.workflows import ask
from team_mcp.workflows import selftest as selftest_pipeline

_WORKFLOW = "team_validate"

# heurística no exhaustiva a propósito — no es un scanner de secretos
# completo, solo los patrones más obvios y baratos de detectar.
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI-style / muchos proveedores
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
    """None = no había tests que correr (no bloquea)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(scope)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"no se pudo ejecutar pytest: {exc}"

    output = proc.stdout + proc.stderr
    if "no tests ran" in output.lower() or "no tests collected" in output.lower():
        return None, "sin tests en el scope"
    return proc.returncode == 0, output[-2000:]


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
        return "git no disponible"
    if proc.returncode != 0:
        return "scope no es un repo git (o está fuera de uno) — se omite"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return f"{len(lines)} archivo(s) con cambios sin commitear" if lines else "working tree limpio"


def _check_lint(py_files: list[Path], scope: Path) -> str:
    if not py_files:
        return "sin archivos .py que lintar"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(scope)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ruff no disponible: {exc}"
    return "sin hallazgos" if proc.returncode == 0 else proc.stdout[-1000:]


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
            summary=f"scope '{scope}' no existe", dry_run=config.dry_run,
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
        blockers.append(f"sintaxis: {syntax_detail}")
    if tests_ok is False:
        blockers.append(f"tests en rojo: {tests_detail[:500]}")
    if not secrets_ok:
        blockers.append(f"posibles secretos hardcodeados: {secrets_detail}")

    warnings = [f"lint: {lint_detail}"] if lint_detail != "sin hallazgos" else []
    warnings.append(f"git: {git_status}")

    if spec_original:
        try:
            ask_manifest = await ask.run(
                router, ledger, config,
                question=(
                    f"Para cada requisito de esta spec, indica si está implementado y "
                    f"dónde (ruta:línea). Spec:\n{spec_original}"
                ),
                scope_paths=[str(scope_path)],
            )
            warnings.append(f"trazabilidad de requisitos:\n{ask_manifest.summary}")
        except Exception as exc:  # noqa: BLE001 — informativo, no debe tumbar el validate
            warnings.append(f"trazabilidad de requisitos no disponible: {exc}")

        try:
            edits = [
                FileEdit(path=str(f.relative_to(scope_path.parent if scope_path.is_file() else scope_path)), search="", replace=f.read_text(encoding="utf-8", errors="replace"))
                for f in py_files[:20]  # tope: revisión de arquitectura no necesita todo un repo enorme
            ]
            report = await critic_review(
                router, _WORKFLOW,
                f"Evalúa si la arquitectura del código coincide con esta spec:\n{spec_original}",
                edits,
                focus="Enfócate SOLO en arquitectura: ¿la estructura del código refleja lo que pide la spec? ¿faltan piezas? ¿hay algo que contradiga el diseño pedido?",
            )
            if report.findings:
                lines = [f"[{f.severity}] {f.file}: {f.claim}" for f in report.findings[:10]]
                warnings.append("revisión de arquitectura:\n" + "\n".join(lines))
        except Exception as exc:  # noqa: BLE001 — informativo, no debe tumbar el validate
            warnings.append(f"revisión de arquitectura no disponible: {exc}")

    verdict = "NO-GO" if blockers else "GO"
    summary_lines = [f"veredicto: {verdict}"]
    if blockers:
        summary_lines.append("bloqueantes:\n- " + "\n- ".join(blockers))
    if warnings:
        summary_lines.append("avisos:\n- " + "\n- ".join(warnings))

    return Manifest(
        tool=_WORKFLOW,
        tests_status="red" if blockers else ("green" if tests_ok else "not_run"),
        critic_findings_open=sum(1 for w in warnings if w.startswith("revisión de arquitectura")),
        summary="\n\n".join(summary_lines)[:4000],
        dry_run=config.dry_run,
    )
