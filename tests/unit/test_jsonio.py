from __future__ import annotations

import pytest

from team_mcp.engine.jsonio import JsonExtractionError, extract_json


def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_plain_array():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_wrapped_in_prose():
    raw = 'Aquí tienes el resultado: {"a": 1, "b": 2} espero que ayude.'
    assert extract_json(raw) == {"a": 1, "b": 2}


def test_extract_json_wrapped_in_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_picks_longest_when_object_and_array_both_present():
    raw = 'nota: [1] pero el real es {"a": 1, "b": [1, 2, 3]}'
    assert extract_json(raw) == {"a": 1, "b": [1, 2, 3]}


def test_extract_json_raises_when_no_json_present():
    with pytest.raises(JsonExtractionError):
        extract_json("esto no tiene nada de JSON")


def test_extract_json_raises_on_malformed_json():
    with pytest.raises(Exception):  # noqa: B017 — json.JSONDecodeError, no queremos acoplar el tipo exacto
        extract_json("{a: 1, b:}")


def test_extract_json_strips_think_block_containing_braces():
    # el caso real que rompía el regex greedy: el modelo razona en voz alta
    # sobre la forma del JSON (con llaves de ejemplo) ANTES del JSON real.
    raw = (
        '<think>voy a devolver algo como {"ejemplo": true} y luego el real</think>'
        '{"edits": [{"path": "a.py", "replace": "x = 1\\n"}]}'
    )
    assert extract_json(raw) == {"edits": [{"path": "a.py", "replace": "x = 1\n"}]}


def test_extract_json_strips_thinking_block_case_insensitive():
    raw = '<THINKING>{no es esto}</THINKING>\n{"a": 1}'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_works_without_think_block():
    assert extract_json('{"a": 1}') == {"a": 1}
