"""Maps KiCad pins to part-definition pins. Pure."""

from __future__ import annotations

from puffin.kicad.netlist import Node
from puffin.library import PartDefinition


def map_pin(part: PartDefinition, node: Node) -> str | None:
    """The part pin a KiCad node lands on, or None if nothing matches.

    The pad number is tried first against each pin's `numbers` (as strings, since pads
    such as "A6" are alphanumeric). If no pin lists numbers for it, the KiCad pin name is
    tried against the part pin key, then `datasheet_name`. Several pads can map to one pin
    (GND on 1, 40 and 41); callers collapse them to one endpoint.
    """
    for name, pin in part.pins.items():
        if node.pin in {str(n) for n in pin.numbers}:
            return name
    if node.pinfunction:
        if node.pinfunction in part.pins:
            return node.pinfunction
        for name, pin in part.pins.items():
            if pin.datasheet_name == node.pinfunction:
                return name
    return None
