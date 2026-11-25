"""`puffin import kicad`: review and apply KiCad changes to puffin.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from puffin.core import Issue, Op, Project
from puffin.kicad import NetlistError, read_netlist
from puffin.kicad.changes import ChangeSet, build_changes
from puffin.kicad.export import KiCadCliError, export_netlist
from puffin.kicad.state import STATE_PATH, record_sync, schematics


def _endpoints(items: tuple[str, ...]) -> str:
    return "[" + ", ".join(items) + "]"


def render(changes: ChangeSet) -> str:
    """Human-readable change set in the fixed section order of docs/M2_IMPORT.md (h)."""
    lines = [f"import from {changes.schematic}"]
    if changes.unmanaged:
        counts = ", ".join(f"{part} x{n}" for part, n in changes.unmanaged.items())
        lines.append(f"  unmanaged (excluded): {counts}")

    def section(title: str, body: list[str]) -> None:
        if body:
            lines.append(title)
            lines.extend(f"  {line}" for line in body)

    section(
        "1. components",
        [f"+ {c.ref} {c.part} on {c.board}" + (f" [{c.variant}]" if c.variant else "")
         for c in changes.components_added]
        + [f"- {ref}" for ref in changes.components_removed]
        + [f"~ {c.ref} {c.field}: {c.old} -> {c.new}" for c in changes.components_updated],
    )  # fmt: skip
    section(
        "2. nets",
        [f"+ {name} {_endpoints(e)}" for name, e in changes.nets_added]
        + [f"- {name} {_endpoints(e)}" for name, e in changes.nets_removed]
        + [f"~ {old} -> {new}" for old, new in changes.nets_renamed],
    )
    section(
        "3. endpoint changes",
        [
            f"{c.net}: " + " ".join([*(f"-{e}" for e in c.removed), *(f"+{e}" for e in c.added)])
            for c in changes.endpoint_changes
        ],
    )
    section(
        "4. proposed assignment fixes",
        [
            f"{f.component} {f.peripheral}.{f.signal}: {f.old_pin} -> {f.new_pin} (net {f.net})"
            for f in changes.assignment_fixes
        ],
    )
    section("5. ambiguous, left alone", [str(a) for a in changes.ambiguous])
    section("6. warnings", [str(i) for i in changes.warnings])
    return "\n".join(lines)


def _introduced(errors: list[Issue]) -> str:
    body = "\n".join(f"  {str(issue).replace(chr(10), chr(10) + '  ')}" for issue in errors)
    return "7. errors the change would introduce\n" + body


def _netlists(project: Project, args: argparse.Namespace) -> list[tuple[str, str]]:
    names = schematics(project.model)
    if not names:
        raise KiCadCliError("no board sets kicad_schematic; nothing to import")
    if args.netlist is not None:
        if len(names) != 1:
            raise KiCadCliError(
                "--netlist needs exactly one schematic in the project; found " + ", ".join(names)
            )
        try:
            return [(names[0], Path(args.netlist).read_text(encoding="utf-8"))]
        except OSError as exc:
            raise KiCadCliError(f"cannot read {args.netlist}: {exc.strerror or exc}") from exc
    base = project.path.parent
    return [(name, export_netlist(base / name)) for name in names]


def _confirm(prompt: Callable[[str], str]) -> bool:
    return prompt("apply these changes? [y/N] ").strip().lower() in {"y", "yes"}


def cmd_import_kicad(
    project: Project, args: argparse.Namespace, prompt: Callable[[str], str] | None = None
) -> int:
    try:
        sources = _netlists(project, args)
        change_sets = [
            build_changes(project.model, project.library, read_netlist(text), name)
            for name, text in sources
        ]
    except (KiCadCliError, NetlistError) as exc:
        print(f"puffin import: {exc}", file=sys.stderr)
        return 2

    ops: list[Op] = [op for changes in change_sets for op in changes.ops]
    import_errors = [issue for changes in change_sets for issue in changes.errors]
    preview = project.batch(ops, dry_run=True) if ops and not import_errors else None
    introduced = (preview.errors if preview and not preview.ok else []) + import_errors
    in_sync = not ops and not import_errors
    payload: dict[str, Any] = {
        "in_sync": in_sync,
        "ok": not introduced,
        "applied": False,
        "state_updated": False,
        "schematics": [changes.to_dict() for changes in change_sets],
        "introduced_errors": [issue.to_dict() for issue in introduced],
    }

    def report(message: str = "") -> None:
        if args.json:
            print(json.dumps(payload, indent=2))
        elif message:
            print(message)

    def sync_state() -> None:
        """Record schematic hashes, but only for a netlist kicad-cli exported in this run.

        A --netlist file may come from any revision of the schematic (or none), so recording
        the current hash would make `puffin check` report "in sync" when it is not.
        """
        if args.netlist is not None:
            print(
                "puffin import: note: --netlist was used, so drift state "
                f"({STATE_PATH.as_posix()}) was not updated",
                file=sys.stderr,
            )
            return
        for name, _ in sources:
            record_sync(project.path.parent, name)
        payload["state_updated"] = True

    if not args.json:
        for changes in change_sets:
            print(render(changes))
        if introduced:
            print(_introduced(introduced))

    if introduced:
        report("puffin import: nothing applied; fix the errors above first")
        return 1
    if in_sync:
        if not args.dry_run:
            sync_state()
        report("puffin import: in sync, nothing to change")
        return 0
    if args.dry_run:
        report(f"puffin import: dry run, {len(ops)} operation(s) not applied")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("puffin import: refusing to apply without --yes (no terminal)", file=sys.stderr)
            report()
            return 1
        if not _confirm(prompt or input):
            report("puffin import: cancelled, nothing applied")
            return 1

    result = project.batch(ops)
    if not result.ok:  # the project did not change since the preview, so this is a bug guard
        payload["ok"] = False
        payload["introduced_errors"] = [issue.to_dict() for issue in result.errors]
        report("puffin import: rejected on apply; nothing written")
        return 1
    project.save()
    sync_state()
    payload["applied"] = True
    report(f"puffin import: applied {len(ops)} operation(s) to {project.path.name}")
    return 0


def add_parser(commands: Any) -> None:
    importer = commands.add_parser("import", help="import changes from an EDA tool")
    tools = importer.add_subparsers(dest="tool", required=True)
    kicad = tools.add_parser("kicad", help="sync nets and components from KiCad")
    kicad.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    kicad.add_argument("--netlist", help="use a pre-exported kicadsexpr netlist")
    kicad.add_argument("--dry-run", action="store_true", help="show the change set only")
    kicad.add_argument("--yes", action="store_true", help="apply without asking")
    kicad.add_argument("--json", action="store_true", help="machine-readable output")
    kicad.set_defaults(func=cmd_import_kicad)
