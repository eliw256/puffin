"""A generated symbol for components without a KiCad schematic (M4 §e fallback). Pure."""

from __future__ import annotations

from typing import Any

from puffin.library import PartDefinition

PITCH = 2.54  # mm between pins
LENGTH = 2.54  # pin length
WIDTH = 15.24  # body width


def generated_symbol(ref: str, part: PartDefinition) -> dict[str, Any]:
    """Pins in part-file order, first half on the left, second half on the right. Same shape as
    `symbol_graphics`, so the pin map draws both the same way."""
    names = list(part.pins)
    left = names[: (len(names) + 1) // 2]
    right = names[len(left) :]
    rows = max(len(left), len(right), 1)
    height = (rows + 1) * PITCH
    top = -height / 2
    pins = []
    for side, group in ((-1, left), (1, right)):
        for index, name in enumerate(group):
            pin = part.pins[name]
            y = round(top + (index + 1) * PITCH, 4)
            body_x = side * WIDTH / 2
            pins.append({
                "number": str(pin.numbers[0]) if pin.numbers else name,
                "name": pin.datasheet_name or name,
                "point": [round(body_x + side * LENGTH, 4), y],
                "body": [round(body_x, 4), y],
                "outward": 180 if side < 0 else 0,
                "hidden": False,
            })  # fmt: skip
    return {
        "ref": ref,
        "lib_id": part.id,
        "angle": 0,
        "pin_name_offset": 0.508,
        "shapes": [
            {"kind": "rect", "x": -WIDTH / 2, "y": round(top, 4), "w": WIDTH, "h": round(height, 4)}
        ],
        "pins": pins,
        "skipped": [],
    }
