"""Tests never depend on examples/: the example is a live project the engineer keeps editing.

Everything reads the frozen copy in tests/fixtures/dual-esp32s3-robot/. The single exception is
the smoke test below, which only asks whether the example still passes `puffin check`.
"""

from __future__ import annotations

import re
from pathlib import Path

from puffin.cli.main import main

TESTS = Path(__file__).parent
REPO = TESTS.parent
SMOKE = Path(__file__).name

# A path segment "examples" in a string or a Path expression (`"examples"`, `/ "examples" /`,
# `examples/...`), not the English word in prose such as "for example".
_EXAMPLES_PATH = re.compile(r"""["'/]examples["'/]""")


def test_no_test_reads_from_examples() -> None:
    offenders = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == SMOKE:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _EXAMPLES_PATH.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert not offenders, "tests must use tests/fixtures, not examples/:\n" + "\n".join(offenders)


def test_guard_pattern_catches_example_paths() -> None:
    assert _EXAMPLES_PATH.search('Path(__file__).parents[1] / "examples" / "x"')
    assert _EXAMPLES_PATH.search('main(["check", "examples/dual-esp32s3-robot"])')
    assert not _EXAMPLES_PATH.search("# for example, an IMU on SPI")


def test_example_project_passes_check() -> None:
    """Smoke test: the live example stays a valid project, whatever its current pins."""
    assert main(["check", str(REPO / "examples" / "dual-esp32s3-robot")]) == 0
