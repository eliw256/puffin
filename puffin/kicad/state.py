"""`.puffin/state.json`: the state of both sides at the last real KiCad sync.

For each schematic it records two fingerprints, taken when an import ran kicad-cli and the graph
then matched the schematic:

- `schematic_sha256`: SHA-256 over the root sheet and every sub-sheet, line endings normalized
  to LF, so the same git checkout gives the same hash on every OS.
- `graph_sha256`: SHA-256 of the graph's managed connectivity (see `managed_connectivity`).

`puffin check` warns if either side changed since. The file is committed (docs/M2_IMPORT.md, i).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from puffin.core import Issue, ProjectModel, Severity
from puffin.core.fsio import atomic_write
from puffin.core.scope import managed_connectivity, schematics

STATE_PATH = Path(".puffin") / "state.json"
STATE_VERSION = 2

DRIFT_CODES: dict[str, Severity] = {
    "graph-drift": Severity.WARNING,
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


def graph_hash(model: ProjectModel, schematic: str) -> str:
    canonical = json.dumps(managed_connectivity(model, schematic), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def record_sync(project_dir: Path, schematic: str, model: ProjectModel) -> bool:
    """Record both sides after a real sync. Returns False if nothing changed.

    Only call this when kicad-cli exported the netlist in the same run and the graph now
    matches it; a --netlist file may come from any revision.
    """
    file_hash = schematic_hash(project_dir, schematic)
    if file_hash is None:
        return False
    entry = {"schematic_sha256": file_hash, "graph_sha256": graph_hash(model, schematic)}
    state = read_state(project_dir)
    if state.get("version") == STATE_VERSION and state["schematics"].get(schematic) == entry:
        return False
    state["version"] = STATE_VERSION
    state["schematics"][schematic] = entry
    data = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
    (project_dir / STATE_PATH).parent.mkdir(exist_ok=True)
    atomic_write(project_dir / STATE_PATH, data)
    return True


_DRY_RUN_HINT = "run `puffin import kicad --dry-run` to see what an import would change"


def drift_issues(project_dir: Path, model: ProjectModel) -> list[Issue]:
    """Warnings when either side changed since the last real sync, or there was none."""
    recorded = read_state(project_dir)["schematics"]
    issues = []
    for schematic in schematics(model):
        current = schematic_hash(project_dir, schematic)
        where = f"kicad_schematic {schematic}"
        entry = recorded.get(schematic)
        if current is None:
            issues.append(_issue("schematic-missing", where, f"{schematic} not found"))
            continue
        if not isinstance(entry, dict) or "graph_sha256" not in entry:
            # Never synced, or recorded by an older Puffin that tracked the schematic only.
            issues.append(
                _issue(
                    "schematic-unsynced",
                    where,
                    f"{schematic} has no recorded KiCad sync",
                    "run `puffin import kicad` (without --netlist) to compare and record one",
                )
            )
            continue
        if entry.get("schematic_sha256") != current:
            issues.append(
                _issue("schematic-drift", where, "schematic changed since last import",
                       _DRY_RUN_HINT)
            )  # fmt: skip
        if entry.get("graph_sha256") != graph_hash(model, schematic):
            issues.append(
                _issue(
                    "graph-drift",
                    where,
                    "puffin.yaml connectivity differs from the last KiCad sync "
                    "(KiCad wins on next import)",
                    "make the change in KiCad, or run "
                    "`puffin import kicad --dry-run` to see what would be overwritten",
                )
            )
    return issues


def _issue(code: str, where: str, message: str, hint: str = "") -> Issue:
    return Issue(DRIFT_CODES[code], code, where, message, hint)
