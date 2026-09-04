from __future__ import annotations

import pytest

from team_mcp.engine.jsonio import JsonExtractionError, extract_json, extract_json_dict_with_repair


def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_plain_array():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_wrapped_in_prose():
    raw = 'Here is the result: {"a": 1, "b": 2} hope that helps.'
    assert extract_json(raw) == {"a": 1, "b": 2}


def test_extract_json_wrapped_in_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_picks_longest_when_object_and_array_both_present():
    raw = 'note: [1] but the real one is {"a": 1, "b": [1, 2, 3]}'
    assert extract_json(raw) == {"a": 1, "b": [1, 2, 3]}


def test_extract_json_raises_when_no_json_present():
    with pytest.raises(JsonExtractionError):
        extract_json("this has no JSON at all")


def test_extract_json_raises_on_malformed_json():
    with pytest.raises(Exception):  # noqa: B017 — json.JSONDecodeError, we don't want to couple to the exact type
        extract_json("{a: 1, b:}")


def test_extract_json_strips_think_block_containing_braces():
    # the real case that broke the greedy regex: the model reasons out
    # loud about the shape of the JSON (with example braces) BEFORE the real JSON.
    raw = (
        '<think>I\'ll return something like {"example": true} and then the real one</think>'
        '{"edits": [{"path": "a.py", "replace": "x = 1\\n"}]}'
    )
    assert extract_json(raw) == {"edits": [{"path": "a.py", "replace": "x = 1\n"}]}


def test_extract_json_strips_thinking_block_case_insensitive():
    raw = '<THINKING>{not this one}</THINKING>\n{"a": 1}'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_works_without_think_block():
    assert extract_json('{"a": 1}') == {"a": 1}


# --- extract_json_dict_with_repair ------------------------------------------


class _FakeRouter:
    def __init__(self, fast_response: str):
        self._fast_response = fast_response
        self.fast_calls = 0

    async def fast(self, workflow, prompt, temperature=0.0):
        self.fast_calls += 1
        return self._fast_response


async def test_extract_json_dict_with_repair_skips_repair_when_already_valid():
    router = _FakeRouter(fast_response="unused")
    data = await extract_json_dict_with_repair('{"edits": []}', router, "wf")
    assert data == {"edits": []}
    assert router.fast_calls == 0


async def test_extract_json_dict_with_repair_recovers_broken_json_via_tier_fast():
    # real bug found live: team_feature discarded a whole fan-out
    # candidate (a full tier-coder call) on the first parse failure, with
    # no repair attempt, despite jsonio.py's own docstring describing the
    # repair chain as universal — it was only ever wired to critic.py.
    router = _FakeRouter(fast_response='{"edits": [{"path": "a.py"}]}')
    broken = '{"edits": [{"path": "a.py",}]}'  # trailing comma
    data = await extract_json_dict_with_repair(broken, router, "wf")
    assert data == {"edits": [{"path": "a.py"}]}
    assert router.fast_calls == 1


async def test_extract_json_dict_with_repair_raises_when_repair_also_fails():
    router = _FakeRouter(fast_response="still not JSON")
    with pytest.raises(Exception):  # noqa: B017 — JsonExtractionError, don't couple to the exact type
        await extract_json_dict_with_repair("also not JSON", router, "wf")
    assert router.fast_calls == 1
