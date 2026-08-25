from __future__ import annotations

import team_mcp.engine.consensus as consensus_mod
from team_mcp.engine.consensus import ConsensusCandidate, _fast_path_winner, run_consensus
from team_mcp.engine.sandbox import Sandbox
from team_mcp.engine.schemas import FileEdit, VerificationResult


def _candidate(cid: str, body: str, test_body: str = "def test_x():\n    assert True\n") -> ConsensusCandidate:
    return ConsensusCandidate(
        id=cid, model="fake",
        edits=[FileEdit(path="sol.py", search="", replace=body)],
        test_edits=[FileEdit(path="test_sol.py", search="", replace=test_body)],
    )


def test_fast_path_winner_none_when_all_distinct():
    candidates = [_candidate("a", "x = 1\n"), _candidate("b", "x = 2\n")]
    assert _fast_path_winner(candidates) is None


def test_fast_path_winner_ignores_whitespace_and_comments():
    a = _candidate("a", "x = 1  # comentario\n")
    b = _candidate("b", "x = 1\n")
    assert _fast_path_winner([a, b]) == "a"


async def test_run_consensus_no_candidates_escalates(make_config):
    result = await run_consensus("wf", Sandbox(make_config()), {}, [])
    assert result.winner_id is None
    assert result.escalate_to_premium is True


async def test_run_consensus_fast_path_skips_matrix(make_config):
    sandbox = Sandbox(make_config())
    a = _candidate("a", "x = 1\n")
    b = _candidate("b", "x = 1\n")  # idéntico a 'a' tras normalizar
    result = await run_consensus("wf", sandbox, {}, [a, b])
    assert result.winner_id in ("a", "b")
    assert result.matrix == []  # nunca corrió la matriz N×N


async def test_run_consensus_picks_impl_that_passes_more_foreign_tests(make_config, monkeypatch):
    sandbox = Sandbox(make_config())
    candidates = [_candidate("a", "x = 1\n"), _candidate("b", "x = 2\n")]

    # 'a' pasa todo, 'b' nunca pasa nada -> 'a' debe ganar
    async def fake_verify(target):
        passed = 1 if target.candidate_id.startswith("a") else 0
        return VerificationResult(candidate_id=target.candidate_id, parses=True, lint_ok=True, tests_run=1, tests_passed=passed)

    monkeypatch.setattr(consensus_mod, "verify_candidate", fake_verify)
    result = await run_consensus("wf", sandbox, {}, candidates, test_command=["pytest"])
    assert result.winner_id == "a"
    assert result.scores["a"] > result.scores["b"]


async def test_run_consensus_discards_test_nobody_passes(make_config, monkeypatch):
    sandbox = Sandbox(make_config())
    candidates = [_candidate("a", "x = 1\n"), _candidate("b", "x = 2\n")]

    async def fake_verify(target):
        # nadie pasa nunca -> ambos test suites deberían marcarse descartadas
        return VerificationResult(candidate_id=target.candidate_id, parses=True, lint_ok=True, tests_run=1, tests_passed=0)

    monkeypatch.setattr(consensus_mod, "verify_candidate", fake_verify)
    result = await run_consensus("wf", sandbox, {}, candidates, test_command=["pytest"])
    assert set(result.discarded_tests) == {"a", "b"}
    assert result.winner_id is None
    assert result.escalate_to_premium is True


async def test_run_consensus_marks_trivial_test_that_everyone_passes(make_config, monkeypatch):
    sandbox = Sandbox(make_config())
    candidates = [_candidate("a", "x = 1\n"), _candidate("b", "x = 2\n")]

    async def fake_verify(target):
        return VerificationResult(candidate_id=target.candidate_id, parses=True, lint_ok=True, tests_run=1, tests_passed=1)

    monkeypatch.setattr(consensus_mod, "verify_candidate", fake_verify)
    result = await run_consensus("wf", sandbox, {}, candidates, test_command=["pytest"])
    assert set(result.trivial_tests) == {"a", "b"}
