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
    a = _candidate("a", "x = 1  # comment\n")
    b = _candidate("b", "x = 1\n")
    assert _fast_path_winner([a, b]) == "a"


async def test_run_consensus_no_candidates_escalates(make_config):
    result = await run_consensus("wf", Sandbox(make_config()), {}, [])
    assert result.winner_id is None
    assert result.escalate_to_premium is True


async def test_run_consensus_fast_path_skips_matrix(make_config):
    sandbox = Sandbox(make_config())
    a = _candidate("a", "x = 1\n")
    b = _candidate("b", "x = 1\n")  # identical to 'a' after normalization
    result = await run_consensus("wf", sandbox, {}, [a, b])
    assert result.winner_id in ("a", "b")
    assert result.matrix == []  # never ran the N×N matrix


async def test_run_consensus_picks_impl_that_passes_more_foreign_tests(make_config, monkeypatch):
    sandbox = Sandbox(make_config())
    candidates = [_candidate("a", "x = 1\n"), _candidate("b", "x = 2\n")]

    # 'a' passes everything, 'b' never passes anything -> 'a' must win
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
        # nobody ever passes -> both test suites should be marked discarded
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


async def test_run_consensus_survives_edit_conflict_without_raising(make_config, monkeypatch):
    # real bug found live (Phase 14): a candidate whose `search` doesn't
    # match cleanly against base_files (here: it matches TWICE, ambiguous)
    # made sandbox.materialize_edits raise EditConflict UNCAUGHT inside
    # the N×N loop, aborting team_feature entirely with no manifest --
    # seen failing against a real README with repeated phrases.
    sandbox = Sandbox(make_config())
    base_files = {"readme.md": "hello world\nhello world\n"}

    bad = ConsensusCandidate(
        id="bad", model="fake",
        edits=[FileEdit(path="readme.md", search="hello world", replace="goodbye world")],
        test_edits=[FileEdit(path="test_x.py", search="", replace="def test_x():\n    assert True\n")],
    )
    good = ConsensusCandidate(
        id="good", model="fake",
        edits=[FileEdit(path="readme.md", search="", replace="hello world changed\n")],
        test_edits=[FileEdit(path="test_x.py", search="", replace="def test_x():\n    assert True\n")],
    )

    async def fake_verify(target):
        passed = 1 if target.candidate_id.startswith("good") else 0
        return VerificationResult(candidate_id=target.candidate_id, parses=True, lint_ok=True, tests_run=1, tests_passed=passed)

    monkeypatch.setattr(consensus_mod, "verify_candidate", fake_verify)

    result = await run_consensus("wf", sandbox, base_files, [bad, good], test_command=["pytest"])

    assert result.winner_id == "good"
    assert result.scores["bad"] == 0.0
