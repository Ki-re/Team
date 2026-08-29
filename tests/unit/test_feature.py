from __future__ import annotations

import json

import team_mcp.engine.consensus as consensus_mod
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import FileEdit
from team_mcp.engine.verify import VerificationResult
from team_mcp.workflows import feature
from team_mcp.workflows.feature import _force_basename, _generate_candidate, _validate_target_paths


class _RaisingRouter:
    """router.coder() always fails with a concrete exception — simulates
    what used to get lost (timeout, 429, etc. all ended up as `None`)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def coder(self, workflow, prompt, temperature=0.2):
        raise self._exc


class _OkRouter:
    async def coder(self, workflow, prompt, temperature=0.2):
        return '{"edits": [{"path": "a.py", "search": "", "replace": "x = 1\\n"}], "test_edits": []}'


async def test_generate_candidate_propagates_real_error_on_failure():
    router = _RaisingRouter(TimeoutError("after 120.0s"))
    candidate, error = await _generate_candidate(router, "w1", "spec", ["a.py"], {"a.py": ""})
    assert candidate is None
    assert error is not None
    assert "w1" in error
    assert "TimeoutError" in error
    assert "after 120.0s" in error


async def test_generate_candidate_returns_candidate_and_no_error_on_success():
    router = _OkRouter()
    candidate, error = await _generate_candidate(router, "w1", "spec", ["a.py"], {"a.py": ""})
    assert error is None
    assert candidate is not None
    assert candidate.id == "w1"
    assert candidate.edits[0].path == "a.py"


def test_force_basename_strips_directories_from_model_output():
    # real bug found live: a kind=fix worker copied a path with a
    # subfolder from repro_command instead of using the basename,
    # breaking scratch verification (EditConflict "doesn't exist") before
    # ever reaching _to_target_paths, which only normalizes right before
    # the final write.
    edits = [
        FileEdit(path="playground/selfreview_bug.py", search="", replace="x = 1\n"),
        FileEdit(path="already_flat.py", search="", replace="y = 2\n"),
    ]
    result = _force_basename(edits)
    assert [e.path for e in result] == ["selfreview_bug.py", "already_flat.py"]


def test_force_basename_leaves_flat_edits_unchanged():
    edits = [FileEdit(path="a.py", search="", replace="x = 1\n")]
    result = _force_basename(edits)
    assert result[0] is edits[0]


# --- _validate_target_paths -------------------------------------------------


def test_validate_target_paths_rejects_a_directory(tmp_path):
    # real bug found live: Path.exists() is true for directories too, so
    # _read_base_files used to hand them straight to read_text(), which
    # raises (PermissionError on Windows, IsADirectoryError on POSIX)
    # uncaught anywhere upstream — team_feature crashed with no Manifest
    # at all instead of a clear error.
    d = tmp_path / "somedir"
    d.mkdir()
    error = _validate_target_paths([str(d)])
    assert error is not None
    assert d.name in error


def test_validate_target_paths_accepts_files_and_nonexistent_paths(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert _validate_target_paths([str(f), str(tmp_path / "new_file.py")]) is None


# --- _run_new: premium-tier rescue when consensus finds no winner ----------


class _FakePremium:
    last_used = "fallback"


class _RescueRouter:
    """coder() returns a different (but always valid, always-passing-test)
    candidate on every call, so the initial 3-way fan-out doesn't
    collapse into consensus.py's fast path (identical candidates would
    short-circuit the N×N matrix this test needs to exercise). Real
    verification (real subprocess pytest runs, via engine.repair's own
    verify_candidate, never mocked) is what actually makes repair_loop's
    rescue attempt succeed here — only consensus.py's verify_candidate is
    monkeypatched, to force the initial matrix to score everyone 0."""

    def __init__(self):
        self.premium = _FakePremium()
        self._coder_calls = 0

    async def coder(self, workflow, prompt, temperature=0.2):
        self._coder_calls += 1
        n = self._coder_calls
        # satisfies two different consumers with two different shapes:
        # feature.py's _generate_candidate reads edits+test_edits
        # separately, while repair.py's repair prompt expects every file
        # (impl AND test) flattened into "edits" alone — test_solution.py
        # is duplicated across both keys so this one fake response works
        # for the initial fan-out AND for repair_loop's rescue attempt.
        test_edit = {"path": "test_solution.py", "search": "", "replace": "def test_x():\n    assert True\n"}
        return json.dumps({
            "edits": [{"path": "a.py", "search": "", "replace": f"x = {n}\n"}, test_edit],
            "test_edits": [test_edit],
        })


async def test_run_new_falls_back_to_premium_rescue_when_consensus_finds_no_winner(make_config, monkeypatch, tmp_path):
    async def fake_verify(target):
        # every cross-validation cell "fails" -> every score is 0.0 ->
        # consensus.winner_id is None, the exact scenario that used to
        # just give up with "no consensus... needs manual synthesis".
        return VerificationResult(
            candidate_id=target.candidate_id, parses=True, lint_ok=True, tests_run=1, tests_passed=0,
        )

    monkeypatch.setattr(consensus_mod, "verify_candidate", fake_verify)

    router = _RescueRouter()
    config = make_config(sandbox_roots=[tmp_path])
    ledger = Ledger(config)
    target = str(tmp_path / "a.py")

    manifest = await feature.run(router, ledger, config, spec="add a function", target_paths=[target])

    assert manifest.tests_status == "green"
    assert "premium-rescue" in manifest.summary
    assert manifest.files_changed
    assert (tmp_path / "a.py").exists()


# --- _run_review: one downed rubric pass must not crash the other two -----


class _OneRubricFailsRouter:
    """premium_review() (what critic.review() calls) raises for the
    "security" rubric specifically, succeeds for the other two — real bug
    found live: the 3 review passes ran under a bare asyncio.gather with
    no return_exceptions, so one downed critic call cancelled the other
    two and crashed kind=review entirely instead of reporting 2/3 passes."""

    last_used = "fallback"

    async def premium_review(self, workflow, prompt):
        if "path traversal" in prompt.lower():  # unique to the "security" rubric's own text
            raise TimeoutError("gateway didn't respond after 120.0s")
        return '{"findings": []}'


async def test_run_review_survives_one_rubric_pass_raising(make_config, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n")
    config = make_config(sandbox_roots=[tmp_path])
    ledger = Ledger(config)
    router = _OneRubricFailsRouter()
    router.premium = router  # feature.py reads router.premium.last_used

    manifest = await feature.run(
        router, ledger, config, spec="review this", target_paths=[str(f)], kind="review",
    )

    assert manifest.tests_status == "not_run"
    assert "2/3 review passes unavailable" not in manifest.summary  # only 1 of 3 failed
    assert "1/3 review passes unavailable" in manifest.summary
    assert "TimeoutError" in manifest.summary
