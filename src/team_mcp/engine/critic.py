"""Adversarial critique (plan primitive #3).

Fixed rubric, not "review this". The critic runs on tier-premium (agy or
its fallback), from a different model family than tier-coder, so errors
don't correlate. The real anti-false-positive filter lives in the schema:
CriticFinding requires a `failure_scenario`, so a finding without a
concrete scenario doesn't even pass pydantic validation — it's discarded
at parse time, not after.
"""

from __future__ import annotations

from team_mcp.engine.jsonio import parse_or_repair
from team_mcp.engine.schemas import CriticReport, FileEdit

_DEFAULT_FOCUS = """\
Assess ONLY these criteria:

1. correctness: does it do what the spec asks, in every case, not just the happy path?
2. edge_cases: empty/null inputs, boundaries, unexpected types?
3. security: injection, path traversal, unsafe deserialization, secrets?
4. contract_adherence: does it respect the surrounding code's APIs/conventions?
5. simplicity: is there unjustified complexity for what the spec asks?
"""

_RUBRIC = """\
You're an adversarial code reviewer. Your job is to find real problems,
not to praise the code. {focus}

Original spec:
{spec}

Code to review:
{code}

For EVERY finding, you must be able to describe a CONCRETE
`failure_scenario`: specific inputs that produce an incorrect output or a
crash. If you can't construct that scenario, DON'T report the finding — a
vague suspicion doesn't count.

Respond ONLY with JSON in this exact shape (empty list if there are no
real findings):
{{"findings": [
  {{"severity": "low|medium|high|critical",
    "file": "<path>",
    "line": <number or null>,
    "claim": "<what's wrong, in one sentence>",
    "failure_scenario": "<concrete input -> incorrect output/behavior>",
    "suggested_fix": "<optional>"}}
]}}
"""


def _render_code(edits: list[FileEdit]) -> str:
    return "\n\n".join(f"--- {e.path} ---\n{e.replace}" for e in edits)


async def review(
    router, workflow: str, spec: str, edits: list[FileEdit], *, focus: str | None = None,
) -> CriticReport:
    prompt = _RUBRIC.format(focus=focus or _DEFAULT_FOCUS, spec=spec, code=_render_code(edits))
    raw = await router.premium_review(workflow, prompt)
    return await parse_or_repair(raw, CriticReport, router, workflow)
