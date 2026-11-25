"""Which parts of the graph KiCad manages, and edits that would be overwritten by an import.

KiCad is authoritative for the components on boards that set `kicad_schematic`, and for every
net restricted to their pins (docs/M2_IMPORT.md, decision e). Assignments, constraints,
overrides and intent stay graph-owned. Pure: no file or KiCad access.
"""

from __future__ import annotations

from typing import Any

from puffin.core.issues import Issue, Severity
from puffin.core.models import ProjectModel, split_endpoint

KICAD_OVERWRITE = "kicad-overwrite"


def schematics(model: ProjectModel) -> list[str]:
    """Distinct `kicad_schematic` values, sorted."""
    return sorted({b.kicad_schematic for b in model.boards.values() if b.kicad_schematic})


def managed_connectivity(model: ProjectModel, schematic: str) -> dict[str, Any]:
    """What KiCad is authoritative for, canonicalized: the components on boards that use
    `schematic`, and every net restricted to their pins."""
    boards = {name for name, b in model.boards.items() if b.kicad_schematic == schematic}
    owned = {ref for ref, c in model.components.items() if c.board in boards}
    components = {
        ref: [c.part, c.board, c.variant]
        for ref, c in sorted(model.components.items())
        if ref in owned
    }
    nets = {}
    for name, endpoints in sorted(model.nets.items()):
        mine = sorted({e for e in endpoints if split_endpoint(e)[0] in owned})
        if mine:
            nets[name] = mine
    return {"components": components, "nets": nets}


def _changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def kicad_overwrite_warnings(before: ProjectModel, after: ProjectModel) -> list[Issue]:
    """One warning per schematic whose managed connectivity the edit changes.

    The next KiCad import makes the graph match the schematic again, so such a change is
    reverted unless the same change is made in KiCad.
    """
    warnings = []
    for schematic in sorted(set(schematics(before)) | set(schematics(after))):
        old = managed_connectivity(before, schematic)
        new = managed_connectivity(after, schematic)
        nets = _changed(old["nets"], new["nets"])
        components = _changed(old["components"], new["components"])
        if not nets and not components:
            continue
        parts = []
        if nets:
            parts.append("net" + ("s " if len(nets) > 1 else " ") + ", ".join(nets))
        if components:
            parts.append(
                "component" + ("s " if len(components) > 1 else " ") + ", ".join(components)
            )
        warnings.append(
            Issue(
                Severity.WARNING,
                KICAD_OVERWRITE,
                f"kicad_schematic {schematic}",
                f"this edit changes KiCad-managed {' and '.join(parts)}; the next KiCad import "
                "reverts it unless the schematic changes too",
                f"make the same change in {schematic}, then run `puffin import kicad`",
            )
        )
    return warnings
