"""`puffin gen`: generate firmware files from puffin.yaml."""

from __future__ import annotations

import argparse

from puffin.core import Project, Severity
from puffin.generators import generate
from puffin.generators.writer import PlannedWrite, apply_writes, plan_writes


def _changes(plan: PlannedWrite) -> str:
    added, removed = plan.line_changes()
    return f"(+{added} -{removed} lines)"


def cmd_gen(project: Project, args: argparse.Namespace) -> int:
    result = generate(project.model, project.library, args.board)
    for issue in result.issues:
        print(issue)
    if not result.ok:
        errors = sum(issue.severity is Severity.ERROR for issue in result.issues)
        print(f"puffin gen: nothing generated; fix {errors} error(s) first")
        return 1

    plans = plan_writes(project.path.parent, result.files)
    blocked = [plan for plan in plans if plan.action == "blocked"]
    if blocked:
        for plan in blocked:
            print(f"error: {plan.file.path} {plan.reason}\n  fix: {plan.hint}")
        print("puffin gen: nothing written")
        return 1

    if args.dry_run:
        for plan in plans:
            if plan.action == "unchanged":
                print(f"unchanged {plan.file.path}")
            else:
                print(plan.diff(), end="")
        return 0

    if args.check:
        stale = 0
        for plan in plans:
            if plan.action == "create":
                print(f"missing {plan.file.path}")
            elif plan.action == "update":
                print(f"stale {plan.file.path} {_changes(plan)}")
            else:
                print(f"up to date {plan.file.path}")
            stale += plan.action != "unchanged"
        return 1 if stale else 0

    apply_writes(plans)
    for plan in plans:
        if plan.action == "create":
            print(f"created {plan.file.path}")
        elif plan.action == "update":
            print(f"updated {plan.file.path} {_changes(plan)}")
        else:
            print(f"unchanged {plan.file.path}")
    return 0


def add_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    gen = commands.add_parser("gen", help="generate firmware files")
    gen.add_argument("path", nargs="?", default=".", help="puffin.yaml or its directory")
    gen.add_argument("--board", help="generate only this board")
    mode = gen.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 if any output is stale"
    )
    mode.add_argument("--dry-run", action="store_true", help="print the diff; write nothing")
    gen.set_defaults(func=cmd_gen)
