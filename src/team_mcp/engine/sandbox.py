"""Path cage for every on-disk write the MCP server performs.

Hard rule of the project: writing is the dangerous part. Nothing is
touched outside TEAM_SANDBOX_ROOTS, and everything goes through
dry_run/snapshot before it's actually applied.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.schemas import FileEdit


class SandboxViolation(RuntimeError):
    pass


class EditConflict(RuntimeError):
    """The `search` block doesn't match the current content exactly."""


@dataclass
class Snapshot:
    id: str
    files: dict[Path, str | None]  # None = the file didn't exist before


class Sandbox:
    def __init__(self, config: Config) -> None:
        self._roots = [r.resolve() for r in config.sandbox_roots]
        self._dry_run = config.dry_run

    def _check_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not self._roots:
            raise SandboxViolation(
                "TEAM_SANDBOX_ROOTS is empty: no paths allowed for writing"
            )
        if not any(resolved == r or r in resolved.parents for r in self._roots):
            raise SandboxViolation(f"path outside the whitelist: {resolved}")
        return resolved

    def snapshot(self, paths: list[str]) -> Snapshot:
        files: dict[Path, str | None] = {}
        for raw in paths:
            p = self._check_path(Path(raw))
            files[p] = p.read_text(encoding="utf-8") if p.exists() else None
        return Snapshot(id=f"snap-{int(time.time() * 1000)}", files=files)

    def rollback(self, snap: Snapshot) -> None:
        for p, content in snap.files.items():
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(content, encoding="utf-8")

    def apply_edits(self, edits: list[FileEdit]) -> list[str]:
        """Applies search/replace blocks after validating an exact match.

        All or nothing: if a single edit doesn't fit, none get written.
        """
        targets = [self._check_path(Path(e.path)) for e in edits]
        snap = self.snapshot([str(t) for t in targets])

        try:
            for edit, target in zip(edits, targets):
                self._apply_one(target, edit)
        except EditConflict:
            if not self._dry_run:
                self.rollback(snap)
            raise

        changed = [str(t) for t in targets]
        if self._dry_run:
            self.rollback(snap)
        return changed

    def _apply_one(self, target: Path, edit: FileEdit) -> None:
        _apply_edit_unchecked(target, edit)

    def materialize_edits(self, edits: list[FileEdit], into: Path) -> None:
        """Applies `edits` directly onto `into` (already assumed to be a
        safe scratch dir, bypassing the whitelist). Used by consensus.py
        to build each cell of the cross matrix without touching the real
        sandbox."""
        for edit in edits:
            target = into / edit.path
            _apply_edit_unchecked(target, edit)


def _apply_edit_unchecked(target: Path, edit: FileEdit) -> None:
    if edit.search == "":
        # new file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.replace, encoding="utf-8")
        return

    if not target.exists():
        raise EditConflict(f"{target}: doesn't exist and the edit isn't for a new file")

    current = target.read_text(encoding="utf-8")
    if current.count(edit.search) != 1:
        raise EditConflict(
            f"{target}: the `search` block doesn't appear exactly once "
            f"(occurrences={current.count(edit.search)})"
        )
    target.write_text(current.replace(edit.search, edit.replace, 1), encoding="utf-8")
