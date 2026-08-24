"""Consenso por validación cruzada (primitiva #2 del plan).

El voto por mayoría no funciona con código libre: dos soluciones correctas
casi nunca son textualmente iguales. En su lugar, cada candidato aporta
implementación + tests; se construye una matriz N×N ejecutando impl_i contra
tests_j. Gana quien mejor satisface las suites ajenas — es la señal de que
capturó la intención compartida, no solo la propia interpretación.

Efectos secundarios detectados gratis, sin gastar tokens en arbitraje:
- tests_j que NADIE pasa -> ese test está alucinado, se descarta.
- tests_j que TODOS pasan -> trivial, no aporta señal, no cuenta en el score.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from team_mcp.engine.sandbox import Sandbox
from team_mcp.engine.schemas import ConsensusResult, CrossMatrixCell, FileEdit
from team_mcp.engine.verify import VerifyTarget, verify_candidate

_ESCALATE_THRESHOLD = 0.6


class ConsensusCandidate:
    def __init__(self, id: str, model: str, edits: list[FileEdit], test_edits: list[FileEdit]):
        self.id = id
        self.model = model
        self.edits = edits
        self.test_edits = test_edits


def _normalize_for_hash(edits: list[FileEdit]) -> str:
    """Formateo laxo: colapsa espacios y quita comentarios de línea simples
    para que dos candidatos semánticamente idénticos (variando solo
    indentación/comentarios) se detecten como iguales en el fast path."""
    parts = []
    for e in sorted(edits, key=lambda x: x.path):
        body = re.sub(r"#.*", "", e.replace)
        body = re.sub(r"\s+", " ", body).strip()
        parts.append(f"{e.path}:{body}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _fast_path_winner(candidates: list[ConsensusCandidate]) -> str | None:
    seen: dict[str, str] = {}
    counts: dict[str, int] = {}
    for c in candidates:
        h = _normalize_for_hash(c.edits)
        seen[c.id] = h
        counts[h] = counts.get(h, 0) + 1
    for cid, h in seen.items():
        if counts[h] >= 2:
            return cid
    return None


async def run_consensus(
    workflow: str,
    sandbox: Sandbox,
    base_files: dict[str, str],
    candidates: list[ConsensusCandidate],
    *,
    test_command: list[str] | None = None,
    timeout_s: float = 60.0,
) -> ConsensusResult:
    if not candidates:
        return ConsensusResult(winner_id=None, scores={}, matrix=[], escalate_to_premium=True)

    fast_winner = _fast_path_winner(candidates)
    if fast_winner is not None:
        return ConsensusResult(
            winner_id=fast_winner,
            scores={c.id: (1.0 if c.id == fast_winner else 0.0) for c in candidates},
            matrix=[],
            escalate_to_premium=False,
        )

    matrix: list[CrossMatrixCell] = []
    raw_scores: dict[str, list[float]] = {c.id: [] for c in candidates}
    test_pass_counts: dict[str, int] = {c.id: 0 for c in candidates}  # como tests_j, cuántos impls pasan

    for impl in candidates:
        for tests in candidates:
            with tempfile.TemporaryDirectory(prefix="team_consensus_") as tmp:
                scratch = Path(tmp)
                for rel, content in base_files.items():
                    p = scratch / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")

                sandbox.materialize_edits(impl.edits, scratch)
                sandbox.materialize_edits(tests.test_edits, scratch)

                py_files = [e.path for e in impl.edits] + [e.path for e in tests.test_edits]
                result = await verify_candidate(VerifyTarget(
                    candidate_id=f"{impl.id}x{tests.id}", workdir=scratch,
                    py_files=py_files, test_command=test_command, timeout_s=timeout_s,
                ))

            cell = CrossMatrixCell(
                impl_id=impl.id, tests_id=tests.id,
                passed=result.tests_passed, total=result.tests_run,
            )
            matrix.append(cell)

            if result.tests_run > 0 and result.tests_passed == result.tests_run:
                test_pass_counts[tests.id] += 1

            if impl.id != tests.id:
                rate = (result.tests_passed / result.tests_run) if result.tests_run else 0.0
                raw_scores[impl.id].append(rate)

    scores = {cid: (sum(v) / len(v) if v else 0.0) for cid, v in raw_scores.items()}
    discarded_tests = [c.id for c in candidates if test_pass_counts[c.id] == 0]
    trivial_tests = [c.id for c in candidates if test_pass_counts[c.id] == len(candidates)]

    if not scores or max(scores.values()) == 0.0:
        return ConsensusResult(
            winner_id=None, scores=scores, matrix=matrix,
            discarded_tests=discarded_tests, trivial_tests=trivial_tests,
            escalate_to_premium=True,
        )

    best_score = max(scores.values())
    top = [cid for cid, s in scores.items() if s == best_score]
    winner = top[0]
    escalate = best_score < _ESCALATE_THRESHOLD or len(top) > 1

    return ConsensusResult(
        winner_id=winner, scores=scores, matrix=matrix,
        discarded_tests=discarded_tests, trivial_tests=trivial_tests,
        escalate_to_premium=escalate,
    )
