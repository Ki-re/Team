from __future__ import annotations

from pathlib import Path

from team_mcp.workflows.validate import (
    _check_kb_frontmatter,
    _check_secrets,
    _check_syntax,
    _collect_py_files,
)


def test_collect_py_files_single_file(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    assert _collect_py_files(f) == [f]


def test_collect_py_files_ignores_non_py_single_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x = 1\n")
    assert _collect_py_files(f) == []


def test_collect_py_files_directory_recursive(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    a = tmp_path / "a.py"
    b = tmp_path / "sub" / "b.py"
    (tmp_path / "c.txt").write_text("not python\n")
    a.write_text("x = 1\n")
    b.write_text("y = 2\n")
    assert set(_collect_py_files(tmp_path)) == {a, b}


def test_check_syntax_all_valid(tmp_path: Path):
    f = tmp_path / "ok.py"
    f.write_text("def f():\n    return 1\n")
    ok, detail = _check_syntax([f])
    assert ok is True
    assert detail == ""


def test_check_syntax_reports_error_with_filename(tmp_path: Path):
    f = tmp_path / "broken.py"
    f.write_text("def f(:\n")
    ok, detail = _check_syntax([f])
    assert ok is False
    assert "broken.py" in detail


def test_check_secrets_detects_aws_key(tmp_path: Path):
    f = tmp_path / "leak.py"
    f.write_text('key = "AKIAABCDEFGHIJKLMNOP"\n')
    ok, detail = _check_secrets([f])
    assert ok is False
    assert "leak.py" in detail


def test_check_secrets_detects_private_key_block(tmp_path: Path):
    f = tmp_path / "leak.pem"
    f.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBA...\n")
    ok, _ = _check_secrets([f])
    assert ok is False


def test_check_secrets_clean_file_passes(tmp_path: Path):
    f = tmp_path / "clean.py"
    f.write_text("def f():\n    return 'hello world'\n")
    ok, detail = _check_secrets([f])
    assert ok is True
    assert detail == ""


def test_check_secrets_short_password_value_not_flagged(tmp_path: Path):
    # the pattern requires >=8 characters to avoid trivial false positives
    f = tmp_path / "cfg.py"
    f.write_text('password = "abc"\n')
    ok, _ = _check_secrets([f])
    assert ok is True


def test_check_kb_frontmatter_ignores_index_and_files_without_frontmatter(tmp_path: Path):
    (tmp_path / "INDEX.md").write_text("- [a](a.md) — desc\n")
    (tmp_path / "no_frontmatter.md").write_text("just text\n")
    ok, detail = _check_kb_frontmatter(tmp_path)
    assert ok is True
    assert detail == ""


def test_check_kb_frontmatter_passes_valid_frontmatter(tmp_path: Path):
    (tmp_path / "a.md").write_text("---\nname: a\ndescription: x\n---\ncontent\n")
    ok, detail = _check_kb_frontmatter(tmp_path)
    assert ok is True
    assert detail == ""


def test_check_kb_frontmatter_flags_broken_frontmatter(tmp_path: Path):
    f = tmp_path / "broken.md"
    f.write_text("---\nname: [unclosed\n---\ncontent\n")
    ok, detail = _check_kb_frontmatter(tmp_path)
    assert ok is False
    assert "broken.md" in detail
