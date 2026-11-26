"""What an issue's `where` points at, so a UI can select it (M4 §g). Pure."""

from __future__ import annotations

import re
from typing import Any

from puffin.core.models import ProjectModel

_ENDPOINT = re.compile(r"^([^.\s]+)\.([^.\s]+)$")
_INDEXED = re.compile(r"^(constraints|overrides)\[(\d+)\]")


def issue_target(model: ProjectModel, where: str) -> dict[str, Any]:
    """`{kind, component?, pin?, net?, board?}`; kind is pin, net, component, board, schematic,
    writeback or none."""
    if where.startswith("nets."):
        return {"kind": "net", "net": where[5:]}
    if where.startswith("assignments."):
        parts = where.split(".", 3)
        if len(parts) == 4:
            ref, peripheral, signal = parts[1], parts[2], parts[3]
            pin = model.assignments.get(ref, {}).get(peripheral, {}).get(signal)
            if pin:
                return {"kind": "pin", "component": ref, "pin": pin}
            return {"kind": "component", "component": ref}
    if where.startswith("components."):
        return {"kind": "component", "component": where.split(".", 1)[1]}
    if where.startswith("boards."):
        return {"kind": "board", "board": where.split(".")[1]}
    if where.startswith("kicad_schematic "):
        return {"kind": "schematic", "schematic": where.split(" ", 1)[1]}
    if where.startswith(".puffin/backup"):
        return {"kind": "writeback"}
    indexed = _INDEXED.match(where)
    if indexed:
        index = int(indexed.group(2))
        if indexed.group(1) == "constraints" and index < len(model.constraints):
            return {"kind": "net", "net": model.constraints[index].net}
        if indexed.group(1) == "overrides" and index < len(model.overrides):
            ref, pin = model.overrides[index].pin.split(".", 1)
            return {"kind": "pin", "component": ref, "pin": pin}
    endpoint = _ENDPOINT.match(where)
    if endpoint and endpoint.group(1) in model.components:
        return {"kind": "pin", "component": endpoint.group(1), "pin": endpoint.group(2)}
    if where in model.components:
        return {"kind": "component", "component": where}
    return {"kind": "none"}
