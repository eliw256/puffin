"""Firmware generators, keyed by `boards.<name>.firmware.framework`.

`generate` is pure: it reads the model and part library and returns file bytes plus
issues. Writing files is the caller's job (see `puffin.generators.writer`).
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from puffin.core.issues import Issue, Severity
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
from puffin.library import PartLibrary

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


def generate(
    model: ProjectModel, library: PartLibrary, board: str | None = None
) -> GenerationResult:
    """Render firmware files for every board with firmware, or only `board`."""
    issues: list[Issue] = list(validate(model, library))
    if any(issue.severity is Severity.ERROR for issue in issues):
        return GenerationResult([], issues)
    if board is not None and board not in model.boards:
        known = ", ".join(model.boards) or "none"
        issues.append(
            gen_issue(
                "unknown-board", board, f"board '{board}' is not defined", f"use one of: {known}"
            )
        )
        return GenerationResult([], issues)

    files: list[GeneratedFile] = []
    owners: dict[str, str] = {}
    for name in [board] if board is not None else list(model.boards):
        where = f"boards.{name}.firmware"
        firmware = model.boards[name].firmware
        if firmware is None:
            if board is not None:
                issues.append(
                    gen_issue(
                        "no-firmware",
                        where,
                        f"board '{name}' has no firmware",
                        "add a firmware section to the board",
                    )
                )
            continue
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
        part = library.get(model.components[firmware.mcu].part)
        assert part is not None  # guaranteed by validation
        job = BoardJob(model, library, name, firmware, firmware.mcu, part)
        content, board_issues = generator.render(job)
        issues.extend(board_issues)
        files.append(GeneratedFile(name, path, content))

    result = GenerationResult(files, issues)
    return result if result.ok else GenerationResult([], issues)


__all__ = [
    "GENERATION_CODES",
    "GENERATORS",
    "MARKER",
    "MARKER_PREFIX",
    "BoardJob",
    "GeneratedFile",
    "GenerationResult",
    "Generator",
    "generate",
    "normalize_output",
]
