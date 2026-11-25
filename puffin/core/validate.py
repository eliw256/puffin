"""Validation rules. Pure and deterministic: same graph, same ordered issues."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from puffin.core.issues import Issue, Severity
from puffin.core.models import ProjectModel, split_endpoint
from puffin.library import PartDefinition, PartLibrary, PartLoadError

RULES: dict[str, Severity] = {
    "assignment-net-mismatch": Severity.ERROR,
    "firmware-mcu-board": Severity.ERROR,
    "flagged-pin": Severity.ERROR,
    "invalid-part": Severity.ERROR,
    "pin-double-assigned": Severity.ERROR,
    "pin-function": Severity.ERROR,
    "pin-net-conflict": Severity.ERROR,
    "unknown-board": Severity.ERROR,
    "unknown-component": Severity.ERROR,
    "unknown-net": Severity.ERROR,
    "unknown-part": Severity.ERROR,
    "unknown-peripheral": Severity.ERROR,
    "unknown-pin": Severity.ERROR,
    "unknown-signal": Severity.ERROR,
    "unknown-variant": Severity.ERROR,
    "unused-override": Severity.WARNING,
}


@dataclass(frozen=True)
class _Assignment:
    component: str
    peripheral: str
    signal: str
    pin: str

    @property
    def endpoint(self) -> str:
        return f"{self.component}.{self.pin}"

    @property
    def name(self) -> str:
        return f"{self.peripheral}.{self.signal}"

    @property
    def where(self) -> str:
        return f"assignments.{self.component}.{self.peripheral}.{self.signal}"


class _Validator:
    def __init__(self, project: ProjectModel, library: PartLibrary) -> None:
        self.project = project
        self.library = library
        self.issues: list[Issue] = []
        self.parts: dict[str, PartDefinition] = {}
        self.assignments = [
            _Assignment(component, peripheral, signal, pin)
            for component, peripherals in project.assignments.items()
            for peripheral, signals in peripherals.items()
            for signal, pin in signals.items()
        ]
        self.nets_by_pin: dict[str, list[str]] = defaultdict(list)
        for net, endpoints in project.nets.items():
            for endpoint in dict.fromkeys(endpoints):
                self.nets_by_pin[endpoint].append(net)

    def add(self, code: str, where: str, message: str, hint: str = "") -> None:
        self.issues.append(Issue(RULES[code], code, where, message, hint))

    def run(self) -> list[Issue]:
        self.check_components()
        self.check_boards()
        self.check_nets()
        self.check_assignments()
        self.check_constraints()
        self.check_flags()
        return sorted(
            set(self.issues),
            key=lambda i: (i.severity != Severity.ERROR, i.code, i.where, i.message),
        )

    def check_components(self) -> None:
        for ref, component in self.project.components.items():
            where = f"components.{ref}"
            if component.board not in self.project.boards:
                known = ", ".join(sorted(self.project.boards)) or "none"
                self.add(
                    "unknown-board",
                    where,
                    f"board '{component.board}' is not defined",
                    f"use a board from `boards` ({known})",
                )
            try:
                part = self.library.get(component.part)
            except PartLoadError as exc:
                self.add(
                    "invalid-part",
                    where,
                    f"part '{component.part}' failed to load: {exc}",
                    "fix the part definition file",
                )
                continue
            if part is None:
                self.add(
                    "unknown-part",
                    where,
                    f"part '{component.part}' is not in the library",
                    f"add parts/{component.part}.yaml or correct the part id",
                )
                continue
            self.parts[ref] = part
            if component.variant is not None and component.variant not in part.variants:
                known = ", ".join(part.variants) or "none"
                self.add(
                    "unknown-variant",
                    where,
                    f"variant '{component.variant}' is not defined for {part.id}",
                    f"use one of: {known}",
                )

    def check_boards(self) -> None:
        for name, board in self.project.boards.items():
            if board.firmware is None:
                continue
            where = f"boards.{name}.firmware.mcu"
            mcu = board.firmware.mcu
            component = self.project.components.get(mcu)
            if component is None:
                self.add(
                    "unknown-component",
                    where,
                    f"MCU '{mcu}' is not a component",
                    "add the component or correct the reference",
                )
            elif component.board != name:
                self.add(
                    "firmware-mcu-board",
                    where,
                    f"MCU '{mcu}' is on board '{component.board}', not '{name}'",
                    f"set {mcu}'s board to '{name}' or choose an MCU on this board",
                )

    def known_pin(self, endpoint: str, where: str) -> bool:
        """Report an unknown component or pin. Unresolved parts are reported once, elsewhere."""
        ref, pin = split_endpoint(endpoint)
        if ref not in self.project.components:
            self.add(
                "unknown-component",
                where,
                f"component '{ref}' does not exist",
                f"add {ref} to `components` or correct the reference",
            )
            return False
        part = self.parts.get(ref)
        if part is None:
            return False
        if pin not in part.pins:
            self.add(
                "unknown-pin",
                where,
                f"{part.id} has no pin '{pin}'",
                "use a pin name from the part definition",
            )
            return False
        return True

    def check_nets(self) -> None:
        for net, endpoints in self.project.nets.items():
            for endpoint in endpoints:
                self.known_pin(endpoint, f"nets.{net}")
        for endpoint, nets in self.nets_by_pin.items():
            if len(nets) > 1:
                names = ", ".join(sorted(nets))
                self.add(
                    "pin-net-conflict",
                    endpoint,
                    f"pin is on multiple nets: {names}",
                    "disconnect it from all but one net",
                )

    def check_assignments(self) -> None:
        by_pin: dict[str, list[_Assignment]] = defaultdict(list)
        by_net: dict[tuple[str, str], list[_Assignment]] = defaultdict(list)
        for item in self.assignments:
            if item.component not in self.project.components:
                self.add(
                    "unknown-component",
                    item.where,
                    f"component '{item.component}' does not exist",
                    f"add {item.component} to `components` or remove this assignment",
                )
                continue
            part = self.parts.get(item.component)
            if part is None:
                continue
            if not self.known_pin(item.endpoint, item.where):
                continue
            by_pin[item.endpoint].append(item)
            self.check_function(item, part)
            nets = self.nets_by_pin.get(item.endpoint, [])
            if not nets:
                self.add(
                    "assignment-net-mismatch",
                    item.where,
                    f"{item.name} is assigned to {item.pin}, but {item.endpoint} is on no net",
                    f"connect {item.endpoint} to a net or move the assignment to the connected pin",
                )
            for net in nets:
                by_net[(item.component, net)].append(item)
                self.check_power_net(item, net)
        for endpoint, items in by_pin.items():
            if len(items) > 1:
                names = ", ".join(sorted(i.name for i in items))
                self.add(
                    "pin-double-assigned",
                    endpoint,
                    f"pin has multiple assignments: {names}",
                    "keep one assignment and move the others to free pins",
                )
        # Only pins of one component can be shorted; a net between two MCUs is a normal link.
        for (_, net), items in by_net.items():
            if len({i.endpoint for i in items}) > 1:
                names = ", ".join(sorted(f"{i.name} ({i.endpoint})" for i in items))
                self.add(
                    "assignment-net-mismatch",
                    f"nets.{net}",
                    f"different assigned signals are shorted together: {names}",
                    "split the net or correct the assignments",
                )

    def check_function(self, item: _Assignment, part: PartDefinition) -> None:
        block = part.peripherals.get(item.peripheral)
        if block is None:
            known = ", ".join(part.peripherals) or "none"
            self.add(
                "unknown-peripheral",
                item.where,
                f"{part.id} has no peripheral '{item.peripheral}'",
                f"use one of: {known}",
            )
            return
        base = item.signal.split(".", 1)[0]
        if base not in block.signals:
            self.add(
                "unknown-signal",
                item.where,
                f"{item.peripheral} has no signal '{base}'",
                f"use one of: {', '.join(block.signals)}",
            )
            return
        legal = part.legal_pins(item.peripheral, base)
        if item.pin not in legal:
            self.add(
                "pin-function",
                item.where,
                f"{item.pin} cannot carry {item.peripheral}.{base}",
                f"legal pins: {', '.join(legal) or 'none'}",
            )

    def check_power_net(self, item: _Assignment, net: str) -> None:
        for endpoint in self.project.nets[net]:
            ref, pin = split_endpoint(endpoint)
            part = self.parts.get(ref)
            definition = part.pins.get(pin) if part else None
            if definition is not None and definition.is_power:
                self.add(
                    "assignment-net-mismatch",
                    item.where,
                    f"{item.name} on {item.endpoint} shares net '{net}' with power pin {endpoint}",
                    "move the signal off the power net",
                )
                return

    def check_constraints(self) -> None:
        for index, constraint in enumerate(self.project.constraints):
            if constraint.net not in self.project.nets:
                self.add(
                    "unknown-net",
                    f"constraints[{index}]",
                    f"net '{constraint.net}' does not exist",
                    "correct the net name or remove the constraint",
                )

    def check_flags(self) -> None:
        assigned: dict[str, list[_Assignment]] = defaultdict(list)
        for item in self.assignments:
            assigned[item.endpoint].append(item)
        overrides = {(o.pin, o.allow) for o in self.project.overrides}
        needed: set[tuple[str, str]] = set()
        for endpoint in sorted(set(self.nets_by_pin) | set(assigned)):
            ref, pin = split_endpoint(endpoint)
            part = self.parts.get(ref)
            if part is None or pin not in part.pins:
                continue
            variant = self.project.components[ref].variant
            for flag in sorted(part.effective_flags(pin, variant)):
                policy = part.flag_policy.get(flag)
                exempt = set(policy.exempt_peripherals) if policy else set()
                uses = assigned.get(endpoint, [])
                if uses and all(u.peripheral in exempt for u in uses):
                    continue
                needed.add((endpoint, flag))
                if (endpoint, flag) not in overrides:
                    reason = f": {policy.description}" if policy and policy.description else ""
                    self.add(
                        "flagged-pin",
                        endpoint,
                        f"pin is flagged '{flag}'{reason}",
                        "use another pin, or add an override: "
                        f"{{pin: {endpoint}, allow: {flag}, note: ...}}",
                    )
        for index, override in enumerate(self.project.overrides):
            where = f"overrides[{index}]"
            if not self.known_pin(override.pin, where):
                continue
            if (override.pin, override.allow) not in needed:
                self.add(
                    "unused-override",
                    where,
                    f"override '{override.allow}' on {override.pin} has no effect",
                    "remove it, or check the pin and flag name",
                )


def validate(project: ProjectModel, library: PartLibrary) -> list[Issue]:
    """Run every rule and return issues, errors first, in a stable order."""
    return _Validator(project, library).run()
