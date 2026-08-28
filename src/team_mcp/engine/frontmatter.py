"""Frontmatter YAML + convención de knowledge-base (Fase 12 del plan).

Mismo patrón que el propio sistema de memoria de Claude: un archivo .md por
tema con un bloque frontmatter YAML al principio (name/description/tags/
last_verified) y un INDEX.md que lista cada tema en una línea. Ver
docs/KB_CONVENTION.md para el estándar completo — este módulo solo
implementa las partes deterministas y puras (parseo, detección de enlaces
rotos, staleness), sin tocar el filesystem fuera de lectura.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """(frontmatter, cuerpo). frontmatter=None si no hay bloque `---`
    inicial, si no parsea como YAML válido, o si no es un dict (una lista
    o escalar sueltos no cuentan como frontmatter válido aquí)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, text
    if not isinstance(data, dict):
        return None, text
    return data, text[m.end():]


def list_kb_entries(kb_path: Path) -> list[dict]:
    """Escanea kb_path por archivos .md (excluyendo INDEX.md) con
    frontmatter válido. Deliberadamente NO lee el cuerpo completo más allá
    del frontmatter en el resultado — es el índice barato que se le pasa
    al modelo, no una relectura de todo el KB."""
    entries = []
    for f in sorted(kb_path.rglob("*.md")):
        if f.name.upper() == "INDEX.MD":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = split_frontmatter(text)
        if fm is None:
            continue
        entries.append({
            "path": str(f.relative_to(kb_path)).replace("\\", "/"),
            "name": fm.get("name", f.stem),
            "description": fm.get("description", ""),
            "tags": fm.get("tags", []),
            "last_verified": fm.get("last_verified"),
        })
    return entries


def find_local_links(text: str) -> list[str]:
    """Targets de markdown links que parecen rutas relativas locales (ni
    URL con esquema `algo://`, ni solo un ancla `#...`, ni `mailto:`)."""
    targets = []
    for target in _LINK_RE.findall(text):
        target = target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        targets.append(target)
    return targets


def check_dangling_links(kb_path: Path) -> list[str]:
    """`archivo.md -> target roto` por cada link relativo del KB que no
    resuelve a un archivo existente."""
    broken = []
    for f in sorted(kb_path.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for target in find_local_links(text):
            if not (f.parent / target).resolve().exists():
                broken.append(f"{f.relative_to(kb_path)} -> {target}")
    return broken


def check_stale(entries: list[dict], max_age_days: int = 180) -> list[str]:
    """Entradas sin `last_verified` o más viejas que max_age_days."""
    stale = []
    # una staleness check de ~180 días no necesita tz-awareness: la hora
    # local del día actual es más que suficiente precisión aquí.
    cutoff = _dt.datetime.now().date() - _dt.timedelta(days=max_age_days)  # noqa: DTZ005
    for e in entries:
        raw = e.get("last_verified")
        if not raw:
            stale.append(f"{e['path']}: sin last_verified")
            continue
        try:
            d = _dt.date.fromisoformat(str(raw))
        except ValueError:
            stale.append(f"{e['path']}: last_verified inválido ({raw})")
            continue
        if d < cutoff:
            stale.append(f"{e['path']}: last_verified {raw} (> {max_age_days} días)")
    return stale
