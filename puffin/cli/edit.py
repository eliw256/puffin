"""`puffin move-pin` and `puffin writeback confirm|restore` (docs/WRITEBACK.md)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from puffin.core import Project
from puffin.kicad import checkpoint
from puffin.mcp.service import PuffinService, ToolError


def _print_issues(title: str, issues: list[dict[str, str]]) -> None:
    if issues:
        print(title)
        for issue in issues:
            print(f"  {issue['severity']}[{issue['code']}] {issue['where']}: {issue['message']}")
            if issue.get("hint"):
                print(f"    fix: {issue['hint']}")


def cmd_move_pin(
    project: Project, args: argparse.Namespace, prompt: Callable[[str], str] | None = None
) -> int:
    service = PuffinService(project.path)
    op = {"op": "move_pin", "component": args.component, "from_pin": args.from_pin,
          "to_pin": args.to_pin}  # fmt: skip
    try:
        proposal = service.propose_edit([op], writeback=args.writeback)
    except ToolError as exc:
        print(f"puffin move-pin: {exc.issue}", file=sys.stderr)
        return 2
    payload: dict[str, Any] = {"proposal": proposal, "applied": False}

    def done(code: int, message: str = "") -> int:
        if args.json:
            print(json.dumps(payload, indent=2))
        elif message:
            print(message)
        return code

    if not args.json:
        print(proposal["diff"], end="")
        for plan in proposal.get("writeback", []):
            print(plan["diff"], end="")
        for generated in proposal["generated"]:
            print(generated["diff"], end="")
        _print_issues("errors:", proposal["introduced_errors"] + proposal.get("errors", []))
        _print_issues("warnings:", proposal["warnings"])
    if not proposal["ok"]:
        return done(1, "puffin move-pin: nothing applied")
    if args.dry_run:
        return done(0, "puffin move-pin: dry run, nothing applied")
    if not args.yes:
        if not sys.stdin.isatty():
            print("puffin move-pin: refusing to apply without --yes (no terminal)", file=sys.stderr)
            return done(1)
        answer = (prompt or input)("apply this change? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return done(1, "puffin move-pin: cancelled, nothing applied")
    try:
        result = service.apply_edit(proposal["proposal_id"])
    except ToolError as exc:
        print(f"puffin move-pin: {exc.issue}", file=sys.stderr)
        return done(1)
    payload["applied"] = True
    payload["result"] = result
    changed = ", ".join(result["changed_artifacts"])
    message = f"puffin move-pin: applied to {changed}"
    if proposal.get("writeback"):
        message += "\nreview the schematic in KiCad, then run `puffin writeback confirm`"
    return done(0, message)


def cmd_writeback(project: Project, args: argparse.Namespace) -> int:
    root = project.path.parent
    if args.action == "confirm":
        count = checkpoint.confirm(root)
        print(f"puffin writeback: confirmed {count} write-back(s)" if count
              else "puffin writeback: nothing to confirm")  # fmt: skip
        return 0
    count = checkpoint.restore(root)
    print(f"puffin writeback: restored the checkpoint, undoing {count} write-back(s)" if count
          else "puffin writeback: nothing to restore")  # fmt: skip
    return 0


def add_parsers(commands: Any) -> None:
    move = commands.add_parser(
        "move-pin", help="move a pin's net connection and assignments to another pin"
    )
    move.add_argument("component", help="reference designator, e.g. U1")
    move.add_argument("from_pin", metavar="FROM", help="current pin, e.g. GPIO10")
    move.add_argument("to_pin", metavar="TO", help="new pin, e.g. GPIO4")
    move.add_argument("--path", default=".", help="puffin.yaml or its directory")
    move.add_argument("--writeback", action="store_true",
                      help="also move the pin in the KiCad schematic (verified)")  # fmt: skip
    move.add_argument("--dry-run", action="store_true", help="show the proposal only")
    move.add_argument("--yes", action="store_true", help="apply without asking")
    move.add_argument("--json", action="store_true", help="machine-readable output")
    move.set_defaults(func=cmd_move_pin)

    writeback = commands.add_parser("writeback", help="confirm or undo schematic write-backs")
    writeback.add_argument("action", choices=["confirm", "restore"])
    writeback.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    writeback.set_defaults(func=cmd_writeback)
