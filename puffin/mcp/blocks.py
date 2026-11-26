"""Block diagram data (M4 §f): components as blocks, buses grouped by peripheral. Pure."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from puffin.core.models import ProjectModel, split_endpoint
from puffin.library import PartLibrary, PartLoadError


def _pin_info(model: ProjectModel, library: PartLibrary, endpoint: str) -> tuple[bool, bool]:
    """(is a power pin, has a rail) for an endpoint; unknown parts count as signal pins."""
    ref, pin = split_endpoint(endpoint)
    component = model.components.get(ref)
    if component is None:
        return False, False
    try:
        part = library.get(component.part)
    except PartLoadError:
        return False, False
    definition = part.pins.get(pin) if part else None
    return bool(definition and definition.is_power), bool(definition and definition.rail)


def block_view(model: ProjectModel, library: PartLibrary) -> dict[str, Any]:
    """Blocks, board groups and edges.

    A net touching an MCU pin assigned to a peripheral joins one edge per (MCU, peripheral,
    other component), labelled with the peripheral and net count (SPI2, 4 nets); links between
    two MCUs merge into one edge.
    Other nets become one edge per net (a star from the first component). Nets with a power
    pin are `rail` edges, which the UI hides by default.
    """
    assigned: dict[str, tuple[str, str]] = {}  # endpoint -> (peripheral, signal)
    for ref, peripherals in model.assignments.items():
        for peripheral, signals in peripherals.items():
            for signal, pin in signals.items():
                assigned[f"{ref}.{pin}"] = (peripheral, signal)
    mcus = {b.firmware.mcu for b in model.boards.values() if b.firmware} | set(model.assignments)

    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    plain: list[dict[str, Any]] = []
    for net, endpoints in model.nets.items():
        refs = list(dict.fromkeys(split_endpoint(e)[0] for e in endpoints))
        if len(refs) < 2:
            continue
        rail = any(_pin_info(model, library, e)[0] for e in endpoints)
        buses = [(e, assigned[e]) for e in endpoints if e in assigned and not rail]
        if buses:
            for endpoint, (peripheral, _signal) in buses:
                mcu = split_endpoint(endpoint)[0]
                for other in refs:
                    if other == mcu:
                        continue
                    pair = tuple(sorted((mcu, other)))
                    key = (*pair, peripheral) if other not in mcus else (*pair, "link")
                    edge = groups.setdefault(
                        key,
                        {"source": pair[0], "target": pair[1], "peripherals": set(), "nets": []},
                    )
                    edge["peripherals"].add(peripheral)
                    if net not in edge["nets"]:
                        edge["nets"].append(net)
            continue
        for other in refs[1:]:
            plain.append({"source": refs[0], "target": other, "nets": [net],
                          "kind": "rail" if rail else "net", "label": net})  # fmt: skip

    board_of = {ref: c.board for ref, c in model.components.items()}
    edges: list[dict[str, Any]] = []
    for edge in groups.values():
        label = " / ".join(sorted(p.upper() for p in edge["peripherals"]))
        edges.append({
            "source": edge["source"], "target": edge["target"], "kind": "bus",
            "label": f"{label} \u00d7{len(edge['nets'])}", "nets": sorted(edge["nets"]),
            "peripherals": sorted(edge["peripherals"]),
        })  # fmt: skip
    edges.extend({**e, "peripherals": []} for e in plain)
    for index, edge in enumerate(sorted(edges, key=lambda e: (e["kind"], e["source"], e["target"],
                                                               e["label"]))):  # fmt: skip
        edge["id"] = f"e{index}"
        edge["cross_board"] = board_of.get(edge["source"]) != board_of.get(edge["target"])
    edges.sort(key=lambda e: e["id"])

    by_board: dict[str, list[str]] = defaultdict(list)
    nodes = []
    for ref, component in model.components.items():
        by_board[component.board].append(ref)
        nodes.append({"id": ref, "part": component.part, "board": component.board,
                      "mcu": ref in mcus})  # fmt: skip
    boards = [
        {"id": name, "kicad_schematic": board.kicad_schematic, "components": by_board.get(name, [])}
        for name, board in model.boards.items()
    ]
    return {"ok": True, "boards": boards, "nodes": nodes, "edges": edges}
