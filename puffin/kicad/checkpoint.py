"""The write-back checkpoint: the last confirmed schematic(s), puffin.yaml and state.json.

The first write-back after a confirm saves a checkpoint in `.puffin/backup/`. Further
write-backs stack on top of it without new backups. `confirm` accepts all of them and deletes
the checkpoint; `restore` puts every saved file back, undoing all of them (docs/WRITEBACK.md,
resolution 6).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from puffin.core.fsio import atomic_write
from puffin.kicad.state import STATE_PATH

BACKUP_DIR = Path(".puffin") / "backup"
MANIFEST = "checkpoint.json"


@dataclass(frozen=True)
class Checkpoint:
    pending: int
    files: dict[str, str | None]  # project-relative path -> saved copy name, None if absent


def read(project_dir: Path) -> Checkpoint | None:
    path = project_dir / BACKUP_DIR / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(int(data["pending"]), dict(data["files"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(project_dir: Path, checkpoint: Checkpoint) -> None:
    data: dict[str, Any] = {"version": 1, "pending": checkpoint.pending, "files": checkpoint.files}
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    atomic_write(project_dir / BACKUP_DIR / MANIFEST, payload)


def save(project_dir: Path, files: list[str]) -> Checkpoint:
    """Make sure each file's last confirmed copy is in the checkpoint.

    Creates the checkpoint if there is none. A file already in it keeps its saved copy: the
    checkpoint is the state before the *first* unconfirmed write-back.
    """
    existing = read(project_dir)
    saved = dict(existing.files) if existing else {}
    folder = project_dir / BACKUP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for relative in files:
        if relative in saved:
            continue
        source = project_dir / relative
        if source.is_file():
            name = f"{len(saved)}-{Path(relative).name}"
            shutil.copy2(source, folder / name)
            saved[relative] = name
        else:
            saved[relative] = None
    checkpoint = Checkpoint(existing.pending if existing else 0, saved)
    _write(project_dir, checkpoint)
    return checkpoint


def bump(project_dir: Path) -> int:
    checkpoint = read(project_dir)
    if checkpoint is None:
        raise RuntimeError("no checkpoint to count write-backs against")
    _write(project_dir, Checkpoint(checkpoint.pending + 1, checkpoint.files))
    return checkpoint.pending + 1


def discard_if_unused(project_dir: Path) -> None:
    """Remove a checkpoint that no write-back has used yet (rollback after a failure)."""
    checkpoint = read(project_dir)
    if checkpoint is not None and checkpoint.pending == 0:
        shutil.rmtree(project_dir / BACKUP_DIR, ignore_errors=True)


def confirm(project_dir: Path) -> int:
    """Accept every pending write-back. Returns how many were pending."""
    checkpoint = read(project_dir)
    if checkpoint is None:
        return 0
    shutil.rmtree(project_dir / BACKUP_DIR)
    return checkpoint.pending


def restore(project_dir: Path) -> int:
    """Undo every pending write-back: saved files come back, files that did not exist go."""
    checkpoint = read(project_dir)
    if checkpoint is None:
        return 0
    folder = project_dir / BACKUP_DIR
    for relative, name in sorted(checkpoint.files.items()):
        target = project_dir / relative
        if name is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, (folder / name).read_bytes())
    shutil.rmtree(folder)
    return checkpoint.pending


def files_for(yaml_name: str, schematics: list[str]) -> list[str]:
    """What a write-back transaction touches, project-relative."""
    return [yaml_name, STATE_PATH.as_posix(), *schematics]
