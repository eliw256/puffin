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

from puffin.core import Issue, Op, Project, ProjectLoadError, Severity, sort_issues
from puffin.core.models import split_endpoint
from puffin.core.scope import schematics
from puffin.core.views import board_view
from puffin.generators import check_generation, generate
from puffin.generators.writer import apply_writes, plan_writes
from puffin.kicad import NetlistError, read_netlist
from puffin.kicad.changes import build_changes
from puffin.kicad.export import KiCadCliError, export_netlist
from puffin.kicad.state import drift_issues, record_sync, schematic_hash
from puffin.library import PartDefinition, PartLoadError
from puffin.mcp.docs import DocIndex, PathEscape, inside

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
}


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
        return {"result": result, "diff": diff, "generated": generated}

    def _register(self, proposal: Proposal) -> None:
        self.proposals.pop(proposal.proposal_id, None)
        self.proposals[proposal.proposal_id] = proposal
        while len(self.proposals) > MAX_PROPOSALS:
            self.proposals.pop(next(iter(self.proposals)))

    @staticmethod
    def _proposal_id(kind: str, base: str, ops: list[dict[str, Any]], extra: str = "") -> str:
        canonical = json.dumps([kind, base, ops, extra], sort_keys=True)
        return "p_" + sha256(canonical.encode())[:12]

    def propose_edit(self, ops: list[dict[str, Any]]) -> dict[str, Any]:
        project = self.project()
        base = sha256(project.dumps())
        preview = self._preview(project, ops)
        result = preview["result"]
        summary = [Op.from_dict(op).label if "op" in op else "invalid op" for op in ops]
        proposal_id = None
        if result.ok:
            proposal_id = self._proposal_id("edit", base, ops)
            self._register(Proposal(proposal_id, "edit", ops, base, summary))
        return {
            "ok": result.ok,
            "proposal_id": proposal_id,
            "summary": summary,
            "diff": preview["diff"],
            "introduced_errors": [] if result.ok else issues_json(result.errors),
            "warnings": issues_json(result.warnings),
            "generated": preview["generated"],
        }

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
        result = project.batch(proposal.ops, from_kicad=proposal.kind == "import")
        if not result.ok:  # cannot happen for an unchanged file; kept as a guard
            return {"ok": False, "applied": False, "errors": issues_json(result.errors)}
        if proposal.ops:
            project.save()
        if proposal.kind == "import":
            for name in proposal.schematic_hashes:
                record_sync(self.root, name, project.model)
        self._loaded_sha = sha256(project.dumps())
        self._append_history(proposal, self._loaded_sha)
        return {
            "ok": True,
            "applied": True,
            "proposal_id": proposal_id,
            "changed_artifacts": [self.path.name] if proposal.ops else [],
            "warnings": issues_json(result.warnings),
        }

    def _append_history(self, proposal: Proposal, file_hash: str) -> None:
        entry = {
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
