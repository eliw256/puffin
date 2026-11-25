"""The project document and the single validated edit API."""

from __future__ import annotations

import copy
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from puffin.core import yamlio
from puffin.core.issues import Issue, Severity
from puffin.core.models import ProjectModel, split_endpoint
from puffin.core.validate import validate
from puffin.library import PartLibrary


class ProjectLoadError(Exception):
    """The project file cannot be read as a Puffin project."""


class _Rejected(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


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

    def __init__(self, path: Path, source: bytes, library: PartLibrary | None = None) -> None:
        self.path = path
        self.library = library or PartLibrary.for_project(path.parent)
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
    def load(cls, path: str | Path, library: PartLibrary | None = None) -> Project:
        resolved = Path(path)
        if resolved.is_dir():
            resolved = resolved / "puffin.yaml"
        try:
            source = resolved.read_bytes()
        except OSError as exc:
            raise ProjectLoadError(f"cannot read {resolved}: {exc.strerror or exc}") from exc
        return cls(resolved, source, library)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def check(self) -> list[Issue]:
        return validate(self.model, self.library)

    def dumps(self) -> bytes:
        """Serialized project. An unedited project returns its exact source bytes."""
        return yamlio.dump(self._document) if self._dirty else self._source

    def save(self, path: str | Path | None = None) -> bool:
        """Write atomically. Returns False if the target already has these bytes."""
        target = Path(path) if path is not None else self.path
        data = self.dumps()
        if target.is_file() and target.read_bytes() == data:
            changed = False
        else:
            descriptor, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                if target.exists():
                    os.chmod(temp, target.stat().st_mode)
                os.replace(temp, target)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
            changed = True
        if target == self.path:
            self._source = data
            self._dirty = False
        return changed

    def _edit(self, operation: str, mutate: Callable[[Any], None], dry_run: bool) -> EditResult:
        before = {issue.key for issue in self.check()}
        candidate = copy.deepcopy(self._document)
        try:
            mutate(candidate)
            model = _parse_model(candidate)
        except _Rejected as exc:
            return self._rejected(operation, exc.message, exc.hint)
        except ValidationError as exc:
            return self._rejected(operation, _describe(exc), "correct the edit arguments")
        issues = validate(model, self.library)
        errors = [issue for issue in issues if issue.severity is Severity.ERROR]
        warnings = [issue for issue in issues if issue.severity is Severity.WARNING]
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

    def add_component(
        self, ref: str, part: str, board: str, variant: str | None = None, *, dry_run: bool = False
    ) -> EditResult:
        def mutate(document: Any) -> None:
            components = _section(document, "components", CommentedMap)
            if ref in components:
                raise _Rejected(f"component '{ref}' already exists", "choose another reference")
            entry = CommentedMap(part=part, board=board)
            if variant is not None:
                entry["variant"] = variant
            yamlio.append_item(components, ref, entry)

        return self._edit(f"add_component({ref})", mutate, dry_run)

    def remove_component(self, ref: str, *, dry_run: bool = False) -> EditResult:
        def mutate(document: Any) -> None:
            components = document.get("components") or {}
            if ref not in components:
                raise _Rejected(f"component '{ref}' does not exist")
            del components[ref]

        return self._edit(f"remove_component({ref})", mutate, dry_run)

    def connect(self, net: str, *endpoints: str, dry_run: bool = False) -> EditResult:
        def mutate(document: Any) -> None:
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

        return self._edit(f"connect({net})", mutate, dry_run)

    def disconnect(self, net: str, *endpoints: str, dry_run: bool = False) -> EditResult:
        def mutate(document: Any) -> None:
            nets = document.get("nets") or {}
            if net not in nets:
                raise _Rejected(f"net '{net}' does not exist")
            for endpoint in endpoints:
                if endpoint not in nets[net]:
                    raise _Rejected(f"{endpoint} is not on net '{net}'")
                nets[net].remove(endpoint)
            if not nets[net]:
                del nets[net]

        return self._edit(f"disconnect({net})", mutate, dry_run)

    def assign_pin(
        self, component: str, peripheral: str, signal: str, pin: str, *, dry_run: bool = False
    ) -> EditResult:
        def mutate(document: Any) -> None:
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

        return self._edit(f"assign_pin({component}.{peripheral}.{signal})", mutate, dry_run)

    def unassign_pin(
        self, component: str, peripheral: str, signal: str, *, dry_run: bool = False
    ) -> EditResult:
        def mutate(document: Any) -> None:
            try:
                peripherals = document["assignments"][component]
                del peripherals[peripheral][signal]
            except (KeyError, TypeError) as exc:
                raise _Rejected(f"{component} has no assignment {peripheral}.{signal}") from exc
            if not peripherals[peripheral]:
                del peripherals[peripheral]
            if not peripherals:
                del document["assignments"][component]

        return self._edit(f"unassign_pin({component}.{peripheral}.{signal})", mutate, dry_run)

    def move_pin(
        self, component: str, from_pin: str, to_pin: str, *, dry_run: bool = False
    ) -> EditResult:
        """Move every net connection and assignment from one pin to another, atomically."""
        source = f"{component}.{from_pin}"
        target = f"{component}.{to_pin}"

        def mutate(document: Any) -> None:
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

        return self._edit(f"move_pin({source}->{to_pin})", mutate, dry_run)

    def add_override(self, pin: str, allow: str, note: str, *, dry_run: bool = False) -> EditResult:
        def mutate(document: Any) -> None:
            overrides = _section(document, "overrides", CommentedSeq)
            if any(item["pin"] == pin and item["allow"] == allow for item in overrides):
                raise _Rejected(f"override '{allow}' on {pin} already exists")
            if not overrides:
                overrides.fa.set_block_style()
            entry = CommentedMap(pin=pin, allow=allow, note=note)
            entry.fa.set_flow_style()
            yamlio.append_item(overrides, None, entry)

        return self._edit(f"add_override({pin})", mutate, dry_run)

    def remove_override(self, pin: str, allow: str, *, dry_run: bool = False) -> EditResult:
        def mutate(document: Any) -> None:
            overrides = document.get("overrides") or []
            for index, item in enumerate(overrides):
                if item["pin"] == pin and item["allow"] == allow:
                    del overrides[index]
                    return
            raise _Rejected(f"no override '{allow}' on {pin}")

        return self._edit(f"remove_override({pin})", mutate, dry_run)


__all__ = ["EditResult", "Project", "ProjectLoadError", "split_endpoint"]
