"""The project document and the single validated edit API."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from puffin.core import yamlio
from puffin.core.fsio import atomic_write
from puffin.core.issues import Issue, Severity, sort_issues
from puffin.core.models import ProjectModel, split_endpoint
from puffin.core.scope import kicad_overwrite_warnings
from puffin.core.validate import validate
from puffin.library import PartLibrary

Checker = Callable[[ProjectModel, PartLibrary], list[Issue]]
"""An extra check run with validation, e.g. generator preflight wired in by the CLI."""


class ProjectLoadError(Exception):
    """The project file cannot be read as a Puffin project."""


class _Rejected(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class Reference:
    """One place in the graph that points at a component."""

    kind: str  # net | assignment | override | constraint | intent | firmware
    where: str
    detail: str
    blocking: bool = True  # False: informational; removal does not require changing it

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "kind": self.kind,
            "where": self.where,
            "detail": self.detail,
            "blocking": self.blocking,
        }

    def __str__(self) -> str:
        return f"{self.where} ({self.detail})"


@dataclass(frozen=True)
class EditResult:
    """Outcome of one edit. A rejected edit leaves the project unchanged."""

    ok: bool
    operation: str
    applied: bool
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    changed_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "operation": self.operation,
            "applied": self.applied,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "changed_artifacts": self.changed_artifacts,
        }


def _parse_model(document: Any) -> ProjectModel:
    return ProjectModel.model_validate(yamlio.to_plain(document))


def _describe(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "project"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _section(document: Any, name: str, factory: Callable[[], Any]) -> Any:
    if document.get(name) is None:
        if name in document:
            document[name] = factory()
        else:
            yamlio.append_item(document, name, factory())
    return document[name]


class Project:
    """A loaded `puffin.yaml`. All mutation goes through the edit methods."""

    def __init__(
        self,
        path: Path,
        source: bytes,
        library: PartLibrary | None = None,
        checkers: Sequence[Checker] = (),
    ) -> None:
        self.path = path
        self.library = library or PartLibrary.for_project(path.parent)
        self.checkers = list(checkers)
        self._source = source
        self._dirty = False
        try:
            self._document = yamlio.parse(source)
        except (YAMLError, UnicodeDecodeError) as exc:
            raise ProjectLoadError(f"{path}: not valid YAML: {exc}") from exc
        if not isinstance(self._document, CommentedMap):
            raise ProjectLoadError(f"{path}: expected a YAML mapping at the top level")
        try:
            self.model = _parse_model(self._document)
        except ValidationError as exc:
            raise ProjectLoadError(f"{path}: {_describe(exc)}") from exc

    @classmethod
    def load(
        cls, path: str | Path, library: PartLibrary | None = None, checkers: Sequence[Checker] = ()
    ) -> Project:
        resolved = Path(path)
        if resolved.is_dir():
            resolved = resolved / "puffin.yaml"
        try:
            source = resolved.read_bytes()
        except OSError as exc:
            raise ProjectLoadError(f"cannot read {resolved}: {exc.strerror or exc}") from exc
        return cls(resolved, source, library, checkers)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def check(self) -> list[Issue]:
        """Validation plus every extra checker, in one ordered list."""
        return self._issues(self.model)

    def _issues(self, model: ProjectModel) -> list[Issue]:
        found = list(validate(model, self.library))
        for checker in self.checkers:
            found.extend(checker(model, self.library))
        return sort_issues(found)

    def dumps(self) -> bytes:
        """Serialized project. An unedited project returns its exact source bytes."""
        return yamlio.dump(self._document) if self._dirty else self._source

    def references(self, ref: str) -> list[Reference]:
        """Every entry that points at component `ref`, in file order. Read-only.

        Constraints name nets, not components, so a constraint is listed when its net
        includes one of the component's pins. It is informational (blocking=False): other
        components may still be on that net. Every other kind blocks removal.
        """
        return _references(self.model, ref)

    def save(self, path: str | Path | None = None) -> bool:
        """Write atomically. Returns False if the target already has these bytes."""
        target = Path(path) if path is not None else self.path
        data = self.dumps()
        changed = not (target.is_file() and target.read_bytes() == data)
        if changed:
            atomic_write(target, data)
        if target == self.path:
            self._source = data
            self._dirty = False
        return changed

    def batch(
        self,
        ops: Sequence[Op | Mapping[str, Any]],
        *,
        dry_run: bool = False,
        from_kicad: bool = False,
    ) -> EditResult:
        """Apply operations in order to a copy, then judge the final state once.

        The batch is rejected as a whole if any operation fails or if the final state has
        errors that were not already present. Intermediate states are never validated, so
        a swap (two pins trading assignments) succeeds as one batch.

        A batch that changes KiCad-managed connectivity gets a `kicad-overwrite` warning,
        since the next import would revert it. Imports pass `from_kicad=True`: bringing the
        graph in line with KiCad is their purpose.
        """
        parsed = [op if isinstance(op, Op) else Op.from_dict(op) for op in ops]
        operation = parsed[0].label if len(parsed) == 1 else f"batch({len(parsed)} ops)"
        if not parsed:
            return EditResult(True, operation, False)
        before = {issue.key for issue in self.check()}
        candidate = copy.deepcopy(self._document)
        for index, op in enumerate(parsed):
            where = op.label if len(parsed) == 1 else f"ops[{index}] {op.label}"
            try:
                _apply(candidate, op)
            except _Rejected as exc:
                return self._rejected(where, exc.message, exc.hint)
        try:
            model = _parse_model(candidate)
        except ValidationError as exc:
            return self._rejected(operation, _describe(exc), "correct the edit arguments")
        issues = self._issues(model)
        errors = [issue for issue in issues if issue.severity is Severity.ERROR]
        warnings = [issue for issue in issues if issue.severity is Severity.WARNING]
        if not from_kicad:
            warnings = kicad_overwrite_warnings(self.model, model) + warnings
        introduced = [issue for issue in errors if issue.key not in before]
        if introduced:
            return EditResult(False, operation, False, introduced, warnings)
        if not dry_run:
            self._document = candidate
            self.model = model
            self._dirty = True
        return EditResult(True, operation, not dry_run, errors, warnings, [self.path.name])

    @staticmethod
    def _rejected(operation: str, message: str, hint: str) -> EditResult:
        issue = Issue(Severity.ERROR, "edit-rejected", operation, message, hint)
        return EditResult(False, operation, False, [issue])

    def _one(self, name: str, dry_run: bool, **args: Any) -> EditResult:
        return self.batch([Op(name, args)], dry_run=dry_run)

    def add_component(
        self, ref: str, part: str, board: str, variant: str | None = None, *, dry_run: bool = False
    ) -> EditResult:
        return self._one("add_component", dry_run, ref=ref, part=part, board=board, variant=variant)

    def remove_component(self, ref: str, *, dry_run: bool = False) -> EditResult:
        return self._one("remove_component", dry_run, ref=ref)

    def connect(self, net: str, *endpoints: str, dry_run: bool = False) -> EditResult:
        return self._one("connect", dry_run, net=net, endpoints=list(endpoints))

    def disconnect(self, net: str, *endpoints: str, dry_run: bool = False) -> EditResult:
        return self._one("disconnect", dry_run, net=net, endpoints=list(endpoints))

    def assign_pin(
        self, component: str, peripheral: str, signal: str, pin: str, *, dry_run: bool = False
    ) -> EditResult:
        return self._one(
            "assign_pin",
            dry_run,
            component=component,
            peripheral=peripheral,
            signal=signal,
            pin=pin,
        )

    def unassign_pin(
        self, component: str, peripheral: str, signal: str, *, dry_run: bool = False
    ) -> EditResult:
        return self._one(
            "unassign_pin", dry_run, component=component, peripheral=peripheral, signal=signal
        )

    def move_pin(
        self, component: str, from_pin: str, to_pin: str, *, dry_run: bool = False
    ) -> EditResult:
        """Move every net connection and assignment from one pin to another, atomically."""
        return self._one("move_pin", dry_run, component=component, from_pin=from_pin, to_pin=to_pin)

    def add_override(self, pin: str, allow: str, note: str, *, dry_run: bool = False) -> EditResult:
        return self._one("add_override", dry_run, pin=pin, allow=allow, note=note)

    def remove_override(self, pin: str, allow: str, *, dry_run: bool = False) -> EditResult:
        return self._one("remove_override", dry_run, pin=pin, allow=allow)


def _references(model: ProjectModel, ref: str) -> list[Reference]:
    found: list[Reference] = []
    touched_nets: set[str] = set()
    for net, endpoints in model.nets.items():
        for endpoint in endpoints:
            if split_endpoint(endpoint)[0] == ref:
                found.append(Reference("net", f"nets.{net}", endpoint))
                touched_nets.add(net)
    for peripheral, signals in model.assignments.get(ref, {}).items():
        for signal, pin in signals.items():
            where = f"assignments.{ref}.{peripheral}.{signal}"
            found.append(Reference("assignment", where, pin))
    for index, override in enumerate(model.overrides):
        if split_endpoint(override.pin)[0] == ref:
            detail = f"{override.pin} allow {override.allow}"
            found.append(Reference("override", f"overrides[{index}]", detail))
    for index, constraint in enumerate(model.constraints):
        if constraint.net in touched_nets:
            where = f"constraints[{index}]"
            found.append(Reference("constraint", where, constraint.net, blocking=False))
    if ref in model.intent:
        found.append(Reference("intent", f"intent.{ref}", model.intent[ref]))
    for name, board in model.boards.items():
        if board.firmware is not None and board.firmware.mcu == ref:
            found.append(Reference("firmware", f"boards.{name}.firmware.mcu", ref))
    return found


# --- Operations -------------------------------------------------------------------------
#
# Each operation mutates a candidate document in place and raises _Rejected when it cannot
# apply. They never validate: `Project.batch` judges the final state once.


@dataclass(frozen=True)
class Op:
    """One serializable edit: `{"op": "connect", "net": "SDA", "endpoints": ["U1.GPIO4"]}`."""

    op: str
    args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Op:
        args = dict(data)
        name = args.pop("op", None)
        if not isinstance(name, str):
            raise ValueError("operation needs an 'op' name")
        return cls(name, args)

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, **self.args}

    @property
    def label(self) -> str:
        a = self.args
        subject = {
            "add_component": "{ref}",
            "remove_component": "{ref}",
            "set_component": "{ref}",
            "connect": "{net}",
            "disconnect": "{net}",
            "set_net": "{name}",
            "remove_net": "{name}",
            "rename_net": "{old}->{new}",
            "assign_pin": "{component}.{peripheral}.{signal}",
            "unassign_pin": "{component}.{peripheral}.{signal}",
            "set_assignment_pin": "{component}.{peripheral}.{signal}",
            "move_pin": "{component}.{from_pin}->{to_pin}",
            "add_override": "{pin}",
            "remove_override": "{pin}",
        }.get(self.op, "")
        try:
            return f"{self.op}({subject.format(**a)})"
        except (KeyError, IndexError):
            return f"{self.op}(...)"


def _apply(document: Any, op: Op) -> None:
    function = OPERATIONS.get(op.op)
    if function is None:
        raise _Rejected(f"unknown operation '{op.op}'", "one of: " + ", ".join(sorted(OPERATIONS)))
    try:
        inspect.signature(function).bind(document, **op.args)
    except TypeError as exc:
        raise _Rejected(f"bad arguments for {op.op}: {exc}") from exc
    function(document, **op.args)


def _add_component(
    document: Any, ref: str, part: str, board: str, variant: str | None = None
) -> None:
    components = _section(document, "components", CommentedMap)
    if ref in components:
        raise _Rejected(f"component '{ref}' already exists", "choose another reference")
    entry = CommentedMap(part=part, board=board)
    if variant is not None:
        entry["variant"] = variant
    yamlio.append_item(components, ref, entry)


def _set_component(
    document: Any, ref: str, part: str, board: str, variant: str | None = None
) -> None:
    """Create the component, or update part, board and variant in place."""
    components = _section(document, "components", CommentedMap)
    entry = components.get(ref)
    if entry is None:
        _add_component(document, ref, part, board, variant)
        return
    entry["part"] = part
    entry["board"] = board
    if variant is not None:
        entry["variant"] = variant
    elif "variant" in entry:
        yamlio.remove_item(entry, "variant")


def _remove_component(document: Any, ref: str) -> None:
    components = document.get("components") or {}
    if ref not in components:
        raise _Rejected(f"component '{ref}' does not exist")
    try:
        model = _parse_model(document)
    except ValidationError as exc:
        raise _Rejected(_describe(exc)) from exc
    found = _references(model, ref)
    blockers = [r for r in found if r.blocking]
    if blockers:
        hint = "remove these references first: " + "; ".join(map(str, blockers))
        notes = [r for r in found if not r.blocking]
        if notes:
            hint += "\nnote: also affected, no change required: "
            hint += "; ".join(map(str, notes))
        raise _Rejected(f"component '{ref}' is still referenced in {len(blockers)} place(s)", hint)
    yamlio.remove_item(components, ref)


def _connect(document: Any, net: str, endpoints: list[str]) -> None:
    if not endpoints:
        raise _Rejected("connect needs at least one pin", "pass COMPONENT.PIN")
    nets = _section(document, "nets", CommentedMap)
    existing = list(nets.get(net) or [])
    for endpoint in endpoints:
        if endpoint in existing:
            raise _Rejected(f"{endpoint} is already on net '{net}'")
        existing.append(endpoint)
    if net in nets and nets[net] is not None:
        nets[net].extend(existing[len(nets[net]) :])
    else:
        yamlio.append_item(nets, net, yamlio.flow_seq(existing))


def _disconnect(document: Any, net: str, endpoints: list[str]) -> None:
    nets = document.get("nets") or {}
    if net not in nets:
        raise _Rejected(f"net '{net}' does not exist")
    for endpoint in endpoints:
        if endpoint not in nets[net]:
            raise _Rejected(f"{endpoint} is not on net '{net}'")
        nets[net].remove(endpoint)
    if not nets[net]:
        yamlio.remove_item(nets, net)


def _set_net(document: Any, name: str, endpoints: list[str]) -> None:
    """Create the net or replace its endpoints, keeping its position and comments."""
    if not endpoints:
        raise _Rejected(f"net '{name}' needs at least one pin", "use remove_net to delete it")
    if len(set(endpoints)) != len(endpoints):
        raise _Rejected(f"net '{name}' lists a pin twice")
    nets = _section(document, "nets", CommentedMap)
    if name in nets:
        nets[name] = yamlio.flow_seq(list(endpoints))
    else:
        yamlio.append_item(nets, name, yamlio.flow_seq(list(endpoints)))


def _remove_net(document: Any, name: str) -> None:
    nets = document.get("nets") or {}
    if name not in nets:
        raise _Rejected(f"net '{name}' does not exist")
    yamlio.remove_item(nets, name)


def _rename_net(document: Any, old: str, new: str) -> None:
    """Rename a net, its constraints, and an intent entry keyed by the old name."""
    nets = document.get("nets")
    if not nets or old not in nets:
        raise _Rejected(f"net '{old}' does not exist")
    if new in nets:
        raise _Rejected(f"net '{new}' already exists")
    yamlio.rename_key(nets, old, new)
    for constraint in document.get("constraints") or []:
        if constraint.get("net") == old:
            constraint["net"] = new
    intent = document.get("intent")
    if intent and old in intent and new not in intent:
        yamlio.rename_key(intent, old, new)


def _assign_pin(document: Any, component: str, peripheral: str, signal: str, pin: str) -> None:
    assignments = _section(document, "assignments", CommentedMap)
    if component not in assignments:
        yamlio.append_item(assignments, component, CommentedMap())
    peripherals = assignments[component]
    if peripheral not in peripherals:
        yamlio.append_item(peripherals, peripheral, CommentedMap())
    signals = peripherals[peripheral]
    if signal in signals:
        raise _Rejected(
            f"{component} {peripheral}.{signal} is already assigned to {signals[signal]}",
            "use move_pin to change it",
        )
    yamlio.append_item(signals, signal, pin)


def _signals(document: Any, component: str, peripheral: str, signal: str) -> Any:
    try:
        signals = document["assignments"][component][peripheral]
        if signal not in signals:
            raise KeyError(signal)
    except (KeyError, TypeError) as exc:
        raise _Rejected(f"{component} has no assignment {peripheral}.{signal}") from exc
    return signals


def _unassign_pin(document: Any, component: str, peripheral: str, signal: str) -> None:
    signals = _signals(document, component, peripheral, signal)
    assignments = document["assignments"]
    peripherals = assignments[component]
    # Delete the highest level that would become empty, so spacing after it survives.
    if len(signals) > 1:
        yamlio.remove_item(signals, signal)
    elif len(peripherals) > 1:
        yamlio.remove_item(peripherals, peripheral)
    else:
        yamlio.remove_item(assignments, component)


def _set_assignment_pin(
    document: Any, component: str, peripheral: str, signal: str, pin: str
) -> None:
    """Point an existing assignment at another pin. Nets are left alone."""
    _signals(document, component, peripheral, signal)[signal] = pin


def _move_pin(document: Any, component: str, from_pin: str, to_pin: str) -> None:
    source = f"{component}.{from_pin}"
    target = f"{component}.{to_pin}"
    if from_pin == to_pin:
        raise _Rejected(f"{source} is already the target pin")
    moved = False
    for endpoints in (document.get("nets") or {}).values():
        for index, endpoint in enumerate(endpoints):
            if endpoint == source:
                endpoints[index] = target
                moved = True
    peripherals = (document.get("assignments") or {}).get(component) or {}
    for signals in peripherals.values():
        for signal, pin in signals.items():
            if pin == from_pin:
                signals[signal] = to_pin
                moved = True
    if not moved:
        raise _Rejected(f"{source} has no net or assignment to move")


def _add_override(document: Any, pin: str, allow: str, note: str) -> None:
    overrides = _section(document, "overrides", CommentedSeq)
    if any(item["pin"] == pin and item["allow"] == allow for item in overrides):
        raise _Rejected(f"override '{allow}' on {pin} already exists")
    if not overrides:
        overrides.fa.set_block_style()
    entry = CommentedMap(pin=pin, allow=allow, note=note)
    entry.fa.set_flow_style()
    yamlio.append_item(overrides, None, entry)


def _remove_override(document: Any, pin: str, allow: str) -> None:
    overrides = document.get("overrides") or []
    for index, item in enumerate(overrides):
        if item["pin"] == pin and item["allow"] == allow:
            yamlio.remove_item(overrides, index)
            return
    raise _Rejected(f"no override '{allow}' on {pin}")


OPERATIONS: dict[str, Callable[..., None]] = {
    "add_component": _add_component,
    "add_override": _add_override,
    "assign_pin": _assign_pin,
    "connect": _connect,
    "disconnect": _disconnect,
    "move_pin": _move_pin,
    "remove_component": _remove_component,
    "remove_net": _remove_net,
    "remove_override": _remove_override,
    "rename_net": _rename_net,
    "set_assignment_pin": _set_assignment_pin,
    "set_component": _set_component,
    "set_net": _set_net,
    "unassign_pin": _unassign_pin,
}
"""Every operation `Project.batch` accepts, by name."""


__all__ = [
    "OPERATIONS",
    "Checker",
    "EditResult",
    "Op",
    "Project",
    "ProjectLoadError",
    "Reference",
    "split_endpoint",
]
