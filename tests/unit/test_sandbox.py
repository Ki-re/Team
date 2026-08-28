from __future__ import annotations

import pytest

from team_mcp.engine.sandbox import EditConflict, Sandbox, SandboxViolation
from team_mcp.engine.schemas import FileEdit


def test_check_path_rejects_outside_whitelist(make_config, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    sandbox = Sandbox(make_config(sandbox_roots=[allowed]))
    with pytest.raises(SandboxViolation):
        sandbox._check_path(tmp_path / "outside" / "f.py")


def test_check_path_accepts_inside_whitelist(make_config, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    sandbox = Sandbox(make_config(sandbox_roots=[root]))
    inside = root / "sub" / "f.py"
    assert sandbox._check_path(inside) == inside.resolve()


def test_check_path_empty_roots_rejects_everything(make_config, tmp_path):
    sandbox = Sandbox(make_config(sandbox_roots=[]))
    with pytest.raises(SandboxViolation):
        sandbox._check_path(tmp_path / "f.py")


def test_apply_edits_creates_new_file(make_config, tmp_path):
    sandbox = Sandbox(make_config(sandbox_roots=[tmp_path]))
    target = tmp_path / "new.py"
    changed = sandbox.apply_edits([FileEdit(path=str(target), search="", replace="print('hi')\n")])
    assert changed == [str(target.resolve())]
    assert target.read_text() == "print('hi')\n"


def test_apply_edits_replaces_matching_search(make_config, tmp_path):
    sandbox = Sandbox(make_config(sandbox_roots=[tmp_path]))
    target = tmp_path / "existing.py"
    target.write_text("def f():\n    return 1\n")
    sandbox.apply_edits([FileEdit(path=str(target), search="return 1", replace="return 2")])
    assert target.read_text() == "def f():\n    return 2\n"


def test_apply_edits_conflict_raises_and_rolls_back_all(make_config, tmp_path):
    sandbox = Sandbox(make_config(sandbox_roots=[tmp_path]))
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("original a\n")
    b.write_text("original b\n")
    edits = [
        FileEdit(path=str(a), search="original a", replace="changed a"),
        FileEdit(path=str(b), search="not present", replace="changed b"),
    ]
    with pytest.raises(EditConflict):
        sandbox.apply_edits(edits)
    # all or nothing: 'a' must not end up modified even though its own edit matched
    assert a.read_text() == "original a\n"
    assert b.read_text() == "original b\n"


def test_apply_edits_conflict_when_search_appears_twice(make_config, tmp_path):
    sandbox = Sandbox(make_config(sandbox_roots=[tmp_path]))
    target = tmp_path / "dup.py"
    target.write_text("x = 1\nx = 1\n")
    with pytest.raises(EditConflict):
        sandbox.apply_edits([FileEdit(path=str(target), search="x = 1", replace="x = 2")])


def test_apply_edits_dry_run_does_not_persist(make_config, tmp_path):
    sandbox = Sandbox(make_config(sandbox_roots=[tmp_path], dry_run=True))
    target = tmp_path / "ghost.py"
    sandbox.apply_edits([FileEdit(path=str(target), search="", replace="x = 1\n")])
    assert not target.exists()


def test_materialize_edits_writes_directly_without_whitelist_check(make_config, tmp_path):
    # materialize_edits is for consensus.py's scratch dirs: it deliberately
    # bypasses _check_path, so it must work even if `into` isn't in
    # sandbox_roots.
    sandbox = Sandbox(make_config(sandbox_roots=[]))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sandbox.materialize_edits([FileEdit(path="x.py", search="", replace="x = 1\n")], scratch)
    assert (scratch / "x.py").read_text() == "x = 1\n"
