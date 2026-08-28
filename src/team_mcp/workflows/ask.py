"""team_ask: questions about code/logs, without writing anything.

Real map-reduce over tier-context (a Layer 2 primitive of the plan):
  1. map: each file is chunked and each fragment is asked in parallel
     ("what's here relevant to the question, with a path:line citation?").
  2. reduce: the relevant excerpts are synthesized into one answer.
  3. citation verification: every `path:line` that appears in the final
     answer is checked structurally (the file exists in scope, the line
     is in range). Ones that can't be verified are marked
     `[unverified]` in-line — deterministic anti-hallucination, doesn't
     depend on any model grading itself.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.ledger import Ledger
from team_mcp.engine.schemas import Manifest
from team_mcp.providers.router import Router

_WORKFLOW = "team_ask"
_CHUNK_CHARS = 12_000
_MAX_CHUNKS = 12  # cap on parallel calls per question
_SCAN_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".yml", ".json", ".txt", ".log", ".toml",
}
_CITATION_RE = re.compile(r"([\w./\\-]+\.\w+):(\d+)")

_MAP_PROMPT = """\
You're reading ONE fragment of a larger file, as part of a distributed
search to answer a question. Extract ONLY the facts from this fragment
relevant to the question, each with its exact citation in the format
`{path}:<line>`. If nothing in this fragment is relevant, respond with
ONLY the word NOTHING_RELEVANT.

File: {path}
Question: {question}

Fragment (the first line below is line {start_line} of the file):
{content}
"""

_REDUCE_PROMPT = """\
These are relevant excerpts from several files/fragments, each already
with its `path:line` citations. Synthesize a concise answer to the
question, preserving the citations you use exactly as they appear below.
Don't invent citations that aren't in the excerpts.

Question: {question}

Excerpts:
{digests}
"""

_WEB_AUGMENT_PROMPT = """\
Answer based on the local code/files (may be empty if nothing was
relevant):
{local_answer}

Original question: {question}

Use the web search tool ONLY to fill in what the local context doesn't
cover (current versions, external documentation, something outside the
repo). Don't use it if the local answer is already complete. Cite the web
sources you use with their URL.
"""


def _collect_files(scope_paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in scope_paths:
        p = Path(raw)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix in _SCAN_EXTENSIONS))
    return files


def _chunk_file(path: Path) -> tuple[list[tuple[int, str]], int]:
    """Returns ([(start_line, fragment_text), ...], total_lines)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0

    lines = text.splitlines(keepends=True)
    if not lines:
        return [], 0

    chunks: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_size = 0
    start_line = 1
    for i, line in enumerate(lines, start=1):
        buf.append(line)
        buf_size += len(line)
        if buf_size >= _CHUNK_CHARS:
            chunks.append((start_line, "".join(buf)))
            buf, buf_size = [], 0
            start_line = i + 1
    if buf:
        chunks.append((start_line, "".join(buf)))
    return chunks, len(lines)


async def _map_chunk(router: Router, path: str, start_line: int, content: str, question: str) -> str | None:
    prompt = _MAP_PROMPT.format(path=path, question=question, start_line=start_line, content=content)
    try:
        result = await router.context(_WORKFLOW, prompt, temperature=0.1)
    except Exception:  # noqa: BLE001 — one downed fragment shouldn't sink the whole search
        return None
    if len(result) < 60 and "NOTHING_RELEVANT" in result.upper():
        return None
    return f"[{path} from line {start_line}]\n{result}"


def _verify_citations(answer: str, line_counts: dict[str, int]) -> tuple[str, int, int]:
    total = 0
    unverified = 0

    def _check(m: re.Match) -> str:
        nonlocal total, unverified
        total += 1
        path, line = m.group(1), int(m.group(2))
        max_line = line_counts.get(path) or line_counts.get(Path(path).name)
        if max_line is None or not (1 <= line <= max_line):
            unverified += 1
            return f"{m.group(0)} [unverified]"
        return m.group(0)

    annotated = _CITATION_RE.sub(_check, answer)
    return annotated, total, unverified


async def run(
    router: Router,
    ledger: Ledger,
    config: Config,
    *,
    question: str,
    scope_paths: list[str],
    allow_web_search: bool = False,
) -> Manifest:
    """allow_web_search (plan Phase 7): when Claude knows the question
    needs external context (a library version, docs for an external API),
    it switches to using LiteLLM's MCP Gateway `web_search` tool
    (tier-context only, only from team_ask — see
    providers/router.py::context_with_tools). Never automatic: if it's
    not requested, behavior is exactly as before."""
    files = _collect_files(scope_paths)
    digests: list[str] = []
    line_counts: dict[str, int] = {}
    truncated = False

    if files:
        tasks: list[tuple[str, int, str]] = []
        for f in files:
            chunks, total_lines = _chunk_file(f)
            line_counts[str(f)] = total_lines
            for start_line, content in chunks:
                tasks.append((str(f), start_line, content))

        truncated = len(tasks) > _MAX_CHUNKS
        tasks = tasks[:_MAX_CHUNKS]

        map_results = await asyncio.gather(*[
            _map_chunk(router, path, start_line, content, question)
            for path, start_line, content in tasks
        ])
        digests = [r for r in map_results if r]

    if not digests and not allow_web_search:
        summary = (
            f"no file found in scope_paths={scope_paths}" if not files
            else "no fragment in scope turned out relevant to the question"
        )
        return Manifest(tool=_WORKFLOW, tests_status="not_run", summary=summary, dry_run=config.dry_run)

    answer = ""
    if digests:
        reduce_prompt = _REDUCE_PROMPT.format(question=question, digests="\n\n".join(digests))
        answer = await router.context(_WORKFLOW, reduce_prompt, temperature=0.2)

    if allow_web_search:
        web_prompt = _WEB_AUGMENT_PROMPT.format(
            question=question, local_answer=answer or "(no relevant local context)",
        )
        try:
            answer = await router.context_with_tools(_WORKFLOW, web_prompt)
        except Exception as exc:  # noqa: BLE001 — web search is a complement, it shouldn't sink a local answer that DID work
            if not answer:
                return Manifest(
                    tool=_WORKFLOW, tests_status="not_run",
                    summary=f"web search failed and there was no local context: {exc}",
                    dry_run=config.dry_run,
                )
            answer += f"\n\n[web search unavailable: {exc}]"

    annotated, total_citations, unverified = _verify_citations(answer, line_counts)

    summary = annotated[:4000]
    if total_citations:
        summary += f"\n\n[{total_citations - unverified}/{total_citations} citations structurally verified]"
    if truncated:
        summary += f"\n[scope truncated to the first {_MAX_CHUNKS} fragments out of {len(files)} files]"

    return Manifest(
        tool=_WORKFLOW, tests_status="not_run",
        summary=summary, dry_run=config.dry_run,
    )
