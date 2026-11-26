"""CLI entry point. Exit codes: 0 success, 1 errors or stale output, 2 project unreadable."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from puffin import __version__
from puffin.cli import edit, gen, importer
from puffin.core import Issue, Project, ProjectLoadError, Severity, sort_issues
from puffin.core.views import board_view
from puffin.generators import check_generation
from puffin.kicad.state import drift_issues


def _counts(issues: list[Issue]) -> tuple[int, int]:
    errors = sum(issue.severity is Severity.ERROR for issue in issues)
    return errors, len(issues) - errors


def cmd_check(project: Project, args: argparse.Namespace) -> int:
    issues = sort_issues([*project.check(), *drift_issues(project.path.parent, project.model)])
    errors, warnings = _counts(issues)
    if args.json:
        payload = {
            "ok": errors == 0,
            "errors": errors,
            "warnings": warnings,
            "issues": [issue.to_dict() for issue in issues],
        }
        print(json.dumps(payload, indent=2))
    else:
        for issue in issues:
            print(issue)
        print(f"{project.model.project}: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def _print_view(data: dict[str, Any]) -> None:
    print(f"project: {data['project']} (schema {data['puffin']})")
    print("\nboards:")
    for name, board in data["boards"].items():
        firmware = board.get("firmware")
        detail = (
            f"{firmware['framework']}/{firmware['target']}, mcu {firmware['mcu']}"
            if firmware
            else "no firmware"
        )
        print(f"  {name}: {detail}")
    print("\ncomponents:")
    for ref, item in data["components"].items():
        variant = f" [{item['variant']}]" if "variant" in item else ""
        print(f"  {ref}: {item['part']}{variant} on {item['board']}")
    print("\nnets:")
    for net, endpoints in data["nets"].items():
        print(f"  {net}: {', '.join(endpoints)}")
    print("\nassignments:")
    for ref, peripherals in data["assignments"].items():
        for peripheral, signals in peripherals.items():
            for signal, pin in signals.items():
                print(f"  {ref} {peripheral}.{signal} -> {pin}")
    if data["constraints"]:
        print("\nconstraints:")
        for item in data["constraints"]:
            limit = f"max {item['max_freq_hz']:,} Hz" if "max_freq_hz" in item else "no limits"
            print(f"  {item['net']}: {limit}")
    if data["overrides"]:
        print("\noverrides:")
        for item in data["overrides"]:
            print(f"  {item['pin']} allow {item['allow']}: {item['note']}")


def cmd_show(project: Project, args: argparse.Namespace) -> int:
    if args.board is not None and args.board not in project.model.boards:
        known = ", ".join(sorted(project.model.boards)) or "none"
        print(f"puffin: unknown board '{args.board}' (boards: {known})", file=sys.stderr)
        return 2
    data = board_view(project.model, args.board)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _print_view(data)
    return 0


def cmd_ui(project: Project, args: argparse.Namespace) -> int:
    from puffin.web.server import serve as serve_ui  # imported lazily: only the UI needs it

    serve_ui(project.path, args.port, open_browser=not args.no_browser)
    return 0


def cmd_mcp(project: Project, args: argparse.Namespace) -> int:
    from puffin.mcp.server import serve  # imported lazily: the SDK is only needed here

    serve(project.path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="puffin", description="Puffin project tools")
    parser.add_argument("--version", action="version", version=f"puffin {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="validate a project")
    check.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.set_defaults(func=cmd_check)

    show = commands.add_parser("show", help="print the project graph")
    show.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    show.add_argument("--board", help="limit output to one board")
    show.add_argument("--json", action="store_true", help="machine-readable output")
    show.set_defaults(func=cmd_show)

    gen.add_parser(commands)
    importer.add_parser(commands)
    edit.add_parsers(commands)

    ui = commands.add_parser("ui", help="open the local web UI (binds 127.0.0.1 only)")
    ui.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    ui.add_argument("--port", type=int, default=None, help="port on 127.0.0.1 (default: any free)")
    ui.add_argument("--no-browser", action="store_true", help="print the URL, don't open it")
    ui.set_defaults(func=cmd_ui)

    serve = commands.add_parser("mcp", help="serve the project to an AI model over MCP (stdio)")
    serve.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    serve.set_defaults(func=cmd_mcp)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project = Project.load(args.path, checkers=[check_generation])
    except ProjectLoadError as exc:
        print(f"puffin: {exc}", file=sys.stderr)
        return 2
    result: int = args.func(project, args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
