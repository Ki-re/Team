from __future__ import annotations

import team_mcp.workflows.epic as epic_mod
from team_mcp.engine.ledger import Ledger, SpendEvent
from team_mcp.engine.schemas import Manifest
from team_mcp.workflows.epic import _validate_plan, run


def test_validate_plan_accepts_valid_dag():
    plan = [
        {"id": "a", "spec": "s"},
        {"id": "b", "spec": "s", "depends_on": ["a"]},
    ]
    assert _validate_plan(plan) is None


def test_validate_plan_rejects_duplicate_ids():
    plan = [{"id": "a", "spec": "s"}, {"id": "a", "spec": "s"}]
    assert "duplicate" in _validate_plan(plan)


def test_validate_plan_rejects_missing_dependency():
    plan = [{"id": "a", "spec": "s", "depends_on": ["ghost"]}]
    assert "ghost" in _validate_plan(plan)


def test_validate_plan_rejects_node_without_required_fields():
    plan = [{"id": "a"}]  # missing 'spec'
    assert _validate_plan(plan) is not None


class _FakeRouter:
    """Not actually used: feature.run gets monkeypatched, but epic.run
    still builds/passes the router — a stub is enough."""


def _make_fake_feature_run(call_log: list[str], *, fail_ids: set[str] = frozenset(), tokens_per_call: int = 100):
    async def fake_run(router, ledger, config, *, spec, target_paths, kind=None, repro_command=None):
        node_id = spec  # tests use spec=node id for simplicity
        call_log.append(node_id)
        ledger.record(SpendEvent(
            workflow="team_feature", tier="tier-coder", model="fake",
            tokens_in=tokens_per_call, tokens_out=0, latency_ms=1.0, ok=True,
        ))
        status = "red" if node_id in fail_ids else "green"
        return Manifest(tool="team_feature", files_changed=[f"{node_id}.py"], tests_status=status)

    return fake_run


async def test_run_executes_independent_nodes_and_then_dependent(make_config, monkeypatch):
    config = make_config()
    ledger = Ledger(config)
    call_log: list[str] = []
    monkeypatch.setattr(epic_mod.feature, "run", _make_fake_feature_run(call_log))

    plan = [
        {"id": "a", "spec": "a", "target_paths": []},
        {"id": "b", "spec": "b", "target_paths": []},
        {"id": "c", "spec": "c", "target_paths": [], "depends_on": ["a", "b"]},
    ]
    manifest = await run(_FakeRouter(), ledger, config, plan=plan, budget=100_000)

    assert call_log.index("c") > call_log.index("a")
    assert call_log.index("c") > call_log.index("b")
    assert manifest.tests_status == "green"
    assert set(manifest.files_changed) == {"a.py", "b.py", "c.py"}


async def test_run_skips_dependents_of_failed_node(make_config, monkeypatch):
    config = make_config()
    ledger = Ledger(config)
    call_log: list[str] = []
    monkeypatch.setattr(epic_mod.feature, "run", _make_fake_feature_run(call_log, fail_ids={"a"}))

    plan = [
        {"id": "a", "spec": "a", "target_paths": []},
        {"id": "b", "spec": "b", "target_paths": [], "depends_on": ["a"]},
    ]
    manifest = await run(_FakeRouter(), ledger, config, plan=plan, budget=100_000)

    assert "b" not in call_log  # never ran: its dependency failed
    assert manifest.tests_status == "red"
    assert "1 failed" in manifest.summary
    assert "1 skipped" in manifest.summary


async def test_run_budget_zero_skips_everything_without_spending(make_config, monkeypatch):
    config = make_config()
    ledger = Ledger(config)
    call_log: list[str] = []
    monkeypatch.setattr(epic_mod.feature, "run", _make_fake_feature_run(call_log))

    plan = [{"id": "a", "spec": "a", "target_paths": []}]
    manifest = await run(_FakeRouter(), ledger, config, plan=plan, budget=0)

    assert call_log == []  # not even attempted
    assert manifest.tokens_used["team_feature"] == 0
    assert "BUDGET EXHAUSTED" in manifest.summary


async def test_run_budget_none_falls_back_to_config_default(make_config, monkeypatch):
    # regression test for the real bug: "budget or default" treated 0 as
    # "not specified"; here we confirm that None (genuinely unspecified)
    # still falls back to the default and DOES run.
    config = make_config()
    ledger = Ledger(config)
    call_log: list[str] = []
    monkeypatch.setattr(epic_mod.feature, "run", _make_fake_feature_run(call_log))

    plan = [{"id": "a", "spec": "a", "target_paths": []}]
    manifest = await run(_FakeRouter(), ledger, config, plan=plan, budget=None)

    assert call_log == ["a"]
    assert manifest.tests_status == "green"


async def test_run_detects_cycle(make_config, monkeypatch):
    config = make_config()
    ledger = Ledger(config)
    call_log: list[str] = []
    monkeypatch.setattr(epic_mod.feature, "run", _make_fake_feature_run(call_log))

    plan = [
        {"id": "a", "spec": "a", "target_paths": [], "depends_on": ["b"]},
        {"id": "b", "spec": "b", "target_paths": [], "depends_on": ["a"]},
    ]
    manifest = await run(_FakeRouter(), ledger, config, plan=plan, budget=100_000)

    assert call_log == []  # neither of the two could ever start
    assert manifest.tests_status == "not_run"
    assert "2 skipped" in manifest.summary


async def test_run_empty_plan_returns_manifest_without_error(make_config):
    config = make_config()
    manifest = await run(_FakeRouter(), Ledger(config), config, plan=[])
    assert manifest.tests_status == "not_run"
    assert "empty" in manifest.summary


async def test_run_invalid_plan_returns_manifest_without_executing(make_config, monkeypatch):
    config = make_config()
    ledger = Ledger(config)
    call_log: list[str] = []
    monkeypatch.setattr(epic_mod.feature, "run", _make_fake_feature_run(call_log))

    plan = [{"id": "a", "spec": "s", "depends_on": ["ghost"]}]
    manifest = await run(_FakeRouter(), ledger, config, plan=plan)

    assert call_log == []
    assert "invalid" in manifest.summary
