from __future__ import annotations

from team_mcp.engine.repair import (
    _edits_signature,
    _force_basename,
    _materialize_to_dict,
    repair_loop,
)
from team_mcp.engine.sandbox import Sandbox
from team_mcp.engine.schemas import FileEdit


def test_force_basename_strips_directories_from_model_output():
    # same real bug as in feature.py: a repair worker copied a path with
    # a subfolder from the literal error instead of the basename, which
    # crashed scratch verification with an EditConflict "doesn't exist".
    edits = [
        FileEdit(path="playground/foo.py", search="", replace="x = 1\n"),
        FileEdit(path="already_flat.py", search="", replace="y = 2\n"),
    ]
    result = _force_basename(edits)
    assert [e.path for e in result] == ["foo.py", "already_flat.py"]


def test_materialize_to_dict_full_file_replace():
    base = {"a.py": "old content\n"}
    edits = [FileEdit(path="a.py", search="", replace="new content\n")]
    assert _materialize_to_dict(base, edits) == {"a.py": "new content\n"}


def test_materialize_to_dict_partial_search_replace():
    base = {"a.py": "def f():\n    return 1\n"}
    edits = [FileEdit(path="a.py", search="return 1", replace="return 2")]
    assert _materialize_to_dict(base, edits) == {"a.py": "def f():\n    return 2\n"}


def test_materialize_to_dict_falls_back_to_replace_when_search_does_not_match():
    # exactly the real bug that motivated repair.py: a search that doesn't
    # match must not crash the prompt render, it falls back to best-effort
    base = {"a.py": "unrelated content\n"}
    edits = [FileEdit(path="a.py", search="not present anywhere", replace="fallback content\n")]
    assert _materialize_to_dict(base, edits) == {"a.py": "fallback content\n"}


def test_materialize_to_dict_new_file_not_in_base():
    base: dict[str, str] = {}
    edits = [FileEdit(path="new.py", search="", replace="x = 1\n")]
    assert _materialize_to_dict(base, edits) == {"new.py": "x = 1\n"}


def test_materialize_to_dict_preserves_untouched_files():
    base = {"a.py": "a content\n", "b.py": "b content\n"}
    edits = [FileEdit(path="a.py", search="", replace="a changed\n")]
    result = _materialize_to_dict(base, edits)
    assert result["a.py"] == "a changed\n"
    assert result["b.py"] == "b content\n"


def test_edits_signature_stable_regardless_of_order():
    a = [FileEdit(path="a.py", search="", replace="x"), FileEdit(path="b.py", search="", replace="y")]
    b = [FileEdit(path="b.py", search="", replace="y"), FileEdit(path="a.py", search="", replace="x")]
    assert _edits_signature(a) == _edits_signature(b)


def test_edits_signature_changes_with_content():
    a = [FileEdit(path="a.py", search="", replace="x")]
    b = [FileEdit(path="a.py", search="", replace="y")]
    assert _edits_signature(a) != _edits_signature(b)


class _FakeRouter:
    """coder() always returns the same broken response -> stagnation
    within 2 iterations (no need to exhaust max_iterations to test
    escalation to agy). premium_review() is configurable per test."""

    def __init__(self, coder_response: str, premium_response: str | None):
        self._coder_response = coder_response
        self._premium_response = premium_response
        self.premium_calls = 0

    async def coder(self, workflow, prompt, temperature=0.2):
        return self._coder_response

    async def premium_review(self, workflow, prompt):
        self.premium_calls += 1
        if self._premium_response is None:
            raise RuntimeError("agy unavailable in this test")
        return self._premium_response


_BROKEN_JSON = '{"edits": [{"path": "a.py", "replace": "def f(:\\n"}]}'  # syntax error
_WORKING_JSON = '{"edits": [{"path": "a.py", "replace": "def f():\\n    return 1\\n"}]}'


async def test_repair_loop_succeeds_on_first_tier_coder_attempt(make_config):
    sandbox = Sandbox(make_config())
    router = _FakeRouter(coder_response=_WORKING_JSON, premium_response=None)
    outcome = await repair_loop(
        router, "wf", sandbox, {"a.py": "broken\n"}, "spec",
        [FileEdit(path="a.py", search="", replace="broken\n")], "initial error",
    )
    assert outcome.success is True
    assert router.premium_calls == 0  # no need to escalate


async def test_repair_loop_escalates_to_premium_after_tier_coder_stagnates(make_config):
    sandbox = Sandbox(make_config())
    router = _FakeRouter(coder_response=_BROKEN_JSON, premium_response=_WORKING_JSON)
    outcome = await repair_loop(
        router, "wf", sandbox, {"a.py": "broken\n"}, "spec",
        [FileEdit(path="a.py", search="", replace="broken\n")], "initial error",
        max_iterations=3,
    )
    assert outcome.success is True
    assert router.premium_calls == 1
    assert outcome.iterations[-1].based_on_error == "agy"


async def test_repair_loop_fails_when_premium_also_fails(make_config):
    sandbox = Sandbox(make_config())
    router = _FakeRouter(coder_response=_BROKEN_JSON, premium_response=None)
    outcome = await repair_loop(
        router, "wf", sandbox, {"a.py": "broken\n"}, "spec",
        [FileEdit(path="a.py", search="", replace="broken\n")], "initial error",
        max_iterations=3,
    )
    assert outcome.success is False
    assert router.premium_calls == 1  # attempted, but agy also failed (or wasn't available)


async def test_repair_loop_skips_premium_when_disabled(make_config):
    sandbox = Sandbox(make_config())
    router = _FakeRouter(coder_response=_BROKEN_JSON, premium_response=_WORKING_JSON)
    outcome = await repair_loop(
        router, "wf", sandbox, {"a.py": "broken\n"}, "spec",
        [FileEdit(path="a.py", search="", replace="broken\n")], "initial error",
        max_iterations=3, use_premium_fallback=False,
    )
    assert outcome.success is False
    assert router.premium_calls == 0
