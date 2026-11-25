"""Scoped KiCad write-back of pin moves (docs/WRITEBACK.md).

`plan_writeback` turns the `move_pin`s of a proposal into a minimal text edit of each affected
`.kicad_sch`, then verifies it with kicad-cli: the edited schematic's netlist must equal the
original's with exactly the expected connectivity change, and must be in sync with the edited
graph. Anything outside the supported shapes is refused with a reason, never guessed.
"""

from __future__ import annotations

import difflib
import hashlib
import shutil
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puffin.core import Op, ProjectModel
from puffin.core.scope import managed_connectivity, schematics
from puffin.kicad.changes import build_changes
from puffin.kicad.export import KiCadCliError, export_netlist
from puffin.kicad.netlist import Netlist, read_netlist
from puffin.kicad.schematic import (
    Atom,
    Point,
    Schematic,
    SchematicError,
    fmt,
    read_schematic,
    rotate,
)
from puffin.library import PartLibrary

Exporter = Callable[[Path], str]
_UUID_NAMESPACE = uuid.UUID("6f1c3a52-3e0b-4d43-9a58-6d1f0f0b7a11")
_REWIRE = "; rewire in KiCad"


class Refusal(Exception):
    """Write-back cannot apply this change. The message says why."""


@dataclass(frozen=True)
class Move:
    ref: str
    from_pin: str
    to_pin: str
    net: str
    from_pad: str
    to_pad: str

    def describe(self) -> str:
        return f"{self.net}: {self.ref}.{self.from_pin} -> {self.ref}.{self.to_pin}"


@dataclass
class WritebackPlan:
    schematic: str  # as in boards.*.kicad_schematic
    moves: list[Move]
    available: bool
    reason: str = ""
    original: bytes = b""
    modified: bytes = b""
    verified: bool = False
    diff: str = ""
    edits: list[str] = field(default_factory=list)

    @property
    def original_sha256(self) -> str:
        return hashlib.sha256(self.original).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schematic": self.schematic,
            "available": self.available,
            "reason": self.reason,
            "moves": [m.describe() for m in self.moves],
            "verified": self.verified,
            "diff": self.diff,
        }


def lock_file(path: Path) -> Path:
    """KiCad 9's lock file for an open schematic."""
    return path.parent / f"~{path.name}.lck"


# --- which moves ------------------------------------------------------------------------


def find_moves(
    before: ProjectModel, after: ProjectModel, ops: Sequence[Op], library: PartLibrary
) -> dict[str, list[Move]]:
    """The pin moves per schematic, or Refusal if the managed change is anything else."""
    net_of = {e: net for net, endpoints in before.nets.items() for e in endpoints}
    owner = {
        ref: before.boards[c.board].kicad_schematic
        for ref, c in before.components.items()
        if c.board in before.boards and before.boards[c.board].kicad_schematic
    }
    moves: dict[str, list[Move]] = {}
    for op in ops:
        if op.op != "move_pin":
            continue
        ref, src, tgt = op.args["component"], op.args["from_pin"], op.args["to_pin"]
        schematic = owner.get(ref)
        net = net_of.get(f"{ref}.{src}")
        if schematic is None or net is None:
            continue  # not KiCad-managed, or no net to move: nothing to write back
        part = library.get(before.components[ref].part)
        if part is None:
            raise Refusal(f"part {before.components[ref].part} is not in the library")
        pads = []
        for pin in (src, tgt):
            numbers = part.pins[pin].numbers if pin in part.pins else []
            if len(numbers) != 1:
                raise Refusal(f"{ref}.{pin} maps to {len(numbers)} pads{_REWIRE}")
            pads.append(str(numbers[0]))
        moves.setdefault(schematic, []).append(Move(ref, src, tgt, net, pads[0], pads[1]))

    for schematic in sorted(set(schematics(before)) | set(schematics(after))):
        expected = managed_connectivity(before, schematic)
        swap = {f"{m.ref}.{m.from_pin}": f"{m.ref}.{m.to_pin}" for m in moves.get(schematic, [])}
        sources = set(swap)
        if any(target in sources for target in swap.values()):
            raise Refusal(f"a move targets another move's source pin (a swap){_REWIRE}")
        expected["nets"] = {
            net: sorted(swap.get(e, e) for e in endpoints)
            for net, endpoints in expected["nets"].items()
        }
        actual = managed_connectivity(after, schematic)
        if expected != actual:
            raise Refusal(
                f"this proposal changes KiCad-managed connectivity on {schematic} beyond pin "
                "moves; write-back supports move_pin only"
            )
    return moves


