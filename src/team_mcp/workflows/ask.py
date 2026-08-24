"""team_ask: preguntas sobre código/logs, sin escribir nada.

Map-reduce real sobre tier-context (primitiva de la Capa 2 del plan):
  1. map: cada archivo se trocea y cada fragmento se pregunta en paralelo
     ("¿qué hay aquí relevante para la pregunta, con cita ruta:línea?").
  2. reduce: los extractos relevantes se sintetizan en una respuesta.
  3. verificación de citas: cada `ruta:línea` que aparece en la respuesta
     final se comprueba estructuralmente (el archivo existe en el scope, la
     línea está dentro de rango). Las que no se pueden verificar se marcan
     `[no verificado]` in-line — anti-alucinación determinista, no depende
     de que ningún modelo se autoevalúe.
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
_MAX_CHUNKS = 12  # tope de llamadas paralelas por pregunta
_SCAN_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".yml", ".json", ".txt", ".log", ".toml",
}
_CITATION_RE = re.compile(r"([\w./\\-]+\.\w+):(\d+)")

_MAP_PROMPT = """\
Estás leyendo UN fragmento de un archivo más grande, como parte de una
búsqueda distribuida para responder una pregunta. Extrae SOLO los hechos de
este fragmento relevantes para la pregunta, cada uno con su cita exacta en
formato `{path}:<línea>`. Si nada de este fragmento es relevante, responde
ÚNICAMENTE la palabra NADA_RELEVANTE.

Archivo: {path}
Pregunta: {question}

Fragmento (la primera línea de abajo es la línea {start_line} del archivo):
{content}
"""

_REDUCE_PROMPT = """\
Estos son extractos relevantes de varios archivos/fragmentos, cada uno ya
con sus citas `ruta:línea`. Sintetiza una respuesta concisa a la pregunta,
preservando las citas que uses tal cual aparecen abajo. No inventes citas
que no estén en los extractos.

Pregunta: {question}

Extractos:
{digests}
"""

_WEB_AUGMENT_PROMPT = """\
Respuesta basada en el código/archivos locales (puede estar vacía si no
había nada relevante):
{local_answer}

Pregunta original: {question}

Usa la tool de búsqueda web SOLO para completar lo que el contexto local no
cubre (versiones actuales, documentación externa, algo fuera del repo). No
la uses si la respuesta local ya es completa. Cita las fuentes web que uses
con su URL.
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
    """Devuelve ([(línea_inicio, texto_fragmento), ...], total_líneas)."""
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
    except Exception:  # noqa: BLE001 — un fragmento caído no debe tumbar la búsqueda
        return None
    if len(result) < 60 and "NADA_RELEVANTE" in result.upper():
        return None
    return f"[{path} desde línea {start_line}]\n{result}"


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
            return f"{m.group(0)} [no verificado]"
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
    """allow_web_search (Fase 7 del plan): cuando Claude sabe que la
    pregunta necesita contexto externo (versión de una librería, docs de
    una API externa), pasa a usar la tool `web_search` del MCP Gateway de
    LiteLLM (solo tier-context, solo desde team_ask — ver
    providers/router.py::context_with_tools). Nunca automático: si no se
    pide, el comportamiento es exactamente el de antes."""
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
            f"ningún archivo encontrado en scope_paths={scope_paths}" if not files
            else "ningún fragmento del scope resultó relevante para la pregunta"
        )
        return Manifest(tool=_WORKFLOW, tests_status="not_run", summary=summary, dry_run=config.dry_run)

    answer = ""
    if digests:
        reduce_prompt = _REDUCE_PROMPT.format(question=question, digests="\n\n".join(digests))
        answer = await router.context(_WORKFLOW, reduce_prompt, temperature=0.2)

    if allow_web_search:
        web_prompt = _WEB_AUGMENT_PROMPT.format(
            question=question, local_answer=answer or "(sin contexto local relevante)",
        )
        try:
            answer = await router.context_with_tools(_WORKFLOW, web_prompt)
        except Exception as exc:  # noqa: BLE001 — la búsqueda web es un complemento, no debe tumbar una respuesta local que sí funcionó
            if not answer:
                return Manifest(
                    tool=_WORKFLOW, tests_status="not_run",
                    summary=f"búsqueda web falló y no había contexto local: {exc}",
                    dry_run=config.dry_run,
                )
            answer += f"\n\n[búsqueda web no disponible: {exc}]"

    annotated, total_citations, unverified = _verify_citations(answer, line_counts)

    summary = annotated[:4000]
    if total_citations:
        summary += f"\n\n[{total_citations - unverified}/{total_citations} citas verificadas estructuralmente]"
    if truncated:
        summary += f"\n[scope truncado a los primeros {_MAX_CHUNKS} fragmentos de {len(files)} archivos]"

    return Manifest(
        tool=_WORKFLOW, tests_status="not_run",
        summary=summary, dry_run=config.dry_run,
    )
