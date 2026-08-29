from __future__ import annotations

from team_mcp.engine.ledger import Ledger
from team_mcp.workflows.ask import run as ask_run


class _MapOkReduceFailsRouter:
    """context() succeeds for the map phase (per-chunk digests) but
    raises for the reduce/synthesis call — real bug found live: that
    reduce call wasn't wrapped in try/except, so a downed tier-context
    backend crashed team_ask entirely even though useful per-chunk
    digests were already sitting right there."""

    async def context(self, workflow, prompt, temperature=0.2):
        if "Excerpts:" in prompt:
            raise TimeoutError("gateway didn't respond after 120.0s")
        return "the answer lives here"


async def test_run_survives_reduce_call_raising_and_falls_back_to_raw_digests(make_config, tmp_path):
    config = make_config(sandbox_roots=[tmp_path])
    ledger = Ledger(config)
    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n")

    manifest = await ask_run(
        _MapOkReduceFailsRouter(), ledger, config,
        question="what does this do?", scope_paths=[str(f)],
    )

    assert manifest.tests_status == "not_run"
    assert "synthesis unavailable" in manifest.summary
    assert "the answer lives here" in manifest.summary
