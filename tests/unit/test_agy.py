from __future__ import annotations

from team_mcp.providers.agy import PremiumProvider, _parse_agy_output


class _FakeGateway:
    def __init__(self, usage: dict | None = None):
        self._usage = usage

    async def chat(self, tier, messages, *, temperature=0.2, max_tokens=None, response_format=None):
        resp = {"choices": [{"message": {"content": "fallback response"}}]}
        if self._usage is not None:
            resp["usage"] = self._usage
        return resp


# --- _parse_agy_output (pure, deterministic) -------------------------------


def test_parse_agy_output_extracts_content_and_usage_from_json():
    raw = '{"response": "hello", "usage": {"input_tokens": 12, "output_tokens": 3, "thinking_tokens": 1}}'
    content, usage = _parse_agy_output(raw)
    assert content == "hello"
    assert usage == {"input_tokens": 12, "output_tokens": 3}


def test_parse_agy_output_handles_json_without_usage_field():
    raw = '{"response": "hello"}'
    content, usage = _parse_agy_output(raw)
    assert content == "hello"
    assert usage is None


def test_parse_agy_output_falls_back_to_raw_text_for_plain_text_output():
    # a different subscription CLI swapped in via TEAM_AGY_CLI_ARGS that
    # just prints plain text (e.g. `claude -p ... --output-format text`)
    raw = "plain text answer, not JSON at all"
    content, usage = _parse_agy_output(raw)
    assert content == raw
    assert usage is None


def test_parse_agy_output_falls_back_on_malformed_json():
    raw = '{"response": "unterminated'
    content, usage = _parse_agy_output(raw)
    assert content == raw
    assert usage is None


def test_parse_agy_output_falls_back_when_json_has_no_response_field():
    # syntactically valid JSON, just not agy's shape — e.g. some other
    # CLI's own unrelated JSON output format
    raw = '{"result": "hello", "ok": true}'
    content, usage = _parse_agy_output(raw)
    assert content == raw
    assert usage is None


# --- PremiumProvider.complete: last_usage propagation -----------------------


async def test_complete_records_last_error_when_agy_fails_and_falls_back(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    async def _raise_run_agy(prompt, *, timeout):
        raise RuntimeError("agy exit=1: quota exhausted")

    provider._run_agy = _raise_run_agy

    result = await provider.complete("prompt")

    assert result == "fallback response"
    assert provider.last_used == "fallback"
    assert provider.last_error == "RuntimeError: agy exit=1: quota exhausted"


async def test_complete_clears_last_error_on_success(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    async def _ok_run_agy(prompt, *, timeout):
        provider.last_used = "agy"  # the real _run_agy also sets this on success
        provider.last_usage = {"input_tokens": 100, "output_tokens": 20}
        return "agy response"

    provider._run_agy = _ok_run_agy
    provider.last_error = "leftover from a previous call"

    result = await provider.complete("prompt")

    assert result == "agy response"
    assert provider.last_used == "agy"
    assert provider.last_error is None
    assert provider.last_usage == {"input_tokens": 100, "output_tokens": 20}


async def test_complete_records_real_usage_from_gateway_fallback(make_config):
    # the fallback path always goes through the gateway, which returns
    # real usage — this used to be discarded entirely (ledger always saw
    # 0,0 for any premium_review call, agy or fallback alike).
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway(usage={"prompt_tokens": 50, "completion_tokens": 8}))
    provider._agy_path = None  # no agy binary resolved -> straight to fallback

    result = await provider.complete("prompt")

    assert result == "fallback response"
    assert provider.last_used == "fallback"
    assert provider.last_usage == {"input_tokens": 50, "output_tokens": 8}


async def test_complete_leaves_last_usage_none_when_gateway_omits_usage(make_config):
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway(usage=None))
    provider._agy_path = None

    await provider.complete("prompt")

    assert provider.last_usage is None


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, b""

    def kill(self):
        pass


async def test_run_agy_parses_real_json_output_end_to_end(make_config, monkeypatch):
    # exercises _run_agy itself, not just the pure parser — confirms the
    # subprocess -> stdout -> _parse_agy_output -> last_usage wiring works.
    config = make_config()
    provider = PremiumProvider(config, _FakeGateway())
    provider._agy_path = "C:/fake/agy.exe"

    raw_stdout = b'{"response": "the answer", "usage": {"input_tokens": 200, "output_tokens": 40}}'

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(raw_stdout)

    import team_mcp.providers.agy as agy_mod
    monkeypatch.setattr(agy_mod.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result = await provider.complete("prompt")

    assert result == "the answer"
    assert provider.last_used == "agy"
    assert provider.last_usage == {"input_tokens": 200, "output_tokens": 40}
