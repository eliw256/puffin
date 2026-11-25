"""`.puffin/state.json`: which schematic revision the graph was last synced with.

Each schematic is fingerprinted by SHA-256 over its root sheet and every sub-sheet, with line
endings normalized to LF, so the same git checkout gives the same hash on every OS. The file
is committed (docs/M2_IMPORT.md, decision i).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from puffin.core import Issue, ProjectModel, Severity
from puffin.core.fsio import atomic_write

STATE_PATH = Path(".puffin") / "state.json"
STATE_VERSION = 1

DRIFT_CODES: dict[str, Severity] = {
    "schematic-drift": Severity.WARNING,
    "schematic-missing": Severity.WARNING,
    "schematic-unsynced": Severity.WARNING,
}

_SHEETFILE = re.compile(rb'\(property\s+"Sheetfile"\s+"((?:[^"\\]|\\.)*)"')


def schematic_files(root: Path) -> list[Path]:
    """The root sheet and every sub-sheet it reaches, each once, in discovery order."""
    seen: list[Path] = []
    pending = [root]
    while pending:
        path = pending.pop(0)
        if path in seen or not path.is_file():
            continue
        seen.append(path)
        for match in _SHEETFILE.finditer(path.read_bytes()):
            pending.append(path.parent / match.group(1).decode("utf-8"))
    return seen


def schematic_hash(project_dir: Path, schematic: str) -> str | None:
    """Combined LF-normalized hash, or None if the root sheet is missing."""
    root = project_dir / schematic
    if not root.is_file():
        return None
    digest = hashlib.sha256()
    entries = []
    for path in schematic_files(root):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        relative = path.relative_to(root.parent).as_posix()
        entries.append((relative, hashlib.sha256(data).hexdigest()))
    for relative, file_hash in sorted(entries):
        digest.update(f"{relative}\0{file_hash}\n".encode())
    return digest.hexdigest()


def read_state(project_dir: Path) -> dict[str, Any]:
    path = project_dir / STATE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "schematics": {}}
    if not isinstance(data, dict) or not isinstance(data.get("schematics"), dict):
        return {"version": STATE_VERSION, "schematics": {}}
    return data


def record_sync(project_dir: Path, schematic: str) -> bool:
    """Record the schematic's current hash. Returns False if nothing changed."""
    file_hash = schematic_hash(project_dir, schematic)
    if file_hash is None:
        return False
    state = read_state(project_dir)
    state["version"] = STATE_VERSION
    if state["schematics"].get(schematic) == {"sha256": file_hash}:
        return False
    state["schematics"][schematic] = {"sha256": file_hash}
    data = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
    (project_dir / STATE_PATH).parent.mkdir(exist_ok=True)
    atomic_write(project_dir / STATE_PATH, data)
    return True


def schematics(model: ProjectModel) -> list[str]:
    """Distinct `kicad_schematic` values, sorted. One import covers each."""
    return sorted({b.kicad_schematic for b in model.boards.values() if b.kicad_schematic})


def drift_issues(project_dir: Path, model: ProjectModel) -> list[Issue]:
    """Warnings for schematics that changed (or were never imported) since the last sync."""
    recorded = read_state(project_dir)["schematics"]
    issues = []
    for schematic in schematics(model):
        current = schematic_hash(project_dir, schematic)
        where = f"kicad_schematic {schematic}"
        if current is None:
            issues.append(_issue("schematic-missing", where, f"{schematic} not found"))
        elif schematic not in recorded:
            issues.append(
                _issue(
                    "schematic-unsynced",
                    where,
                    f"{schematic} has never been imported",
                    "run `puffin import kicad --dry-run` to compare it with the graph",
                )
            )
        elif recorded[schematic].get("sha256") != current:
            issues.append(
                _issue(
                    "schematic-drift",
                    where,
                    f"{schematic} may have changed since the last import",
                    "run `puffin import kicad --dry-run` to see whether connectivity changed",
                )
            )
    return issues


def _issue(code: str, where: str, message: str, hint: str = "") -> Issue:
    return Issue(DRIFT_CODES[code], code, where, message, hint)
