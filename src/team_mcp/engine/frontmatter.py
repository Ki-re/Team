"""YAML frontmatter + knowledge-base convention (plan Phase 12).

Same pattern as Claude's own memory system: one .md file per topic with a
YAML frontmatter block at the top (name/description/tags/last_verified)
and an INDEX.md that lists each topic on one line. See
docs/KB_CONVENTION.md for the full standard — this module only implements
the deterministic, pure parts (parsing, broken-link detection, staleness),
without touching the filesystem beyond reading.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """(frontmatter, body). frontmatter=None if there's no initial `---`
    block, if it doesn't parse as valid YAML, or if it isn't a dict (a
    bare list or scalar doesn't count as valid frontmatter here)."""
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
    """Scans kb_path for .md files (excluding INDEX.md) with valid
    frontmatter. Deliberately does NOT read the full body beyond the
    frontmatter into the result — it's the cheap index handed to the
    model, not a re-read of the whole KB."""
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
    """Targets of markdown links that look like local relative paths
    (not a `scheme://` URL, not just an anchor `#...`, not `mailto:`)."""
    targets = []
    for target in _LINK_RE.findall(text):
        target = target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        targets.append(target)
    return targets


def check_dangling_links(kb_path: Path) -> list[str]:
    """`file.md -> broken target` for every relative link in the KB that
    doesn't resolve to an existing file."""
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
    """Entries without `last_verified` or older than max_age_days."""
    stale = []
    # a ~180-day staleness check doesn't need tz-awareness: the current
    # day's local time is more than enough precision here.
    cutoff = _dt.datetime.now().date() - _dt.timedelta(days=max_age_days)  # noqa: DTZ005
    for e in entries:
        raw = e.get("last_verified")
        if not raw:
            stale.append(f"{e['path']}: no last_verified")
            continue
        try:
            d = _dt.date.fromisoformat(str(raw))
        except ValueError:
            stale.append(f"{e['path']}: invalid last_verified ({raw})")
            continue
        if d < cutoff:
            stale.append(f"{e['path']}: last_verified {raw} (> {max_age_days} days)")
    return stale
