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
