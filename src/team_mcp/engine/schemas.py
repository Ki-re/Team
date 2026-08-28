"""Structured contracts between pipeline stages.

Everything a worker returns is validated against one of these models
before moving to the next stage. Small models produce broken JSON
frequently: the rescue chain (parse -> repair with tier-fast -> hard
failure) lives in engine/repair.py and uses these schemas as its target.
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
    """Strict search/replace block. Validated by an EXACT match of
    `search` in the file before applying `replace`; the whole file is
    never overwritten."""

    path: str
    search: str
    replace: str


class CandidateSolution(BaseModel):
    """Output of a tier-coder worker in the initial fan-out."""

    worker_id: str
    model: str
    edits: list[FileEdit]
    test_edits: list[FileEdit] = Field(default_factory=list)
    rationale: str = ""


class VerificationResult(BaseModel):
    """Deterministic result from verify.py — never produced by a model."""

    candidate_id: str
    parses: bool
    lint_ok: bool
    tests_run: int = 0
    tests_passed: int = 0
    error_output: str = ""

    @property
    def passes_gate(self) -> bool:
        # only "parses" is the hard, free gate, as the plan says. lint_ok
        # stays as an informational signal: a cosmetic ruff nit (import
        # order, blank line) shouldn't reject correct code — seen failing
        # exactly this way in real kind=refactor tests.
        return self.parses


class CrossMatrixCell(BaseModel):
    impl_id: str
    tests_id: str
    passed: int
    total: int


class ConsensusResult(BaseModel):
    winner_id: str | None
    scores: dict[str, float]
    matrix: list[CrossMatrixCell]
    discarded_tests: list[str] = Field(default_factory=list)  # tests nobody passes
    trivial_tests: list[str] = Field(default_factory=list)    # tests everyone passes
    escalate_to_premium: bool = False


class CriticFinding(BaseModel):
    severity: Severity
    file: str
    line: int | None = None
    claim: str
    failure_scenario: str  # required: without this, the finding is discarded
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
    """The only thing the orchestrating agent sees at the end of a
    pipeline. Deliberately compact."""

    tool: str
    kind: FeatureKind | None = None
    escalated_from: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    tests_status: str = "unknown"  # "green" | "red" | "not_run"
    critic_findings_open: int = 0
    tokens_used: dict[str, int] = Field(default_factory=dict)
    provider_used: dict[str, str] = Field(default_factory=dict)  # e.g. tier_premium -> "agy"|"fallback"
    summary: str = ""
    dry_run: bool = False
