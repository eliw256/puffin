"""Generator acceptance tests. Golden files are hand-written and approved; never regenerate them."""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from puffin.core import Project
from puffin.generators import GENERATION_CODES, GenerationResult, generate
from puffin.generators.espidf import _arrow, define_name
from puffin.library import PartLibrary
from puffin.library.loader import bundled_parts_dir
from tests.conftest import EXAMPLE

GOLDEN = Path(__file__).parent / "golden" / "dual-esp32s3-robot"
FIXTURE_LIBRARY = PartLibrary([Path(__file__).parent / "fixtures" / "parts", bundled_parts_dir()])


def golden(board: str) -> bytes:
    return (GOLDEN / board / "puffin_pins.h").read_bytes()


def run(project: Project, board: str | None = None) -> GenerationResult:
    return generate(project.model, project.library, board)


def by_board(result: GenerationResult) -> dict[str, bytes]:
    return {file.board: file.content for file in result.files}


def edited(path: Path, *replacements: tuple[str, str], append: str = "") -> Project:
    text = path.read_text()
    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new, 1)
    path.write_text(text + append)
    return Project.load(path)


@pytest.mark.parametrize("board", ["main", "motor"])
def test_output_matches_golden(board: str) -> None:
    result = run(Project.load(EXAMPLE))
    assert result.issues == []
    assert by_board(result)[board] == golden(board)


def test_output_paths_and_format() -> None:
    files = run(Project.load(EXAMPLE)).files
    assert [(f.board, f.path) for f in files] == [
        ("main", "fw/main/main/include/puffin_pins.h"),
        ("motor", "fw/motor/main/include/puffin_pins.h"),
    ]
    for file in files:
        assert file.content.isascii()
        assert b"\r" not in file.content
        assert file.content.endswith(b"\n") and not file.content.endswith(b"\n\n")


_SCRIPT = """
import sys
from puffin.core import Project
from puffin.generators import generate
project = Project.load(sys.argv[1])
for file in generate(project.model, project.library).files:
    sys.stdout.buffer.write(file.path.encode() + b"\\0" + file.content + b"\\0")
"""


def test_deterministic_across_fresh_processes() -> None:
    outputs = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(EXAMPLE)], capture_output=True, env=env, check=True
        )
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1]
    assert golden("main") in outputs[0]


def test_moving_one_pin_changes_exactly_one_line(project: Project) -> None:
    assert project.move_pin("U1", "GPIO12", "GPIO14").ok
    files = by_board(run(project))
    assert files["motor"] == golden("motor")
    diff = list(
        difflib.ndiff(golden("main").decode().splitlines(), files["main"].decode().splitlines())
    )
    removed = [line[2:] for line in diff if line.startswith("- ")]
    added = [line[2:] for line in diff if line.startswith("+ ")]
    assert removed == ["#define PUFFIN_SPI2_SCK GPIO_NUM_12 /* net IMU_SCK */"]
    assert added == ["#define PUFFIN_SPI2_SCK GPIO_NUM_14 /* net IMU_SCK */"]


@pytest.mark.parametrize(
    ("peripheral", "signal", "expected"),
    [
        ("spi2", "sck", "PUFFIN_SPI2_SCK"),
        ("spi2", "cs.imu", "PUFFIN_SPI2_CS_IMU"),
        ("gpio", "io.boot_button", "PUFFIN_GPIO_BOOT_BUTTON"),
        ("gpio", "io", "PUFFIN_GPIO_IO"),
        ("uart1", "tx", "PUFFIN_UART1_TX"),
        ("gpio", "io.led-2", "PUFFIN_GPIO_LED_2"),
    ],
)
def test_define_names(peripheral: str, signal: str, expected: str) -> None:
    assert define_name(peripheral, signal) == expected


@pytest.mark.parametrize(
    ("local", "remote", "arrow"),
    [
        ("out", "in", "->"),
        ("in", "out", "<-"),
        ("out", "out", "<->"),
        ("in", "in", "<->"),
        ("bidir", "in", "->"),
        ("out", "bidir", "<->"),
        (None, "in", "<->"),
        ("out", None, "<->"),
        (None, None, "<->"),
    ],
)
def test_arrow_rule(local: str | None, remote: str | None, arrow: str) -> None:
    assert _arrow(local, remote) == arrow  # type: ignore[arg-type]


def link_line(project: Project) -> str:
    text = by_board(run(project))["main"].decode()
    return next(line for line in text.splitlines() if line.startswith(" * LINK_MAIN_TX:"))


def test_unknown_direction_list_form_falls_back(example_path: Path) -> None:
    project = edited(example_path, ("  U2:\n    uart1:", "  U2:\n    uart2:"))
    assert (
        link_line(project) == " * LINK_MAIN_TX: U1.GPIO17 (uart1.tx) <-> motor U2.GPIO18 (uart2.rx)"
    )


