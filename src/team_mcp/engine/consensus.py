"""Cross-validation consensus (plan primitive #2).

Majority voting doesn't work with free-form code: two correct solutions
are almost never textually identical. Instead, each candidate contributes
implementation + tests; an N×N matrix is built by running impl_i against
tests_j. The winner is whoever best satisfies the other candidates'
suites — that's the signal it captured the shared intent, not just its
own interpretation.

Side effects detected for free, without spending tokens on arbitration:
- tests_j that NOBODY passes -> that test is hallucinated, discarded.
- tests_j that EVERYONE passes -> trivial, no signal, doesn't count toward the score.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from team_mcp.engine.sandbox import EditConflict, Sandbox
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
    """Loose formatting: collapses whitespace and strips simple line
    comments so two semantically identical candidates (differing only in
    indentation/comments) get detected as equal in the fast path."""
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
    test_pass_counts: dict[str, int] = {c.id: 0 for c in candidates}  # as tests_j, how many impls pass

    for impl in candidates:
        for tests in candidates:
            with tempfile.TemporaryDirectory(prefix="team_consensus_") as tmp:
                scratch = Path(tmp)
                for rel, content in base_files.items():
                    p = scratch / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")

                try:
                    sandbox.materialize_edits(impl.edits, scratch)
                    sandbox.materialize_edits(tests.test_edits, scratch)
                except EditConflict:
                    # a candidate whose `search` doesn't match cleanly
                    # against the base code (ambiguous or nonexistent)
                    # must not sink the WHOLE consensus matrix — this used
                    # to propagate and abort team_feature entirely with no
                    # manifest (seen failing live against a real README
                    # with repeated phrases). It counts as a cell with no
                    # tests run, same as a candidate that doesn't parse.
                    result = None
                else:
                    py_files = [e.path for e in impl.edits] + [e.path for e in tests.test_edits]
                    result = await verify_candidate(VerifyTarget(
                        candidate_id=f"{impl.id}x{tests.id}", workdir=scratch,
                        py_files=py_files, test_command=test_command, timeout_s=timeout_s,
                    ))

            cell = CrossMatrixCell(
                impl_id=impl.id, tests_id=tests.id,
                passed=result.tests_passed if result else 0,
                total=result.tests_run if result else 0,
            )
            matrix.append(cell)

            if cell.total > 0 and cell.passed == cell.total:
                test_pass_counts[tests.id] += 1

            if impl.id != tests.id:
                rate = (cell.passed / cell.total) if cell.total else 0.0
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
