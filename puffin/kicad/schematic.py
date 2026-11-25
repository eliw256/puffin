"""Read `.kicad_sch` geometry with byte offsets, so write-back can edit text in place.

Pure: text in, data out. Coordinates are `Decimal`, parsed from the file text, so arithmetic
on them is exact (100.33 + 10.16 == 110.49). Supports what write-back needs (docs/WRITEBACK.md):
symbol instances, embedded library pins, wires, local labels, other label kinds, junctions
and no-connects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

Point = tuple[Decimal, Decimal]


class SchematicError(ValueError):
    """The schematic cannot be read."""


@dataclass
class Node:
    """A list `( ... )` with its byte span; atoms are (text, start, end, quoted)."""

    items: list[Node | Atom]
    start: int
    end: int  # exclusive, after ")"

    @property
    def head(self) -> str:
        first = self.items[0] if self.items else None
        return first.text if isinstance(first, Atom) else ""

    def children(self, head: str) -> list[Node]:
        return [c for c in self.items if isinstance(c, Node) and c.head == head]

    def child(self, head: str) -> Node | None:
        found = self.children(head)
        return found[0] if found else None

    def atoms(self) -> list[str]:
        return [a.text for a in self.items[1:] if isinstance(a, Atom)]


@dataclass
class Atom:
    text: str
    start: int
    end: int
    quoted: bool


def parse(text: str) -> Node:
    """Parse the whole file. Offsets index into `text` (a str; files are UTF-8)."""
    stack: list[Node] = []
    root: Node | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "(":
            stack.append(Node([], i, -1))
            i += 1
        elif ch == ")":
            if not stack:
                raise SchematicError(f"unexpected ')' at offset {i}")
            node = stack.pop()
            node.end = i + 1
            if stack:
                stack[-1].items.append(node)
            else:
                root = node
            i += 1
        elif ch == '"':
            start = i
            i += 1
            out: list[str] = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i >= n:
                raise SchematicError("unterminated string")
            i += 1
            if not stack:
                raise SchematicError("text outside any list")
            stack[-1].items.append(Atom("".join(out), start, i, True))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '()"':
                i += 1
            if not stack:
                raise SchematicError("text outside any list")
            stack[-1].items.append(Atom(text[start:i], start, i, False))
    if stack or root is None:
        raise SchematicError("unbalanced parentheses")
    if root.head != "kicad_sch":
        raise SchematicError("not a KiCad schematic: expected (kicad_sch ...)")
    return root


def num(text: str) -> Decimal:
    return Decimal(text)


def fmt(value: Decimal) -> str:
    """A coordinate the way KiCad writes it: at most 4 decimals, no trailing zeros."""
    quantized = value.quantize(Decimal("0.0001"))
    text = format(quantized.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


def at_of(node: Node) -> tuple[Point, int, Node]:
    """The `(at x y [angle])` child: point, angle in degrees, and the node itself."""
    at = node.child("at")
    if at is None:
        raise SchematicError(f"({node.head}) without (at ...)")
    values = at.atoms()
    angle = int(Decimal(values[2])) if len(values) > 2 else 0
    return (num(values[0]), num(values[1])), angle % 360, at


# --- geometry ---------------------------------------------------------------------------

_DIRECTIONS = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}  # schematic y points down


def rotate(point: Point, angle: int) -> Point:
    """Rotate a schematic-space offset by a multiple of 90°, counterclockwise on screen."""
    x, y = point
    match angle % 360:
        case 0:
            return x, y
        case 90:
            return y, -x
        case 180:
            return -x, -y
        case 270:
            return -y, x
    raise SchematicError(f"rotation {angle} is not a multiple of 90")


@dataclass(frozen=True)
class LibPin:
    number: str
    unit: int  # 0 = common to all units
    style: int  # body style; 0 = common
    at: Point  # library coordinates, y up
    angle: int  # pin direction in library space: 0 points right (body to the right)


@dataclass(frozen=True)
class PinEnd:
    """Where a placed pin connects, and which way it points away from its symbol."""

    ref: str
    number: str
    point: Point
    outward: int  # 0 right, 90 up, 180 left, 270 down (screen)


@dataclass
class Instance:
    ref: str
    lib_id: str
    at: Point
    angle: int
    mirror: str | None
    unit: int
    node: Node


@dataclass
class Wire:
    a: Point
    b: Point
    node: Node
    a_node: Node
    b_node: Node

    def touches(self, p: Point) -> bool:
        return p in (self.a, self.b)

    def passes_through(self, p: Point) -> bool:
        """`p` lies strictly inside the segment (wires are axis-aligned or straight)."""
        (x1, y1), (x2, y2), (x, y) = self.a, self.b, p
        if p in (self.a, self.b):
            return False
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if cross != 0:
            return False
        return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)


@dataclass
class Label:
    kind: str  # "label" | "global_label" | "hierarchical_label"
    name: str
    at: Point
    angle: int
    node: Node
    at_node: Node


@dataclass
class Marker:
    kind: str  # "junction" | "no_connect"
    at: Point
    node: Node
    at_node: Node


@dataclass
class Schematic:
    text: str
    root: Node
    lib_pins: dict[str, list[LibPin]] = field(default_factory=dict)
    instances: dict[str, Instance] = field(default_factory=dict)
    wires: list[Wire] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    has_sheets: bool = False

    def pin_ends(self, ref: str) -> list[PinEnd]:
        inst = self.instances[ref]
        if inst.mirror is not None:
            raise SchematicError(f"{ref} is mirrored ({inst.mirror}); not supported in v1")
        result = []
        for pin in self.lib_pins.get(inst.lib_id, []):
            if pin.unit not in (0, inst.unit) or pin.style not in (0, 1):
                continue
            offset = rotate((pin.at[0], -pin.at[1]), inst.angle)
            point = (inst.at[0] + offset[0], inst.at[1] + offset[1])
            outward = (pin.angle + 180 + inst.angle) % 360
            result.append(PinEnd(ref, pin.number, point, outward))
        return result

    def pin_end(self, ref: str, number: str) -> PinEnd:
        found = [p for p in self.pin_ends(ref) if p.number == number]
        if len(found) != 1:
            raise SchematicError(f"{ref} has {len(found)} placed pins numbered {number}")
        return found[0]

    def all_pin_ends(self) -> list[PinEnd]:
        ends: list[PinEnd] = []
        for ref, inst in self.instances.items():
            if inst.mirror is None:
                ends.extend(self.pin_ends(ref))
        return ends


def _lib_pins(lib_symbols: Node) -> dict[str, list[LibPin]]:
    pins: dict[str, list[LibPin]] = {}
    for symbol in lib_symbols.children("symbol"):
        name = symbol.atoms()[0]
        found: list[LibPin] = []
        for unit_node in symbol.children("symbol"):
            unit_name = unit_node.atoms()[0]
            suffix = unit_name.rsplit("_", 2)
            unit, style = (int(suffix[1]), int(suffix[2])) if len(suffix) == 3 else (0, 0)
            for pin in unit_node.children("pin"):
                point, angle, _ = at_of(pin)
                number = pin.child("number")
                if number is None:
                    continue
                found.append(LibPin(number.atoms()[0], unit, style, point, angle))
        pins[name] = found
    return pins


def read_schematic(text: str) -> Schematic:
    root = parse(text)
    sch = Schematic(text, root)
    lib_symbols = root.child("lib_symbols")
    if lib_symbols is not None:
        sch.lib_pins = _lib_pins(lib_symbols)
    for item in root.items:
        if not isinstance(item, Node):
            continue
        head = item.head
        if head == "symbol":
            lib_id = item.child("lib_id")
            if lib_id is None:
                continue
            ref = next(
                (p.atoms()[1] for p in item.children("property") if p.atoms()[:1] == ["Reference"]),
                None,
            )
            if ref is None:
                continue
            point, angle, _ = at_of(item)
            mirror = item.child("mirror")
            unit = item.child("unit")
            sch.instances[ref] = Instance(
                ref,
                lib_id.atoms()[0],
                point,
                angle,
                mirror.atoms()[0] if mirror is not None else None,
                int(unit.atoms()[0]) if unit is not None else 1,
                item,
            )
        elif head == "wire":
            pts = item.child("pts")
            xys = pts.children("xy") if pts is not None else []
            if len(xys) != 2:
                continue
            (ax, ay), (bx, by) = (x.atoms() for x in xys)
            sch.wires.append(Wire((num(ax), num(ay)), (num(bx), num(by)), item, xys[0], xys[1]))
        elif head in {"label", "global_label", "hierarchical_label"}:
            point, angle, at_node = at_of(item)
            sch.labels.append(Label(head, item.atoms()[0], point, angle, item, at_node))
        elif head in {"junction", "no_connect"}:
            point, _, at_node = at_of(item)
            sch.markers.append(Marker(head, point, item, at_node))
        elif head == "sheet":
            sch.has_sheets = True
    return sch


def direction(angle: int) -> tuple[int, int]:
    return _DIRECTIONS[angle % 360]
