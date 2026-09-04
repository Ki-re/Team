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


class _BrokenThenRepairedRouter:
    """coder() returns malformed JSON on its first (and only, since the
    repair should succeed) call; fast() is the tier-fast repair call.
    team_task only gets 2 attempts total, so recovering via a cheap
    repair instead of burning a full attempt matters even more here than
    in team_feature's 3-way fan-out."""

    def __init__(self):
        self.coder_calls = 0

    async def coder(self, workflow, prompt, temperature=0.2):
        self.coder_calls += 1
        return '{"search": "", "replace": "x = 1\\n",}'  # trailing comma

    async def fast(self, workflow, prompt, temperature=0.0):
        return '{"search": "", "replace": "x = 1\\n"}'


async def test_run_recovers_via_tier_fast_repair_without_burning_a_retry(make_config, tmp_path):
    config = make_config(sandbox_roots=[tmp_path])
    ledger = Ledger(config)
    target = tmp_path / "a.py"
    router = _BrokenThenRepairedRouter()

    manifest = await task_run(
        router, ledger, config,
        instruction="add x = 1", target_path=str(target),
    )

    assert manifest.tests_status in ("green", "not_run")
    assert manifest.escalated_from is None
    assert router.coder_calls == 1  # recovered via repair, no second attempt needed
    assert target.read_text() == "x = 1\n"
