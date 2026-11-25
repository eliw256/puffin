"""Firmware generators, keyed by `boards.<name>.firmware.framework`.

`check_generation` reports everything that would stop generation and plugs into
`Project` as a checker, so `puffin check` is the whole truth. `generate` is pure: it
reads the model and part library and returns file bytes plus issues. Writing files is
the caller's job (see `puffin.generators.writer`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from puffin.core.issues import Issue, Severity, sort_issues
from puffin.core.models import ProjectModel
from puffin.core.validate import validate
from puffin.generators.espidf import EspIdfGenerator
from puffin.generators.types import (
    GENERATION_CODES,
    MARKER,
    MARKER_PREFIX,
    BoardJob,
    GeneratedFile,
    GenerationResult,
    Generator,
    gen_issue,
)
from puffin.library import PartLibrary, PartLoadError

GENERATORS: dict[str, Generator] = {g.framework: g for g in (EspIdfGenerator(),)}


def normalize_output(path: str) -> str | None:
    """Return `path` as a clean relative POSIX path, or None if it leaves the project."""
    if not path or "\\" in path or PureWindowsPath(path).drive:
        return None
    pure = PurePosixPath(path)
    if pure.is_absolute():
        return None
    parts: list[str] = []
    for part in pure.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts) or None


@dataclass(frozen=True)
class _Planned:
    path: str
    generator: Generator
    job: BoardJob


def _plan(model: ProjectModel, library: PartLibrary) -> tuple[dict[str, _Planned], list[Issue]]:
    """Resolve each board with firmware to a generator job, reporting what blocks it.

    Tolerates models with validation errors: boards whose MCU or part does not resolve are
    skipped, since validation already reports them.
    """
    planned: dict[str, _Planned] = {}
    issues: list[Issue] = []
    owners: dict[str, str] = {}
    for name, board in model.boards.items():
        firmware = board.firmware
        if firmware is None:
            continue
        where = f"boards.{name}.firmware"
        generator = GENERATORS.get(firmware.framework)
        if generator is None:
            supported = ", ".join(sorted(GENERATORS))
            issues.append(
                gen_issue(
                    "unsupported-framework",
                    f"{where}.framework",
                    f"no generator for framework '{firmware.framework}'",
                    f"supported frameworks: {supported}",
                )
            )
            continue
        path = normalize_output(firmware.output)
        if path is None:
            issues.append(
                gen_issue(
                    "output-path-escape",
                    f"{where}.output",
                    f"'{firmware.output}' is not a path inside the project",
                    "use a relative path below the directory of puffin.yaml",
                )
            )
            continue
        if path in owners:
            issues.append(
                gen_issue(
                    "output-path-collision",
                    f"{where}.output",
                    f"'{path}' is also the output of board '{owners[path]}'",
                    "give each board its own output path",
                )
            )
            continue
        owners[path] = name
        component = model.components.get(firmware.mcu)
        try:
            part = library.get(component.part) if component else None
        except PartLoadError:
            part = None
        if part is None:
            continue
        job = BoardJob(model, library, name, firmware, firmware.mcu, part)
        issues.extend(generator.preflight(job))
        planned[name] = _Planned(path, generator, job)
    return planned, issues


def check_generation(model: ProjectModel, library: PartLibrary) -> list[Issue]:
    """Everything that would stop generation, as a Project checker.

    Wire it in with `Project.load(path, checkers=[check_generation])` so `check()` and the
    edit API reject what `puffin gen` would reject. Pure.
    """
    return _plan(model, library)[1]


def generate(
    model: ProjectModel, library: PartLibrary, board: str | None = None
) -> GenerationResult:
    """Render firmware files for every board with firmware, or only `board`. Pure."""
    planned, generation_issues = _plan(model, library)
    issues = sort_issues([*validate(model, library), *generation_issues])
    if any(issue.severity is Severity.ERROR for issue in issues):
        return GenerationResult([], issues)
    if board is not None:
        if board not in model.boards:
            known = ", ".join(model.boards) or "none"
            issue = gen_issue(
                "unknown-board", board, f"board '{board}' is not defined", f"use one of: {known}"
            )
            return GenerationResult([], [*issues, issue])
        if board not in planned:
            issue = gen_issue(
                "no-firmware",
                f"boards.{board}.firmware",
                f"board '{board}' has no firmware",
                "add a firmware section to the board",
            )
            return GenerationResult([], [*issues, issue])
    files = [
        GeneratedFile(name, item.path, item.generator.render(item.job))
        for name, item in planned.items()
        if board is None or name == board
    ]
    return GenerationResult(files, issues)


__all__ = [
    "GENERATION_CODES",
    "GENERATORS",
    "MARKER",
    "MARKER_PREFIX",
    "BoardJob",
    "GeneratedFile",
    "GenerationResult",
    "Generator",
    "check_generation",
    "generate",
    "normalize_output",
]
