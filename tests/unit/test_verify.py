from __future__ import annotations

from pathlib import Path

from team_mcp.engine.schemas import VerificationResult
from team_mcp.engine.verify import _parses, compress_log


def test_passes_gate_true_when_parses_even_with_lint_issues():
    # Phase 4 decision: only parsing blocks, lint is informational
    result = VerificationResult(candidate_id="x", parses=True, lint_ok=False)
    assert result.passes_gate is True


def test_passes_gate_false_when_does_not_parse():
    result = VerificationResult(candidate_id="x", parses=False, lint_ok=True)
    assert result.passes_gate is False


def test_parses_detects_valid_python(tmp_path: Path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    ok, err = _parses(["ok.py"], tmp_path)
    assert ok is True
    assert err == ""


def test_parses_detects_syntax_error(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def f(:\n    return 1\n")
    ok, err = _parses(["broken.py"], tmp_path)
    assert ok is False
    assert "broken.py" in err


def test_parses_ignores_missing_or_non_py_files(tmp_path: Path):
    ok, err = _parses(["does_not_exist.py", "not_python.txt"], tmp_path)
    assert ok is True
    assert err == ""


def test_compress_log_strips_ansi_codes():
    assert compress_log("\x1b[31merror\x1b[0m: broken") == "error: broken"


def test_compress_log_collapses_repeated_consecutive_lines():
    text = "\n".join(["downloading..."] * 50 + ["FAILED test_x"])
    assert compress_log(text) == "downloading...\nFAILED test_x"


def test_compress_log_truncates_to_the_tail():
    text = "\n".join(f"line {i}" for i in range(1000))
    result = compress_log(text, max_chars=50)
    assert len(result) <= 50
    assert result.endswith("line 999")


def test_compress_log_preserves_non_repeated_content_unchanged():
    text = "line one\nline two\nline three"
    assert compress_log(text) == text
