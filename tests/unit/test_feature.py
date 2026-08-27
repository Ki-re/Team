from __future__ import annotations

from team_mcp.workflows.feature import _generate_candidate


class _RaisingRouter:
    """router.coder() siempre falla con una excepción concreta — simula lo
    que antes se perdía (timeout, 429, etc. quedaban todos como `None`)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def coder(self, workflow, prompt, temperature=0.2):
        raise self._exc


class _OkRouter:
    async def coder(self, workflow, prompt, temperature=0.2):
        return '{"edits": [{"path": "a.py", "search": "", "replace": "x = 1\\n"}], "test_edits": []}'


async def test_generate_candidate_propagates_real_error_on_failure():
    router = _RaisingRouter(TimeoutError("tras 120.0s"))
    candidate, error = await _generate_candidate(router, "w1", "spec", ["a.py"], {"a.py": ""})
    assert candidate is None
    assert error is not None
    assert "w1" in error
    assert "TimeoutError" in error
    assert "tras 120.0s" in error


async def test_generate_candidate_returns_candidate_and_no_error_on_success():
    router = _OkRouter()
    candidate, error = await _generate_candidate(router, "w1", "spec", ["a.py"], {"a.py": ""})
    assert error is None
    assert candidate is not None
    assert candidate.id == "w1"
    assert candidate.edits[0].path == "a.py"
