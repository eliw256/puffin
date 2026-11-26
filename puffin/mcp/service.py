"""Tool logic for the MCP server, independent of the protocol (docs/M3_MCP.md).

Every public method returns a JSON-ready dict with `"ok"` at the top, or raises `ToolError`
when the request cannot be carried out (the server turns that into an `isError` result).
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from puffin.core import EditResult, Issue, Op, Project, ProjectLoadError, Severity, sort_issues
from puffin.core.fsio import atomic_write
from puffin.core.models import split_endpoint
from puffin.core.scope import KICAD_OVERWRITE, schematics
from puffin.core.views import board_view
from puffin.generators import check_generation, generate
from puffin.generators.writer import apply_writes, plan_writes
from puffin.kicad import NetlistError, checkpoint, read_netlist
from puffin.kicad.changes import build_changes
from puffin.kicad.export import KiCadCliError, export_netlist
from puffin.kicad.mapping import map_pin
from puffin.kicad.netlist import Node
from puffin.kicad.schematic import SchematicError, read_schematic, symbol_graphics
from puffin.kicad.state import drift_issues, record_sync, schematic_hash
from puffin.kicad.writeback import WritebackPlan, lock_file, plan_writeback
from puffin.library import PartDefinition, PartLoadError
from puffin.mcp.blocks import block_view
from puffin.mcp.docs import DocIndex, PathEscape, inside
from puffin.mcp.symbols import generated_symbol
from puffin.mcp.targets import issue_target

PUFFIN_API = 1
HISTORY_PATH = Path(".puffin") / "history.jsonl"
MAX_PROPOSALS = 64

MCP_CODES: dict[str, Severity] = {
    "internal-error": Severity.ERROR,
    "kicad-cli-failed": Severity.ERROR,
    "not-found": Severity.ERROR,
    "output-blocked": Severity.ERROR,
    "path-escape": Severity.ERROR,
    "project-unreadable": Severity.ERROR,
    "proposal-unknown": Severity.ERROR,
    "stale-proposal": Severity.ERROR,
    "writeback-unavailable": Severity.ERROR,
}


_FAMILY_PREFIXES = (("spi", "spi"), ("i2c", "i2c"), ("uart", "uart"), ("adc", "adc"),
                    ("usb", "usb"), ("jtag", "usb"), ("gpio", "gpio"))  # fmt: skip


def pin_family(assignments: list[dict[str, str]], rail: str | None, is_power: bool) -> str:
    """What a pin does in this design, for colour coding: its first assignment's peripheral
    family; otherwise ground/power from its rail; otherwise unused."""
    if assignments:
        peripheral = assignments[0]["peripheral"]
        return next((f for prefix, f in _FAMILY_PREFIXES if peripheral.startswith(prefix)), "gpio")
    if rail == "gnd":
        return "ground"
    if rail or is_power:
        return "power"
    return "unused"


class ToolError(Exception):
    """The request could not be carried out. Carries one structured issue."""

    def __init__(self, code: str, where: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.issue = Issue(MCP_CODES[code], code, where, message, hint)


def issues_json(issues: list[Issue]) -> list[dict[str, str]]:
    return [issue.to_dict() for issue in issues]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Proposal:
    proposal_id: str
    kind: str  # "edit" | "import"
    ops: list[dict[str, Any]]
    base_sha256: str
    summary: list[str]
    schematic_hashes: dict[str, str | None] = field(default_factory=dict)
    writeback: list[WritebackPlan] = field(default_factory=list)


class PuffinService:
    """One project, reloaded whenever `puffin.yaml` changes on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path / "puffin.yaml" if path.is_dir() else path
        self.root = self.path.parent
        self._project: Project | None = None
        self._loaded_sha: str | None = None
        self.proposals: dict[str, Proposal] = {}

    # --- loading ------------------------------------------------------------------------

    def project(self) -> Project:
        try:
            source = self.path.read_bytes()
        except OSError as exc:
            raise ToolError(
                "project-unreadable",
                str(self.path),
                f"cannot read {self.path}: {exc}",
                "check the path passed to `puffin mcp`",
            ) from exc
        digest = sha256(source)
        if self._project is None or digest != self._loaded_sha:
            try:
                self._project = Project(self.path, source, checkers=[check_generation])
            except ProjectLoadError as exc:
                raise ToolError(
                    "project-unreadable", self.path.name, str(exc), "fix puffin.yaml"
                ) from exc
            self._loaded_sha = digest
        return self._project

    def _part(self, part_id: str) -> PartDefinition:
        project = self.project()
        for root in project.library.roots:
            candidate = root / f"{part_id}.yaml"
            if candidate.exists() and not inside(candidate, root):
                raise ToolError(
                    "path-escape",
                    part_id,
                    f"part '{part_id}' resolves outside its library",
                    "part files must live inside parts/ or the bundled library, not link out",
                )
        try:
            part = project.library.get(part_id)
        except PartLoadError as exc:
            raise ToolError(
                "not-found",
                part_id,
                f"part '{part_id}' failed to load: {exc}",
                "fix the part definition file",
            ) from exc
        if part is None:
            raise ToolError(
                "not-found",
                part_id,
                f"no part '{part_id}' in the part library",
                "get_project lists the part ids the project uses",
            )
        return part

    def _docs(self) -> DocIndex:
        return DocIndex(list(self.project().library.roots))

    def _component(self, ref: str) -> tuple[Project, PartDefinition]:
        project = self.project()
        component = project.model.components.get(ref)
        if component is None:
            known = ", ".join(sorted(project.model.components)) or "none"
            raise ToolError("not-found", ref, f"no component '{ref}'", f"components: {known}")
        return project, self._part(component.part)

    # --- read tools ---------------------------------------------------------------------

    def get_project(self, board: str | None = None) -> dict[str, Any]:
        model = self.project().model
        if board is not None and board not in model.boards:
            known = ", ".join(sorted(model.boards)) or "none"
            raise ToolError("not-found", board, f"no board '{board}'", f"boards: {known}")
        view = board_view(model, board)
        return {"ok": True, "puffin_api": PUFFIN_API, "schema": view.pop("puffin"), **view}

    def _assignments_by_pin(self, project: Project, ref: str) -> dict[str, list[dict[str, str]]]:
        found: dict[str, list[dict[str, str]]] = defaultdict(list)
        for peripheral, signals in project.model.assignments.get(ref, {}).items():
            for signal, pin in signals.items():
                found[pin].append({"peripheral": peripheral, "signal": signal})
        return found

    def _net_of(self, project: Project) -> dict[str, str]:
        return {e: net for net, endpoints in project.model.nets.items() for e in endpoints}

    def query_pins(
        self, component: str, pins: list[str] | None = None, used_only: bool = False
    ) -> dict[str, Any]:
        project, part = self._component(component)
        variant = project.model.components[component].variant
        if pins:
            unknown = [p for p in pins if p not in part.pins]
            if unknown:
                raise ToolError(
                    "not-found",
                    f"{component}.{unknown[0]}",
                    f"{part.id} has no pin '{unknown[0]}'",
                    "omit pins to list every pin of the part",
                )
        assigned = self._assignments_by_pin(project, component)
        net_of = self._net_of(project)
        issues = project.check()
        result = []
        for name, pin in part.pins.items():
            if pins and name not in pins:
                continue
            endpoint = f"{component}.{name}"
            if used_only and endpoint not in net_of and name not in assigned:
                continue
            flags = sorted(part.effective_flags(name, variant))
            sources = {
                f: "part" if f in pin.flags else f"variant:{variant or 'any'}" for f in flags
            }
            wheres = {endpoint} | {
                f"assignments.{component}.{a['peripheral']}.{a['signal']}" for a in assigned[name]
            }
            result.append(
                {
                    "pin": name,
                    "numbers": pin.numbers,
                    "datasheet_name": pin.datasheet_name,
                    "description": pin.description,
                    "description_source": part.pin_description_source if pin.description else None,
                    "type": pin.type,
                    "rail": pin.rail,
                    "functions": pin.functions,
                    "flags": flags,
                    "flags_source": sources,
                    "net": net_of.get(endpoint),
                    "assignments": assigned[name],
                    "overrides": [
                        {"allow": o.allow, "note": o.note}
                        for o in project.model.overrides
                        if o.pin == endpoint
                    ],
                    "issues": issues_json([i for i in issues if i.where in wheres]),
                    "family": pin_family(assigned[name], pin.rail, pin.is_power),
                }
            )
        return {"ok": True, "component": component, "part": part.id, "variant": variant,
                "pins": result}  # fmt: skip

    def query_net(self, net: str) -> dict[str, Any]:
        project = self.project()
        model = project.model
        if net not in model.nets:
            raise ToolError("not-found", net, f"no net '{net}'", "query get_project for net names")
        endpoints = []
        for endpoint in model.nets[net]:
            ref, pin = split_endpoint(endpoint)
            component = model.components.get(ref)
            definition = None
            if component is not None:
                try:
                    part = project.library.get(component.part)
                except PartLoadError:
                    part = None
                definition = part.pins.get(pin) if part else None
            endpoints.append(
                {
                    "endpoint": endpoint,
                    "component": ref,
                    "board": component.board if component else None,
                    "pin": pin,
                    "type": definition.type if definition else None,
                    "rail": definition.rail if definition else None,
                    "assignments": self._assignments_by_pin(project, ref)[pin],
                }
            )
        boards = sorted({e["board"] for e in endpoints if e["board"]})
        constraints = [
            c.model_dump(exclude={"net"}, exclude_none=True)
            for c in model.constraints
            if c.net == net
        ]
        wheres = {f"nets.{net}"}
        return {
            "ok": True,
            "net": net,
            "endpoints": endpoints,
            "boards": boards,
            "cross_board": len(boards) > 1,
            "constraints": constraints,
            "intent": model.intent.get(net),
            "issues": issues_json([i for i in project.check() if i.where in wheres]),
        }

    def legal_pins(self, component: str, peripheral: str, signal: str) -> dict[str, Any]:
        project, part = self._component(component)
        block = part.peripherals.get(peripheral)
        if block is None:
            raise ToolError(
                "not-found", f"{component}.{peripheral}", f"{part.id} has no peripheral "
                f"'{peripheral}'", "peripherals: " + ", ".join(part.peripherals)
            )  # fmt: skip
        base = signal.split(".", 1)[0]
        if base not in block.signals:
            raise ToolError(
                "not-found", f"{peripheral}.{signal}", f"{peripheral} has no signal '{base}'",
                "signals: " + ", ".join(block.signals),
            )  # fmt: skip
        model = project.model
        variant = model.components[component].variant
        current = model.assignments.get(component, {}).get(peripheral, {}).get(signal)
        assigned = self._assignments_by_pin(project, component)
        net_of = self._net_of(project)
        overrides = {(o.pin, o.allow) for o in model.overrides}
        candidates = []
        for pin in part.legal_pins(peripheral, base):
            endpoint = f"{component}.{pin}"
            flags = sorted(part.effective_flags(pin, variant))
            needs = [
                f
                for f in flags
                if peripheral not in (part.flag_policy[f].exempt_peripherals
                                      if f in part.flag_policy else [])
                and (endpoint, f) not in overrides
            ]  # fmt: skip
            others = [
                a for a in assigned[pin] if (a["peripheral"], a["signal"]) != (peripheral, signal)
            ]
            if pin == current:
                status = "current"
            elif others:
                status = "in_use"
            elif endpoint in net_of:
                status = "connected"
            elif needs:
                status = "needs_override"
            else:
                status = "free"
            candidates.append(
                {"pin": pin, "status": status, "flags": flags, "needs_override": needs,
                 "net": net_of.get(endpoint), "assigned": others}
            )  # fmt: skip
        return {
            "ok": True,
            "component": component,
            "peripheral": peripheral,
            "signal": signal,
            "base": base,
            "routing": block.routable,
            "current_pin": current,
            "candidates": candidates,
        }

    def get_part(self, part: str) -> dict[str, Any]:
        definition = self._part(part)
        sections = self._docs().by_part.get(part, [])
        return {
            "ok": True,
            "part": definition.model_dump(mode="json", exclude_none=True),
            "has_datasheet": bool(sections or definition.docs.datasheet),
            "sources": definition.sources,
            "doc_sections": sections,
        }

    def search_docs(self, query: str, part: str | None = None, limit: int = 8) -> dict[str, Any]:
        return {"ok": True, "results": self._docs().search(query, part, limit)}

    def read_doc_section(self, section_id: str) -> dict[str, Any]:
        try:
            section = self._docs().get(section_id)
        except PathEscape as exc:
            raise ToolError(
                "path-escape", section_id, "section ids never contain '..' or extra '/'",
                "use an id from search_docs or get_part",
            ) from exc  # fmt: skip
        if section is None:
            raise ToolError(
                "not-found", section_id, f"no doc section '{section_id}'",
                "use an id from search_docs or get_part",
            )  # fmt: skip
        try:
            sources = self._part(section.part).sources
        except ToolError:
            sources = []
        return {
            "ok": True,
            "part": section.part,
            "doc": section.doc,
            "section_id": section.section_id,
            "heading": section.heading,
            "sources": sources,
            "text": section.text,
            "citation": f"{section.part} #{section.slug}",
        }

    def check(self) -> dict[str, Any]:
        project = self.project()
        issues = sort_issues([*project.check(), *drift_issues(self.root, project.model)])
        errors = sum(i.severity is Severity.ERROR for i in issues)
        return {"ok": errors == 0, "errors": errors, "warnings": len(issues) - errors,
                "issues": issues_json(issues)}  # fmt: skip

    # --- proposals ----------------------------------------------------------------------

    def _preview(
        self, project: Project, ops: list[dict[str, Any]], from_kicad: bool = False
    ) -> dict[str, Any]:
        """Apply `ops` to a copy: the result, the YAML diff, and each header's diff."""
        before = project.dumps()
        clone = Project(project.path, before, project.library, project.checkers)
        result = clone.batch(ops, from_kicad=from_kicad)
        after = clone.dumps() if result.ok else before
        diff = "".join(
            difflib.unified_diff(
                before.decode().splitlines(keepends=True),
                after.decode().splitlines(keepends=True),
                "puffin.yaml",
                "puffin.yaml",
            )
        )
        generated = []
        if result.ok:
            old = {f.path: f for f in generate(project.model, project.library).files}
            for new in generate(clone.model, clone.library).files:
                previous = old.get(new.path)
                if previous is None or previous.content != new.content:
                    generated.append(
                        {
                            "board": new.board,
                            "path": new.path,
                            "diff": "".join(
                                difflib.unified_diff(
                                    (previous.content if previous else b"")
                                    .decode()
                                    .splitlines(keepends=True),
                                    new.content.decode().splitlines(keepends=True),
                                    new.path,
                                    new.path,
                                )
                            ),
                        }
                    )
        return {"result": result, "diff": diff, "generated": generated, "model": clone.model}

    def _register(self, proposal: Proposal) -> None:
        self.proposals.pop(proposal.proposal_id, None)
        self.proposals[proposal.proposal_id] = proposal
        while len(self.proposals) > MAX_PROPOSALS:
            self.proposals.pop(next(iter(self.proposals)))

    @staticmethod
    def _proposal_id(kind: str, base: str, ops: list[dict[str, Any]], extra: str = "") -> str:
        canonical = json.dumps([kind, base, ops, extra], sort_keys=True)
        return "p_" + sha256(canonical.encode())[:12]

    def propose_edit(self, ops: list[dict[str, Any]], writeback: bool = False) -> dict[str, Any]:
        """Dry-run `ops`. With `writeback`, also plan and verify the schematic edit."""
        project = self.project()
        base = sha256(project.dumps())
        preview = self._preview(project, ops)
        result = preview["result"]
        summary = [Op.from_dict(op).label if "op" in op else "invalid op" for op in ops]
        warnings = list(result.warnings)
        response: dict[str, Any] = {
            "ok": result.ok,
            "proposal_id": None,
            "base_sha256": base,
            "summary": summary,
            "diff": preview["diff"],
            "introduced_errors": [] if result.ok else issues_json(result.errors),
            "warnings": [],
            "generated": preview["generated"],
        }
        overwrite = [w for w in warnings if w.code == KICAD_OVERWRITE]
        plans: list[WritebackPlan] = []
        if result.ok and (writeback or overwrite):
            parsed = [Op.from_dict(op) for op in ops]
            plans = plan_writeback(
                self.root, project.model, preview["model"], parsed, project.library,
                verify_with=export_netlist if writeback else None,
            )  # fmt: skip
            if not writeback:
                warnings = [self._offer_writeback(w, plans) for w in warnings]
        if writeback and result.ok:
            response["writeback"] = [plan.to_dict() for plan in plans]
            unavailable = [plan for plan in plans if not plan.available]
            if not plans:
                unavailable_reason = "this proposal changes no KiCad-managed net"
            else:
                unavailable_reason = "; ".join(f"{p.schematic}: {p.reason}" for p in unavailable)
            if not plans or unavailable:
                response["ok"] = False
                response["errors"] = [
                    Issue(MCP_CODES["writeback-unavailable"], "writeback-unavailable",
                          "writeback", unavailable_reason,
                          "propose without writeback, or make the change in KiCad").to_dict()
                ]  # fmt: skip
                response["warnings"] = issues_json(warnings)
                return response
            warnings = [w for w in warnings if w.code != KICAD_OVERWRITE] + [
                Issue(
                    Severity.WARNING,
                    "kicad-writeback",
                    f"kicad_schematic {plan.schematic}",
                    "applying also writes "
                    + plan.schematic
                    + ": "
                    + "; ".join(plan.edits)
                    + " (verified with kicad-cli)",
                    "review it in KiCad afterwards, then run `puffin writeback confirm`",
                )
                for plan in plans
            ]
        response["warnings"] = issues_json(warnings)
        if result.ok:
            extra = json.dumps({"writeback": writeback, "sch": [p.original_sha256 for p in plans]})
            kind = "edit+writeback" if writeback else "edit"
            proposal_id = self._proposal_id(kind, base, ops, extra)
            self._register(Proposal(proposal_id, kind, ops, base, summary,
                                    writeback=plans if writeback else []))  # fmt: skip
            response["proposal_id"] = proposal_id
        return response

    @staticmethod
    def _offer_writeback(warning: Issue, plans: list[WritebackPlan]) -> Issue:
        """Rewrite a kicad-overwrite hint to say whether write-back can apply the change."""
        if warning.code != KICAD_OVERWRITE:
            return warning
        schematic = warning.where.removeprefix("kicad_schematic ")
        plan = next((p for p in plans if p.schematic == schematic), None)
        if plan is not None and plan.available:
            hint = (
                f"write-back can apply this to {schematic}: propose again with writeback "
                "(or make the same change in KiCad, then run `puffin import kicad`)"
            )
        else:
            reason = plan.reason if plan is not None else "it is not a pin move"
            hint = (f"write-back can't apply this ({reason}); make the same change in "
                    f"{schematic}, then run `puffin import kicad`")  # fmt: skip
        return Issue(warning.severity, warning.code, warning.where, warning.message, hint)

    def propose_import_kicad(self) -> dict[str, Any]:
        project = self.project()
        names = schematics(project.model)
        if not names:
            raise ToolError(
                "not-found",
                "boards",
                "no board sets kicad_schematic; nothing to import",
                "set boards.<name>.kicad_schematic in puffin.yaml",
            )
        change_sets = []
        hashes: dict[str, str | None] = {}
        for name in names:
            path = self.root / name
            if not inside(path, self.root):
                raise ToolError(
                    "path-escape",
                    name,
                    f"schematic {name} is outside the project",
                    "kicad_schematic must be a path inside the project directory",
                )
            hashes[name] = schematic_hash(self.root, name)
            try:
                netlist = read_netlist(export_netlist(path))
            except (KiCadCliError, NetlistError) as exc:
                raise ToolError(
                    "kicad-cli-failed",
                    name,
                    str(exc),
                    "install KiCad 8+ or set PUFFIN_KICAD_CLI to its kicad-cli",
                ) from exc
            change_sets.append(build_changes(project.model, project.library, netlist, name))
        ops = [op.to_dict() for changes in change_sets for op in changes.ops]
        import_errors = [issue for changes in change_sets for issue in changes.errors]
        preview = self._preview(project, ops, from_kicad=True)
        result = preview["result"]
        introduced = import_errors + ([] if result.ok else result.errors)
        base = sha256(project.dumps())
        proposal_id = None
        if not introduced:
            proposal_id = self._proposal_id("import", base, ops, json.dumps(hashes, sort_keys=True))
            summary = [Op.from_dict(op).label for op in ops] or ["record KiCad sync (in sync)"]
            self._register(Proposal(proposal_id, "import", ops, base, summary, hashes))
        return {
            "ok": not introduced,
            "proposal_id": proposal_id,
            "base_sha256": base,
            "in_sync": not ops and not import_errors,
            "schematics": [changes.to_dict() for changes in change_sets],
            "diff": preview["diff"],
            "introduced_errors": issues_json(introduced),
            "generated": preview["generated"],
        }

    def apply_edit(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.proposals.pop(proposal_id, None)
        if proposal is None:
            raise ToolError(
                "proposal-unknown", proposal_id, f"no open proposal '{proposal_id}'",
                "proposals are single-use and end on server restart; call propose_edit again",
            )  # fmt: skip
        project = self.project()
        if sha256(project.dumps()) != proposal.base_sha256:
            raise ToolError(
                "stale-proposal", proposal_id, "puffin.yaml changed since this proposal",
                "propose again and show the engineer the new diff",
            )  # fmt: skip
        for name, recorded in proposal.schematic_hashes.items():
            if schematic_hash(self.root, name) != recorded:
                raise ToolError(
                    "stale-proposal", proposal_id, f"{name} changed since this proposal",
                    "call propose_import_kicad again",
                )  # fmt: skip
        for plan in proposal.writeback:
            path = self.root / plan.schematic
            if not path.is_file() or sha256(path.read_bytes()) != plan.original_sha256:
                raise ToolError(
                    "stale-proposal", proposal_id, f"{plan.schematic} changed since this proposal",
                    "propose again with writeback",
                )  # fmt: skip
            if lock_file(path).exists():
                raise ToolError(
                    "writeback-unavailable", proposal_id, f"{path.name} is open in KiCad",
                    "close it in KiCad, then propose again",
                )  # fmt: skip
        undo = project.inverse(proposal.ops)
        if proposal.writeback:
            result = self._apply_with_writeback(project, proposal)
        else:
            result = project.batch(proposal.ops, from_kicad=proposal.kind == "import")
            if not result.ok:  # cannot happen for an unchanged file; kept as a guard
                return {"ok": False, "applied": False, "errors": issues_json(result.errors)}
            if proposal.ops:
                project.save()
            if proposal.kind == "import":
                for name in proposal.schematic_hashes:
                    record_sync(self.root, name, project.model)
        self._loaded_sha = sha256(project.dumps())
        self._append_history(proposal, self._loaded_sha, undo)
        changed = [self.path.name] if proposal.ops else []
        changed += [plan.schematic for plan in proposal.writeback]
        return {
            "ok": True,
            "applied": True,
            "proposal_id": proposal_id,
            "changed_artifacts": changed,
            "warnings": issues_json([w for w in result.warnings if w.code != KICAD_OVERWRITE]),
        }

    def _apply_with_writeback(self, project: Project, proposal: Proposal) -> EditResult:
        """Schematic(s), puffin.yaml and state.json together, or none of them."""
        schematics_touched = [plan.schematic for plan in proposal.writeback]
        targets = checkpoint.files_for(self.path.name, schematics_touched)
        originals = {rel: (self.root / rel).read_bytes() if (self.root / rel).is_file() else None
                     for rel in targets}  # fmt: skip
        source_before = project.dumps()
        checkpoint.save(self.root, targets)
        try:
            for plan in proposal.writeback:
                atomic_write(self.root / plan.schematic, plan.modified)
            result = project.batch(proposal.ops)
            if not result.ok:
                raise RuntimeError("the edit no longer validates")
            project.save()
            for name in schematics_touched:
                record_sync(self.root, name, project.model)
            checkpoint.bump(self.root)
        except Exception:
            for rel, data in originals.items():
                if data is None:
                    (self.root / rel).unlink(missing_ok=True)
                else:
                    atomic_write(self.root / rel, data)
            self._project = Project(self.path, source_before, checkers=[check_generation])
            checkpoint.discard_if_unused(self.root)
            raise
        return result

    def _append_history(
        self, proposal: Proposal, file_hash: str, undo: list[dict[str, Any]] | None
    ) -> None:
        entry = {
            "undo": undo,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": "apply_edit",
            "proposal_id": proposal.proposal_id,
            "kind": proposal.kind,
            "ops": proposal.ops,
            "summary": proposal.summary,
            "sha256": file_hash,
        }
        path = self.root / HISTORY_PATH
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    # --- history and undo ---------------------------------------------------------------

    def history(self, limit: int = 10) -> dict[str, Any]:
        """The most recent applied proposals, newest first."""
        path = self.root / HISTORY_PATH
        entries: list[dict[str, Any]] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
        recent = list(reversed(entries))[: max(1, limit)]
        return {
            "ok": True,
            "entries": [
                {"time": e.get("time"), "kind": e.get("kind"), "summary": e.get("summary", []),
                 "proposal_id": e.get("proposal_id"), "can_undo": e.get("undo") is not None}
                for e in recent
            ],
        }  # fmt: skip

    def propose_undo(self, writeback: bool = False) -> dict[str, Any]:
        """Propose the inverse of the most recent applied proposal, as a normal proposal."""
        path = self.root / HISTORY_PATH
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        if not lines:
            raise ToolError("not-found", "history", "nothing to undo", "apply an edit first")
        entry = json.loads(lines[-1])
        if entry.get("undo") is None:
            raise ToolError(
                "not-found", "history", "the last change was recorded before undo support",
                "make the inverse change by hand",
            )  # fmt: skip
        result = self.propose_edit(entry["undo"], writeback=writeback)
        result["undo_of"] = {"proposal_id": entry.get("proposal_id"),
                             "summary": entry.get("summary", [])}  # fmt: skip
        result["changed_since"] = sha256(self.project().dumps()) != entry.get("sha256")
        return result

    # --- views for the UI ---------------------------------------------------------------

    def fingerprint(self) -> dict[str, Any]:
        """Hashes of every file a proposal depends on; the UI compares them for staleness."""
        project = self.project()
        return {
            "ok": True,
            "project": sha256(project.dumps()),
            "schematics": {
                name: schematic_hash(self.root, name) for name in schematics(project.model)
            },
        }

    def symbol_view(self, component: str) -> dict[str, Any]:
        """The component as drawable primitives, from its KiCad symbol when the schematic has
        it (the same geometry write-back uses), else a generated two-sided box."""
        project, part = self._component(component)
        board = project.model.boards.get(project.model.components[component].board)
        schematic = board.kicad_schematic if board else None
        view: dict[str, Any] | None = None
        source = "generated"
        if schematic and (self.root / schematic).is_file():
            try:
                sch = read_schematic((self.root / schematic).read_text(encoding="utf-8"))
                if component in sch.instances:
                    view = symbol_graphics(sch, component)
                    source = "schematic"
            except SchematicError:
                view = None
        if view is None:
            view = generated_symbol(component, part)
        pin_by_number = {}
        for pin in view["pins"]:
            name = map_pin(part, Node(component, pin["number"], pin["name"] or None, None))
            if name:
                pin_by_number[pin["number"]] = name
        return {"ok": True, "component": component, "part": part.id, "source": source,
                "symbol": view, "pin_by_number": pin_by_number}  # fmt: skip

    def block_view(self) -> dict[str, Any]:
        project = self.project()
        return block_view(project.model, project.library)

    def diagnostics(self) -> dict[str, Any]:
        """check() plus, per issue, what it points at (pin, net, component, ...)."""
        result = self.check()
        model = self.project().model
        result["targets"] = [issue_target(model, issue["where"]) for issue in result["issues"]]
        return result

    def writeback_status(self) -> dict[str, Any]:
        pending = checkpoint.read(self.root)
        return {"ok": True, "pending": pending.pending if pending else 0,
                "files": sorted(pending.files) if pending else []}  # fmt: skip

    def writeback_confirm(self) -> dict[str, Any]:
        """The engineer accepts every pending write-back (never exposed over MCP)."""
        return {"ok": True, "confirmed": checkpoint.confirm(self.root)}

    def writeback_restore(self) -> dict[str, Any]:
        """Undo every pending write-back (never exposed over MCP)."""
        restored = checkpoint.restore(self.root)
        self._project = None  # puffin.yaml may have changed underneath us
        return {"ok": True, "restored": restored}

    # --- generate -----------------------------------------------------------------------

    def generate(self, board: str | None = None, check_only: bool = False) -> dict[str, Any]:
        project = self.project()
        result = generate(project.model, project.library, board)
        if not result.ok:
            return {"ok": False, "files": [], "issues": issues_json(result.issues),
                    "errors": issues_json([i for i in result.issues
                                           if i.severity is Severity.ERROR])}  # fmt: skip
        plans = plan_writes(self.root, result.files)
        files = []
        for plan in plans:
            added, removed = plan.line_changes()
            entry: dict[str, Any] = {"board": plan.file.board, "path": plan.file.path,
                                     "action": plan.action, "lines_added": added,
                                     "lines_removed": removed}  # fmt: skip
            if plan.action == "blocked":
                entry["reason"] = plan.reason
            files.append(entry)
        blocked = [
            Issue(MCP_CODES["output-blocked"], "output-blocked", p.file.path, p.reason, p.hint)
            for p in plans
            if p.action == "blocked"
        ]
        if not blocked and not check_only:
            apply_writes(plans)
        response: dict[str, Any] = {
            "ok": not blocked,
            "check_only": check_only,
            "files": files,
            "issues": issues_json(result.issues),
        }
        if blocked:
            response["errors"] = issues_json(blocked)
        return response