# --- the text edit ----------------------------------------------------------------------


def _at(point: Point, angle: int | None = None) -> str:
    coords = f"(at {fmt(point[0])} {fmt(point[1])}"
    return coords + (f" {angle})" if angle is not None else ")")


def _xy(point: Point) -> str:
    return f"(xy {fmt(point[0])} {fmt(point[1])})"


def _apply(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _items_at(sch: Schematic, point: Point, exclude_pin: tuple[str, str]) -> dict[str, list[Any]]:
    return {
        "labels": [lb for lb in sch.labels if lb.at == point],
        "wires": [w for w in sch.wires if w.touches(point)],
        "through": [w for w in sch.wires if w.passes_through(point)],
        "markers": [m for m in sch.markers if m.at == point],
        "pins": [
            p for p in sch.all_pin_ends() if p.point == point and (p.ref, p.number) != exclude_pin
        ],
    }


def _describe(items: dict[str, list[Any]]) -> str:
    parts = []
    for key in ("labels", "wires", "through", "markers", "pins"):
        if items[key]:
            parts.append(f"{len(items[key])} {key}")
    return ", ".join(parts) or "nothing"


def edit_move(text: str, move: Move, source_sha: str) -> tuple[str, list[str]]:
    """Move one pin's label (and stub) to the target pin. Returns the new text and notes."""
    sch = read_schematic(text)
    if move.ref not in sch.instances:
        raise Refusal(f"{move.ref} is not on the root sheet of this schematic{_REWIRE}")
    try:
        source = sch.pin_end(move.ref, move.from_pad)
        target = sch.pin_end(move.ref, move.to_pad)
    except SchematicError as exc:
        raise Refusal(f"{exc}{_REWIRE}") from exc

    s_items = _items_at(sch, source.point, (move.ref, move.from_pad))
    t_items = _items_at(sch, target.point, (move.ref, move.to_pad))
    for label in s_items["labels"]:
        if label.kind != "label":
            raise Refusal(f"{move.ref}.{move.from_pin} connects through a {label.kind}{_REWIRE}")

    # Target: nothing but an optional no-connect.
    t_nc = [m for m in t_items["markers"] if m.kind == "no_connect"]
    if (
        t_items["labels"]
        or t_items["wires"]
        or t_items["through"]
        or t_items["pins"]
        or len(t_items["markers"]) != len(t_nc)
        or len(t_nc) > 1
    ):
        raise Refusal(
            f"target {move.ref}.{move.to_pin} is not free (found {_describe(t_items)}){_REWIRE}"
        )

    delta = (target.outward - source.outward) % 360
    if delta not in (0, 180):
        raise Refusal(
            f"{move.ref}.{move.to_pin} points 90 degrees from {move.from_pin}; only same-side "
            f"and opposite-side moves are supported{_REWIRE}"
        )

    def moved(point: Point) -> Point:
        offset = rotate((point[0] - source.point[0], point[1] - source.point[1]), delta)
        return target.point[0] + offset[0], target.point[1] + offset[1]

    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    s = source.point
    if (
        len(s_items["labels"]) == 1
        and not s_items["wires"]
        and not s_items["through"]
        and not s_items["markers"]
        and not s_items["pins"]
    ):
        label = s_items["labels"][0]  # shape A: label directly on the pin
        notes.append(f"label {label.name} moved to {move.ref}.{move.to_pin}")
    elif (
        not s_items["labels"]
        and len(s_items["wires"]) == 1
        and not s_items["through"]
        and not s_items["markers"]
        and not s_items["pins"]
    ):
        stub = s_items["wires"][0]  # shape B: a stub from the pin to one label
        far = stub.b if stub.a == s else stub.a
        q_items = _items_at(sch, far, ("", ""))
        q_labels = q_items["labels"]
        if (
            len(q_labels) != 1
            or q_labels[0].kind != "label"
            or len(q_items["wires"]) != 1
            or q_items["through"]
            or q_items["markers"]
            or q_items["pins"]
        ):
            raise Refusal(
                f"the wire from {move.ref}.{move.from_pin} does not end in exactly one local "
                f"label (found {_describe(q_items)}){_REWIRE}"
            )
        points = [p.point for p in sch.all_pin_ends()]
        points += [lb.at for lb in sch.labels] + [m.at for m in sch.markers]
        points += [end for w in sch.wires if w is not stub for end in (w.a, w.b)]
        if any(stub.passes_through(point) for point in points):
            raise Refusal(f"something connects along the wire from {move.ref}.{move.from_pin}"
                          f"{_REWIRE}")  # fmt: skip
        label = q_labels[0]
        near, far_node = (stub.a_node, stub.b_node) if stub.a == s else (stub.b_node, stub.a_node)
        edits.append((near.start, near.end, _xy(target.point)))
        edits.append((far_node.start, far_node.end, _xy(moved(far))))
        notes.append(f"label {label.name} and its wire moved to {move.ref}.{move.to_pin}")
    else:
        raise Refusal(
            f"{move.ref}.{move.from_pin} connects through {_describe(s_items)}; write-back "
            f"supports one label, directly or on one wire stub{_REWIRE}"
        )

    edits.append(
        (label.at_node.start, label.at_node.end, _at(moved(label.at), (label.angle + delta) % 360))
    )
    if delta == 180:
        effects = label.node.child("effects")
        justify = effects.child("justify") if effects is not None else None
        for atom in justify.items[1:] if justify is not None else []:
            if not isinstance(atom, Atom) or atom.text not in ("left", "right"):
                continue
            edits.append((atom.start, atom.end, "left" if atom.text == "right" else "right"))

    if t_nc:
        marker = t_nc[0]
        edits.append((marker.at_node.start, marker.at_node.end, _at(s)))
        notes.append(f"no-connect moved from {move.ref}.{move.to_pin} to {move.from_pin}")
    else:
        token = uuid.uuid5(_UUID_NAMESPACE, f"{source_sha}:{move.ref}:{move.from_pad}")
        block = f'\t(no_connect\n\t\t{_at(s)}\n\t\t(uuid "{token}")\n\t)\n'
        anchor = next(
            (m.node.start for m in sch.markers if m.kind == "no_connect"),
            next((w.node.start for w in sch.wires), None),
        )
        if anchor is None:
            raise Refusal("cannot place a no-connect in a schematic without wires")
        line_start = text.rfind("\n", 0, anchor) + 1
        edits.append((line_start, line_start, block))
        notes.append(f"no-connect added on {move.ref}.{move.from_pin}")
    return _apply(text, edits), notes


# --- verification -----------------------------------------------------------------------


def _connectivity(
    netlist: Netlist,
) -> tuple[dict[str, frozenset[tuple[str, str]]], set[tuple[str, str]]]:
    connected: dict[str, frozenset[tuple[str, str]]] = {}
    unconnected: set[tuple[str, str]] = set()
    for net in netlist.nets:
        nodes = frozenset((n.ref, n.pin) for n in net.nodes)
        if net.kind == "unconnected":
            unconnected |= nodes
        else:
            connected[net.name] = nodes
    return connected, unconnected


def _export_copy(schematic: Path, text: bytes | None, export: Exporter) -> Netlist:
    """Export from a temp copy of the schematic's folder, so symbol libraries resolve."""
    with tempfile.TemporaryDirectory(prefix="puffin-writeback-") as tmp:
        folder = Path(tmp) / "sch"
        shutil.copytree(
            schematic.parent,
            folder,
            ignore=shutil.ignore_patterns("*-backups", "*.lck", "_autosave-*", "fp-info-cache"),
        )
        copy = folder / schematic.name
        if text is not None:
            copy.write_bytes(text)
        return read_netlist(export(copy))


def verify(
    path: Path,
    modified: bytes,
    moves: list[Move],
    after: ProjectModel,
    library: PartLibrary,
    schematic: str,
    export: Exporter,
) -> None:
    """Refusal unless kicad-cli sees exactly the expected change, in sync with the graph."""
    try:
        before = _export_copy(path, None, export)
        result = _export_copy(path, modified, export)
    except KiCadCliError as exc:
        raise Refusal(f"kicad-cli is needed to verify write-back: {exc}") from exc
    old_connected, old_unconnected = _connectivity(before)
    new_connected, new_unconnected = _connectivity(result)
    expected = dict(old_connected)
    expected_unconnected = set(old_unconnected)
    for move in moves:
        src, tgt = (move.ref, move.from_pad), (move.ref, move.to_pad)
        name = next((n for n, nodes in expected.items() if src in nodes), None)
        if name is None:
            raise Refusal(f"kicad-cli shows {move.ref} pad {move.from_pad} on no net")
        expected[name] = (expected[name] - {src}) | {tgt}
        expected_unconnected = (expected_unconnected - {tgt}) | {src}
    if new_connected != expected or new_unconnected != expected_unconnected:
        names = sorted(set(expected) | set(new_connected))
        first = next(
            (n for n in names if expected.get(n) != new_connected.get(n)), "unconnected pins"
        )
        raise Refusal(
            f"verification failed: kicad-cli's netlist of the edited schematic differs at "
            f"{first}; nothing was changed"
        )
    changes = build_changes(after, library, result, schematic)
    if not changes.empty or changes.errors:
        raise Refusal("verification failed: the edited schematic does not match the edited graph")


# --- the plan ---------------------------------------------------------------------------


def plan_writeback(
    project_dir: Path,
    before: ProjectModel,
    after: ProjectModel,
    ops: Sequence[Op],
    library: PartLibrary,
    *,
    verify_with: Exporter | None = export_netlist,
) -> list[WritebackPlan]:
    """One plan per affected schematic. `verify_with=None` skips kicad-cli (hints only)."""
    try:
        moves = find_moves(before, after, ops, library)
    except Refusal as exc:
        names = schematics(after) or ["(no schematic)"]
        return [WritebackPlan(names[0], [], False, str(exc))]
    plans = []
    for schematic, group in sorted(moves.items()):
        path = project_dir / schematic
        plan = WritebackPlan(schematic, group, False)
        plans.append(plan)
        try:
            if not path.resolve().is_relative_to(project_dir.resolve()):
                raise Refusal(f"{schematic} is outside the project")
            if lock_file(path).exists():
                raise Refusal(
                    f"{path.name} is open in KiCad ({lock_file(path).name} exists); close it "
                    "first, or KiCad will overwrite the change on its next save"
                )
            plan.original = path.read_bytes()
            text = plan.original.decode("utf-8")
            sha = hashlib.sha256(plan.original).hexdigest()
            for move in group:
                text, notes = edit_move(text, move, sha)
                plan.edits.extend(notes)
            plan.modified = text.encode("utf-8")
            plan.diff = "".join(
                difflib.unified_diff(
                    plan.original.decode("utf-8").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    schematic,
                    schematic,
                )
            )
            if verify_with is not None:
                verify(path, plan.modified, group, after, library, schematic, verify_with)
                plan.verified = True
            plan.available = True
        except (Refusal, SchematicError, OSError) as exc:
            plan.reason = str(exc)
    return plans
