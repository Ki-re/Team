"""JSON extraction/parsing from model responses, with a rescue chain.

Small models produce broken JSON frequently (plan primitive #5): direct
parse -> if it fails, a cheap repair with tier-fast -> if it still fails,
a hard error. Never retried more than once: if tier-fast can't fix it
either, the problem is real.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class JsonExtractionError(ValueError):
    pass


# "reasoning" models (some tier-coder/tier-context ones via OpenRouter)
# sometimes leak their chain of thought wrapped in these tags BEFORE the
# requested JSON. If that block contains even a single stray brace
# (common: the model "thinks out loud" about the shape of the JSON it's
# about to produce), the greedy regex below takes it as the start of the
# match and crosses over to the real final `}`, producing an invalid
# slice — seen failing in production (reported as "backend leaking
# <think> tags"). They're stripped whole before searching for JSON.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)


def extract_json(raw: str) -> dict | list:
    raw = _THINK_BLOCK_RE.sub("", raw)
    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    candidates = [m.group(0) for m in (obj_match, arr_match) if m]
    if not candidates:
        raise JsonExtractionError(f"no JSON in the model's response: {raw[:200]}")

    # the regex is greedy: if there's a bare array BEFORE a real object (or
    # vice versa), the array match extends to the text's last `]`, crossing
    # over the object and producing an invalid slice (seen failing in
    # tests). So "the longest one" blindly isn't enough: each candidate is
    # actually parsed, and length is only compared among the ones that are
    # genuinely valid JSON.
    parsed: list[tuple[str, dict | list]] = []
    last_error: Exception | None = None
    for text in candidates:
        try:
            parsed.append((text, json.loads(text)))
        except json.JSONDecodeError as exc:
            last_error = exc

    if not parsed:
        raise JsonExtractionError(
            f"JSON found but doesn't parse ({last_error}): {raw[:200]}"
        )

    _, best_value = max(parsed, key=lambda pair: len(pair[0]))
    return best_value


def extract_json_dict(raw: str) -> dict:
    """Like extract_json, but requires the result to be an object, not a
    list. Most prompts in this project ask for an object with concrete
    fields (e.g. {"edits": [...]}) — without this, a worker that returns a
    bare list produced a cryptic TypeError further down
    (`list indices must be integers, not str`) instead of a clear error."""
    data = extract_json(raw)
    if not isinstance(data, dict):
        raise JsonExtractionError(
            f"expected a JSON object, got a list: {raw[:200]}"
        )
    return data


async def parse_or_repair(raw: str, schema: type[T], router, workflow: str) -> T:
    """router: providers.router.Router — the direct import is avoided to
    prevent a cycle (router doesn't depend on engine, but this keeps it loose)."""
    try:
        return schema.model_validate(extract_json(raw))
    except (JsonExtractionError, ValidationError, json.JSONDecodeError) as exc:
        repair_prompt = (
            "The following text should be valid JSON matching this "
            f"schema (JSON Schema): {schema.model_json_schema()}\n\n"
            f"Text to fix:\n{raw}\n\n"
            f"Validation error: {exc}\n\n"
            "Return ONLY the fixed JSON, with no extra text or markdown."
        )
        fixed_raw = await router.fast(workflow, repair_prompt, temperature=0.0)
        return schema.model_validate(extract_json(fixed_raw))
