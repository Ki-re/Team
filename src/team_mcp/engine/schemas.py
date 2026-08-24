"""Contratos estructurados entre etapas del pipeline.

Todo lo que un worker devuelve se valida contra uno de estos modelos antes
de pasar a la siguiente etapa. Los modelos pequeños producen JSON roto con
frecuencia: la cadena de rescate (parse -> reparación con tier-fast -> fallo
duro) vive en engine/repair.py y usa estos esquemas como objetivo.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class FeatureKind(str, Enum):
    new = "new"
    refactor = "refactor"
    fix = "fix"
    review = "review"


class FileEdit(BaseModel):
    """Bloque search/replace estricto. Se valida por coincidencia EXACTA de
    `search` en el archivo antes de aplicar `replace`; nunca se sobrescribe
    el archivo entero."""

    path: str
    search: str
    replace: str


class CandidateSolution(BaseModel):
    """Salida de un worker de tier-coder en el fan-out inicial."""

    worker_id: str
    model: str
    edits: list[FileEdit]
    test_edits: list[FileEdit] = Field(default_factory=list)
    rationale: str = ""


class VerificationResult(BaseModel):
    """Resultado determinista de verify.py — nunca lo produce un modelo."""

    candidate_id: str
    parses: bool
    lint_ok: bool
    tests_run: int = 0
    tests_passed: int = 0
    error_output: str = ""

    @property
    def passes_gate(self) -> bool:
        return self.parses and self.lint_ok


class CrossMatrixCell(BaseModel):
    impl_id: str
    tests_id: str
    passed: int
    total: int


class ConsensusResult(BaseModel):
    winner_id: str | None
    scores: dict[str, float]
    matrix: list[CrossMatrixCell]
    discarded_tests: list[str] = Field(default_factory=list)  # tests que nadie pasa
    trivial_tests: list[str] = Field(default_factory=list)    # tests que todos pasan
    escalate_to_premium: bool = False


class CriticFinding(BaseModel):
    severity: Severity
    file: str
    line: int | None = None
    claim: str
    failure_scenario: str  # obligatorio: sin esto, el hallazgo se descarta
    suggested_fix: str = ""


class CriticReport(BaseModel):
    findings: list[CriticFinding] = Field(default_factory=list)

    def blocking(self, min_severity: Severity = Severity.high) -> list[CriticFinding]:
        order = [Severity.low, Severity.medium, Severity.high, Severity.critical]
        threshold = order.index(min_severity)
        return [f for f in self.findings if order.index(f.severity) >= threshold]


class RepairAttempt(BaseModel):
    iteration: int
    edits: list[FileEdit]
    based_on_error: str


class Manifest(BaseModel):
    """Lo único que Claude ve al final de un pipeline. Compacto a propósito."""

    tool: str
    kind: FeatureKind | None = None
    escalated_from: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    tests_status: str = "unknown"  # "green" | "red" | "not_run"
    critic_findings_open: int = 0
    tokens_used: dict[str, int] = Field(default_factory=dict)
    provider_used: dict[str, str] = Field(default_factory=dict)  # ej. tier_premium -> "agy"|"fallback"
    summary: str = ""
    dry_run: bool = False
