from __future__ import annotations

from team_mcp.engine.ledger import Ledger
from team_mcp.workflows.task import run as task_run


class _RaisingRouter:
    """coder() always raises — simulates a downed tier-coder backend
    (e.g. a real ReadTimeout). Real bug found live: the coder call in
    team_task's retry loop wasn't wrapped in try/except, so this crashed
    the whole tool call uncaught instead of retrying/reporting cleanly,
    exactly the same bug class fixed in engine/repair.py."""

    async def coder(self, workflow, prompt, temperature=0.2):
        raise TimeoutError("gateway didn't respond after 120.0s")


async def test_run_survives_coder_call_raising_and_reports_escalation(make_config, tmp_path):
    config = make_config(sandbox_roots=[tmp_path])
    ledger = Ledger(config)
    target = tmp_path / "a.py"

    manifest = await task_run(
        _RaisingRouter(), ledger, config,
        instruction="add a docstring", target_path=str(target),
    )

    assert manifest.escalated_from == "task"
    assert manifest.tests_status == "red"
    assert "TimeoutError" in manifest.summary