def test_unassigned_remote_pin_falls_back(example_path: Path) -> None:
    project = edited(example_path, ("  U2:\n    uart1:\n      tx: GPIO17\n      rx: GPIO18\n", ""))
    assert link_line(project) == " * LINK_MAIN_TX: U1.GPIO17 (uart1.tx) <-> motor U2.GPIO18"


def test_two_drivers_fall_back(example_path: Path) -> None:
    project = edited(
        example_path,
        (
            "      tx: GPIO17\n      rx: GPIO18\n\nconstraints",
            "      rx: GPIO17\n      tx: GPIO18\n\nconstraints",
        ),
    )
    assert (
        link_line(project) == " * LINK_MAIN_TX: U1.GPIO17 (uart1.tx) <-> motor U2.GPIO18 (uart1.tx)"
    )


def test_power_nets_are_not_links() -> None:
    text = by_board(run(Project.load(EXAMPLE)))["main"].decode()
    assert "3V3" not in text and "GND" not in text


def test_comment_terminator_in_note_is_escaped(project: Project) -> None:
    assert project.add_override("U1.GPIO10", "strapping", "ends */ early").ok
    line = next(
        line
        for line in by_board(run(project))["main"].decode().splitlines()
        if "PUFFIN_SPI2_CS_IMU" in line
    )
    assert line.endswith("/* net IMU_CS; override strapping: ends * / early */")
    assert line.count("*/") == 1


def test_validation_errors_block_generation(example_path: Path) -> None:
    project = edited(example_path, ("cs.imu: GPIO10", "cs.imu: GPIO9"))
    result = run(project)
    assert result.files == [] and not result.ok
    assert "assignment-unconnected" in {issue.code for issue in result.issues}


def test_warnings_do_not_block(project: Project) -> None:
    assert project.add_override("U1.GPIO4", "strapping", "spare").ok
    result = run(project)
    assert result.ok and len(result.files) == 2
    assert [issue.code for issue in result.issues] == ["unused-override"]


def _collision(path: Path) -> GenerationResult:
    project = Project.load(path)
    for net, pin, signal in (("A", "GPIO4", "io.a_b"), ("B", "GPIO5", "io.a.b")):
        assert project.connect(net, f"U1.{pin}").ok
        assert project.assign_pin("U1", "gpio", signal, pin).ok
    return run(project)


def _non_ascii(path: Path) -> GenerationResult:
    project = Project.load(path)
    assert project.add_override("U1.GPIO10", "strapping", "caf\u00e9").ok
    return run(project)


def _unsupported_pin(path: Path) -> GenerationResult:
    path.write_text(
        "puffin: '0.1'\nproject: x\nboards:\n  a:\n    firmware:\n      framework: esp-idf\n"
        "      target: x\n      mcu: U1\n      output: out.h\ncomponents:\n  U1:\n"
        "    part: local/pa-mcu\n    board: a\nnets:\n  LED: [U1.PA0]\n"
        "assignments:\n  U1:\n    gpio:\n      io.led: PA0\n"
    )
    return run(Project.load(path, FIXTURE_LIBRARY))


def _gen(
    *replacements: tuple[str, str], board: str | None = None, append: str = ""
) -> Callable[[Path], GenerationResult]:
    return lambda path: run(edited(path, *replacements, append=append), board)


# Each generation code: a scenario that must produce it, with no files.
CODE_CASES: dict[str, Callable[[Path], GenerationResult]] = {
    "unsupported-framework": _gen(("framework: esp-idf", "framework: zephyr")),
    "output-path-escape": _gen(("output: fw/main/main", "output: ../escape")),
    "output-path-collision": _gen(("output: fw/motor/main", "output: fw/main/main")),
    "unknown-board": _gen(board="nope"),
    "no-firmware": _gen(("boards:\n", "boards:\n  spare: {}\n"), board="spare"),
    "name-collision": _collision,
    "non-ascii": _non_ascii,
    "unsupported-pin": _unsupported_pin,
}


def test_every_generation_code_has_a_case() -> None:
    assert set(CODE_CASES) == set(GENERATION_CODES)


@pytest.mark.parametrize("code", sorted(CODE_CASES))
def test_generation_code(code: str, example_path: Path) -> None:
    result = CODE_CASES[code](example_path)
    assert code in {issue.code for issue in result.issues}
    assert result.files == [] and not result.ok


@pytest.mark.parametrize("path", ["/abs/pins.h", "../x.h", "a/../../x.h", "C:/x.h", "a\\b.h", ""])
def test_escaping_output_paths_rejected(path: str, example_path: Path) -> None:
    result = run(
        edited(example_path, ("output: fw/main/main/include/puffin_pins.h", f"output: '{path}'"))
    )
    assert "output-path-escape" in {issue.code for issue in result.issues}


def test_output_path_is_normalized(example_path: Path) -> None:
    project = edited(
        example_path, ("output: fw/main/main/include", "output: ./fw/x/../main/main/include")
    )
    assert run(project).files[0].path == "fw/main/main/include/puffin_pins.h"
