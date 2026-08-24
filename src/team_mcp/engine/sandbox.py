"""Jaula de rutas para toda escritura en disco del MCP.

Regla dura del proyecto: la escritura es la parte peligrosa. Nada se toca
fuera de TEAM_SANDBOX_ROOTS, y todo pasa por dry_run/snapshot antes de
aplicarse de verdad.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from team_mcp.config import Config
from team_mcp.engine.schemas import FileEdit


class SandboxViolation(RuntimeError):
    pass


class EditConflict(RuntimeError):
    """El bloque `search` no coincide exactamente con el contenido actual."""


@dataclass
class Snapshot:
    id: str
    files: dict[Path, str | None]  # None = el archivo no existía antes


class Sandbox:
    def __init__(self, config: Config) -> None:
        self._roots = [r.resolve() for r in config.sandbox_roots]
        self._dry_run = config.dry_run

    def _check_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not self._roots:
            raise SandboxViolation(
                "TEAM_SANDBOX_ROOTS vacío: no hay rutas permitidas para escritura"
            )
        if not any(resolved == r or r in resolved.parents for r in self._roots):
            raise SandboxViolation(f"ruta fuera de la whitelist: {resolved}")
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
        """Aplica bloques search/replace tras validar coincidencia exacta.

        Todo o nada: si un solo edit no encaja, no se escribe ninguno.
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
        if edit.search == "":
            # archivo nuevo
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.replace, encoding="utf-8")
            return

        if not target.exists():
            raise EditConflict(f"{target}: no existe y el edit no es de archivo nuevo")

        current = target.read_text(encoding="utf-8")
        if current.count(edit.search) != 1:
            raise EditConflict(
                f"{target}: el bloque `search` no aparece exactamente una vez "
                f"(apariciones={current.count(edit.search)})"
            )
        target.write_text(current.replace(edit.search, edit.replace, 1), encoding="utf-8")

    def workdir_copy(self, source_paths: list[str], into: Path) -> Path:
        """Copia archivos a un scratch dir para que los workers experimenten
        sin tocar el sandbox real (usado por el fan-out de consenso)."""
        into.mkdir(parents=True, exist_ok=True)
        for raw in source_paths:
            src = self._check_path(Path(raw))
            if src.exists():
                dst = into / src.name
                shutil.copy2(src, dst)
        return into
