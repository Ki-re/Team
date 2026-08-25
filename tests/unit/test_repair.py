from __future__ import annotations

from team_mcp.engine.repair import _edits_signature, _materialize_to_dict
from team_mcp.engine.schemas import FileEdit


def test_materialize_to_dict_full_file_replace():
    base = {"a.py": "old content\n"}
    edits = [FileEdit(path="a.py", search="", replace="new content\n")]
    assert _materialize_to_dict(base, edits) == {"a.py": "new content\n"}


def test_materialize_to_dict_partial_search_replace():
    base = {"a.py": "def f():\n    return 1\n"}
    edits = [FileEdit(path="a.py", search="return 1", replace="return 2")]
    assert _materialize_to_dict(base, edits) == {"a.py": "def f():\n    return 2\n"}


def test_materialize_to_dict_falls_back_to_replace_when_search_does_not_match():
    # exactamente el bug real que motivó repair.py: un search que no coincide
    # no debe reventar el render del prompt, cae a mejor esfuerzo
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
