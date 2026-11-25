"""Builds the change set that brings the graph in line with a KiCad netlist. Pure.

KiCad is authoritative for managed components and their nets; the graph stays
authoritative for assignments, constraints, overrides and intent (docs/M2_IMPORT.md).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from puffin.core import Issue, Op, ProjectModel, Severity
from puffin.core.models import split_endpoint
from puffin.kicad.mapping import map_pin
from puffin.kicad.netlist import Net, Netlist, NetlistComponent
from puffin.library import PartDefinition, PartLibrary, PartLoadError

IMPORT_CODES: dict[str, Severity] = {
    "import-duplicate-net": Severity.ERROR,
    "import-foreign-component": Severity.ERROR,
    "import-missing-board": Severity.ERROR,
    "import-no-part-field": Severity.WARNING,
    "import-series-passive": Severity.WARNING,
    "import-sheet-path": Severity.WARNING,
    "import-unknown-board": Severity.ERROR,
    "import-unknown-part": Severity.ERROR,
    "import-unlabelled-net": Severity.WARNING,
    "import-unmapped-pin": Severity.ERROR,
}

POWER_PIN_TYPES = frozenset({"power_in", "power_out"})


def _issue(code: str, where: str, message: str, hint: str = "") -> Issue:
    return Issue(IMPORT_CODES[code], code, where, message, hint)


@dataclass(frozen=True)
class ComponentChange:
    ref: str
    part: str
    board: str
    variant: str | None


@dataclass(frozen=True)
class FieldChange:
    ref: str
    field: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class EndpointChange:
    net: str
    removed: tuple[str, ...]
    added: tuple[str, ...]


@dataclass(frozen=True)
class AssignmentFix:
    component: str
    peripheral: str
    signal: str
    old_pin: str
    new_pin: str
    net: str


@dataclass(frozen=True)
class Ambiguity:
    component: str
    peripheral: str
    signal: str
    pin: str
    reason: str

    def __str__(self) -> str:
        return f"{self.component} {self.peripheral}.{self.signal} on {self.pin}: {self.reason}"


@dataclass
class ChangeSet:
    """What an import would do, in review order. Every list is sorted."""

    schematic: str
    components_added: list[ComponentChange] = field(default_factory=list)
    components_removed: list[str] = field(default_factory=list)
    components_updated: list[FieldChange] = field(default_factory=list)
    nets_added: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    nets_removed: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    nets_renamed: list[tuple[str, str]] = field(default_factory=list)
    endpoint_changes: list[EndpointChange] = field(default_factory=list)
    assignment_fixes: list[AssignmentFix] = field(default_factory=list)
    ambiguous: list[Ambiguity] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    unmanaged: dict[str, int] = field(default_factory=dict)
    ops: list[Op] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.ops

    def to_dict(self) -> dict[str, Any]:
        return {
            "schematic": self.schematic,
            "components": {
                "added": [vars(c) for c in self.components_added],
                "removed": self.components_removed,
                "updated": [vars(c) for c in self.components_updated],
            },
            "nets": {
                "added": [{"name": n, "endpoints": list(e)} for n, e in self.nets_added],
                "removed": [{"name": n, "endpoints": list(e)} for n, e in self.nets_removed],
                "renamed": [{"old": o, "new": n} for o, n in self.nets_renamed],
            },
            "endpoint_changes": [
                {"net": c.net, "removed": list(c.removed), "added": list(c.added)}
                for c in self.endpoint_changes
            ],
            "assignment_fixes": [vars(f) for f in self.assignment_fixes],
            "ambiguous": [vars(a) for a in self.ambiguous],
            "warnings": [i.to_dict() for i in self.warnings],
            "errors": [i.to_dict() for i in self.errors],
            "unmanaged": dict(sorted(self.unmanaged.items())),
            "ops": [op.to_dict() for op in self.ops],
        }


@dataclass(frozen=True)
class _ImportedNet:
    name: str
    endpoints: frozenset[str]
    source: Net


def import_net_name(net: Net, endpoints: frozenset[str]) -> str:
    """Graph name for a KiCad net: labels lose a root `/`; `Net-(...)` becomes AUTO(...)."""
    if net.kind == "auto":
        return f"AUTO({','.join(sorted(endpoints))})"
    return net.name[1:] if net.name.startswith("/") else net.name


def build_changes(
    model: ProjectModel, library: PartLibrary, netlist: Netlist, schematic: str
) -> ChangeSet:
    """The change set for importing `netlist`, exported from `schematic`.

    `schematic` is the `kicad_schematic` string of the boards it covers. Only components
    on those boards can be added, updated or removed by this import.
    """
    return _Builder(model, library, netlist, schematic).build()


class _Builder:
    def __init__(
        self, model: ProjectModel, library: PartLibrary, netlist: Netlist, schematic: str
    ) -> None:
        self.model = model
        self.library = library
        self.netlist = netlist
        self.changes = ChangeSet(schematic)
        self.boards = {
            name for name, board in model.boards.items() if board.kicad_schematic == schematic
        }
        self.owned = {ref for ref, comp in model.components.items() if comp.board in self.boards}
        self.managed: dict[str, PartDefinition] = {}

    # --- components -------------------------------------------------------------------

    def build(self) -> ChangeSet:
        self._components()
        imported = self._imported_nets()
        self._nets(imported)
        self._series_passives()
        changes = self.changes
        changes.warnings.sort()
        changes.errors.sort()
        changes.ambiguous.sort(key=lambda a: (a.component, a.peripheral, a.signal))
        return changes

    def _components(self) -> None:
        unmanaged: Counter[str] = Counter()
        for ref in sorted(self.netlist.components):
            comp = self.netlist.components[ref]
            part_id = comp.puffin("Puffin_Part")
            existing = self.model.components.get(ref)
            if part_id is None and existing is None:
                unmanaged[comp.libpart or "(no library part)"] += 1
                continue
            if existing is not None and ref not in self.owned:
                self.changes.errors.append(
                    _issue(
                        "import-foreign-component",
                        ref,
                        f"{ref} is on board '{existing.board}', which does not use "
                        f"{self.changes.schematic}",
                        "re-annotate the symbol in KiCad or move the component to a board "
                        "on this schematic",
                    )
                )
                continue
            if part_id is None:
                assert existing is not None
                part_id = existing.part
                self.changes.warnings.append(
                    _issue(
                        "import-no-part-field",
                        ref,
                        f"{ref} matched the graph by reference designator only",
                        f"add a Puffin_Part field ({part_id}) to the symbol so re-annotation "
                        "cannot bind it to the wrong part",
                    )
                )
            self._component(comp, part_id, existing)
        self.changes.unmanaged = dict(sorted(unmanaged.items()))

        for ref in sorted(self.owned - set(self.netlist.components)):
            self.changes.components_removed.append(ref)

    def _component(self, comp: NetlistComponent, part_id: str, existing: Any) -> None:
        ref = comp.ref
        try:
            part = self.library.get(part_id)
        except PartLoadError:
            part = None
        if part is None:
            self.changes.errors.append(
                _issue(
                    "import-unknown-part",
                    ref,
                    f"{ref} uses part '{part_id}', which is not in the part library",
                    "fix the Puffin_Part field or add the part definition",
                )
            )
            return
        board = comp.puffin("Puffin_Board") or (existing.board if existing else None)
        if board is None:
            self.changes.errors.append(
                _issue(
                    "import-missing-board",
                    ref,
                    f"new component {ref} has no Puffin_Board field",
                    "add a Puffin_Board field naming its board, e.g. "
                    + ", ".join(sorted(self.boards)),
                )
            )
            return
        if board not in self.boards:
            self.changes.errors.append(
                _issue(
                    "import-unknown-board",
                    ref,
                    f"{ref}'s Puffin_Board '{board}' is not a board on {self.changes.schematic}",
                    "boards on this schematic: " + ", ".join(sorted(self.boards)),
                )
            )
            return
        # An absent (or empty) Puffin_Variant keeps the graph's variant.
        variant = comp.puffin("Puffin_Variant") or (existing.variant if existing else None)
        self.managed[ref] = part
        if existing is None:
            self.changes.components_added.append(ComponentChange(ref, part_id, board, variant))
        else:
            for name, old, new in (
                ("part", existing.part, part_id),
                ("board", existing.board, board),
                ("variant", existing.variant, variant),
            ):
                if old != new:
                    self.changes.components_updated.append(FieldChange(ref, name, old, new))
        if existing is None or (existing.part, existing.board, existing.variant) != (
            part_id,
            board,
            variant,
        ):
            self.changes.ops.append(
                Op("set_component", {"ref": ref, "part": part_id, "board": board,
                                     "variant": variant})
            )  # fmt: skip

    # --- nets -------------------------------------------------------------------------

    def _endpoints(self, net: Net) -> frozenset[str]:
        found: set[str] = set()
        for node in net.nodes:
            part = self.managed.get(node.ref)
            if part is None:
                continue
            pin = map_pin(part, node)
            if pin is None:
                label = f"pad {node.pin}" + (f" ({node.pinfunction})" if node.pinfunction else "")
                self.changes.errors.append(
                    _issue(
                        "import-unmapped-pin",
                        f"{node.ref}.{node.pin}",
                        f"{node.ref} {label} on net {net.name} matches no pin of {part.id}",
                        "fix the symbol's pin numbers or names, or the part definition",
                    )
                )
                continue
            found.add(f"{node.ref}.{pin}")
        return frozenset(found)

    def _imported_nets(self) -> dict[str, _ImportedNet]:
        imported: dict[str, _ImportedNet] = {}
        for net in self.netlist.nets:
            if net.kind == "unconnected":
                continue
            endpoints = self._endpoints(net)
            if not endpoints:
                continue
            name = import_net_name(net, endpoints)
            if net.name.startswith("/") and "/" in name:
                self.changes.warnings.append(
                    _issue(
                        "import-sheet-path",
                        name,
                        f"net {net.name} comes from a sub-sheet; its path is kept in the name",
                        "use a global label if the net should have a plain name",
                    )
                )
            if name in imported:
                self.changes.errors.append(
                    _issue(
                        "import-duplicate-net",
                        name,
                        f"two KiCad nets import as '{name}'",
                        "rename one of the labels in KiCad",
                    )
                )
                continue
            if net.kind == "auto":
                assigned = sorted(e for e in endpoints if self._assignments_on(e))
                if assigned:
                    self.changes.warnings.append(
                        _issue(
                            "import-unlabelled-net",
                            name,
                            f"unlabelled net {net.name} reaches assigned pin {', '.join(assigned)}",
                            "add a net label in KiCad so the name is stable",
                        )
                    )
            imported[name] = _ImportedNet(name, endpoints, net)
        return imported

    def _assignments_on(self, endpoint: str) -> list[tuple[str, str]]:
        component, pin = split_endpoint(endpoint)
        return [
            (peripheral, signal)
            for peripheral, signals in self.model.assignments.get(component, {}).items()
            for signal, assigned in signals.items()
            if assigned == pin
        ]

    def _owned_endpoint(self, endpoint: str) -> bool:
        return split_endpoint(endpoint)[0] in self.owned | set(self.managed)

    def _nets(self, imported: dict[str, _ImportedNet]) -> None:
        graph = {
            name: frozenset(e for e in endpoints if self._owned_endpoint(e))
            for name, endpoints in self.model.nets.items()
        }
        graph = {name: endpoints for name, endpoints in graph.items() if endpoints}

        pairs: dict[str, str] = {}  # imported name -> graph name
        for name in imported:
            if name in graph:
                pairs[name] = name
        pairs.update(_overlap_pairs(
            {n: i.endpoints for n, i in imported.items() if n not in pairs},
            {n: e for n, e in graph.items() if n not in pairs.values()},
        ))  # fmt: skip

        renames = sorted((old, new) for new, old in pairs.items() if old != new)
        self.changes.nets_renamed = renames
        # Names match before overlap, so a rename target is never a net still in scope.
        self.changes.ops.extend(Op("rename_net", {"old": o, "new": n}) for o, n in renames)

        for name in sorted(imported):
            new = imported[name].endpoints
            if name not in pairs:
                self.changes.nets_added.append((name, tuple(sorted(new))))
                self.changes.ops.append(Op("set_net", {"name": name, "endpoints": sorted(new)}))
                continue
            old_name = pairs[name]
            old = graph[old_name]
            if old != new:
                self.changes.endpoint_changes.append(
                    EndpointChange(name, tuple(sorted(old - new)), tuple(sorted(new - old)))
                )
                kept = _replace_in_place(self.model.nets[old_name], old, new)
                self.changes.ops.append(Op("set_net", {"name": name, "endpoints": kept}))
            self._sync_assignments(name, old, new, imported)

        for old_name in sorted(set(graph) - set(pairs.values())):
            remaining = [e for e in self.model.nets[old_name] if e not in graph[old_name]]
            self.changes.nets_removed.append((old_name, tuple(sorted(graph[old_name]))))
            if remaining:
                self.changes.ops.append(Op("set_net", {"name": old_name, "endpoints": remaining}))
            else:
                self.changes.ops.append(Op("remove_net", {"name": old_name}))
            for endpoint in sorted(graph[old_name]):
                self._ambiguous_for(endpoint, f"net {old_name} is gone", imported)

        for ref in self.changes.components_removed:
            self.changes.ops.append(Op("remove_component", {"ref": ref}))

    def _sync_assignments(
        self, net: str, old: frozenset[str], new: frozenset[str], imported: dict[str, Any]
    ) -> None:
        by_component: dict[str, tuple[set[str], set[str]]] = defaultdict(lambda: (set(), set()))
        for endpoint in old - new:
            component, pin = split_endpoint(endpoint)
            by_component[component][0].add(pin)
        for endpoint in new - old:
            component, pin = split_endpoint(endpoint)
            by_component[component][1].add(pin)
        for component in sorted(by_component):
            left, arrived = by_component[component]
            if len(left) == 1 and len(arrived) == 1:
                (from_pin,), (to_pin,) = left, arrived
                for peripheral, signal in self._assignments_on(f"{component}.{from_pin}"):
                    self.changes.assignment_fixes.append(
                        AssignmentFix(component, peripheral, signal, from_pin, to_pin, net)
                    )
                    self.changes.ops.append(
                        Op("set_assignment_pin", {"component": component,
                                                  "peripheral": peripheral, "signal": signal,
                                                  "pin": to_pin})
                    )  # fmt: skip
                continue
            if arrived:
                reason = f"{len(left)} pin(s) left net {net} and {len(arrived)} arrived"
            else:
                reason = f"net {net} no longer reaches any {component} pin it had"
            for pin in sorted(left):
                self._ambiguous_for(f"{component}.{pin}", reason, imported)

    def _ambiguous_for(self, endpoint: str, reason: str, imported: dict[str, Any]) -> None:
        component, pin = split_endpoint(endpoint)
        elsewhere = sorted(n for n, i in imported.items() if endpoint in i.endpoints)
        if elsewhere:
            reason += f"; {pin} is now on {', '.join(elsewhere)}"
        for peripheral, signal in self._assignments_on(endpoint):
            self.changes.ambiguous.append(Ambiguity(component, peripheral, signal, pin, reason))

    # --- series passives --------------------------------------------------------------

    def _series_passives(self) -> None:
        """Warn when an unmanaged two-pin part splits a signal between managed pins."""
        names: dict[int, str] = {}
        sides: dict[str, frozenset[str]] = {}  # imported net name -> its managed endpoints
        managed_nets: set[int] = set()
        power_nets: set[int] = set()
        pins_by_ref: dict[str, list[int]] = defaultdict(list)
        for index, net in enumerate(self.netlist.nets):
            if net.kind == "unconnected":
                continue
            endpoints = frozenset(
                f"{n.ref}.{p}"
                for n in net.nodes
                if n.ref in self.managed and (p := map_pin(self.managed[n.ref], n))
            )
            names[index] = import_net_name(net, endpoints)
            sides[names[index]] = endpoints
            if endpoints:
                managed_nets.add(index)
            if any(n.pintype in POWER_PIN_TYPES for n in net.nodes):
                power_nets.add(index)
            for node in net.nodes:
                if node.ref not in self.managed and node.ref not in self.model.components:
                    pins_by_ref[node.ref].append(index)

        renamed = dict(self.changes.nets_renamed)
        constrained = {renamed.get(c.net, c.net) for c in self.model.constraints}
        for ref in sorted(pins_by_ref):
            nets = pins_by_ref[ref]
            if len(nets) != 2 or nets[0] == nets[1]:
                continue
            if not set(nets) <= managed_nets or set(nets) & power_nets:
                continue
            a, b = sorted((names[i] for i in nets), key=lambda n: (n.startswith("AUTO("), n))
            libpart = self.netlist.components[ref].libpart
            if b.startswith("AUTO(") and not a.startswith("AUTO("):
                message = f"{a} is split by {ref} ({libpart}); the far side imports as {b}"
            else:
                message = f"{a} and {b} are joined through {ref} ({libpart}) and import apart"
            halves = [n for n in (a, b) if n in constrained]
            if len(halves) == 1:
                side = ", ".join(sorted(sides[halves[0]]))
                message += f"; the constraint covers only the {side} side, net {halves[0]}"
            self.changes.warnings.append(
                _issue(
                    "import-series-passive",
                    ref,
                    message,
                    "Puffin treats each side as its own net until pass-through lands in v1",
                )
            )


def _replace_in_place(current: list[str], old: frozenset[str], new: frozenset[str]) -> list[str]:
    """New endpoint list in graph order: a pin that moved takes the place of the one it
    replaced on the same component; other additions go at the end, sorted."""
    arriving = sorted(new - old)
    result: list[str] = []
    for endpoint in current:
        if endpoint in new or endpoint not in old:
            result.append(endpoint)
            continue
        component = split_endpoint(endpoint)[0]
        same = [e for e in arriving if split_endpoint(e)[0] == component]
        if same:
            result.append(same[0])
            arriving.remove(same[0])
    return result + arriving


def _overlap_pairs(
    imported: dict[str, frozenset[str]], graph: dict[str, frozenset[str]]
) -> dict[str, str]:
    """Pair nets that are each other's unique best endpoint overlap. Ties never pair."""

    def best(source: frozenset[str], candidates: dict[str, frozenset[str]]) -> str | None:
        scores = {name: len(source & other) for name, other in candidates.items()}
        top = max(scores.values(), default=0)
        winners = [name for name, score in scores.items() if score == top]
        return winners[0] if top > 0 and len(winners) == 1 else None

    pairs: dict[str, str] = {}
    for name in sorted(imported):
        match = best(imported[name], graph)
        if match is not None and best(graph[match], imported) == name:
            pairs[name] = match
    return pairs
