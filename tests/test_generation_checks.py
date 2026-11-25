"""`puffin check` must be the whole truth: if it reports no errors, generation succeeds."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from puffin.cli.main import main
from puffin.core import Project, Severity
from puffin.generators import GENERATION_CODES, check_generation, generate
from puffin.library import PartLibrary
from puffin.library.loader import bundled_parts_dir

LIBRARY = PartLibrary([Path(__file__).parent / "fixtures" / "parts", bundled_parts_dir()])

# Codes about the --board argument rather than project content; only `generate` reports them.
ARGUMENT_CODES = {"unknown-board", "no-firmware"}


def load(path: Path) -> Project:
    return Project.load(path, LIBRARY, checkers=[check_generation])


def errors(project: Project) -> set[str]:
    return {i.code for i in project.check() if i.severity is Severity.ERROR}


def rewrite(*replacements: tuple[str, str]) -> Callable[[Path], None]:
    def apply(path: Path) -> None:
        text = path.read_text()
        for old, new in replacements:
            assert old in text, old
            text = text.replace(old, new, 1)
        path.write_text(text)

    return apply


def pa_mcu_project(path: Path) -> None:
    path.write_text(
        "puffin: '0.1'\nproject: x\nboards:\n  a:\n    firmware:\n      framework: esp-idf\n"
        "      target: x\n      mcu: U1\n      output: out.h\ncomponents:\n  U1:\n"
        "    part: local/pa-mcu\n    board: a\nnets:\n  LED: [U1.PA0]\n"
        "assignments:\n  U1:\n    gpio:\n      io.led: PA0\n"
    )


CONTENT_CASES: dict[str, Callable[[Path], None]] = {
    "unsupported-framework": rewrite(("framework: esp-idf", "framework: zephyr")),
    "output-path-escape": rewrite(("output: fw/main/main", "output: ../escape")),
    "output-path-collision": rewrite(("output: fw/motor/main", "output: fw/main/main")),
    "name-collision": rewrite(
        ("nets:\n", "nets:\n  A: [U1.GPIO4]\n  B: [U1.GPIO5]\n"),
        (
            "      io.boot_button: GPIO0\n",
            "      io.boot_button: GPIO0\n      io.a_b: GPIO4\n      io.a.b: GPIO5\n",
        ),
    ),
    "non-ascii": rewrite(("note: boot button, intentional", "note: caf\u00e9 button")),
    "unsupported-pin": pa_mcu_project,
}


def test_core_does_not_import_generators() -> None:
    code = (
        "import sys, puffin.core, puffin.library\n"
        "assert not [m for m in sys.modules if m.startswith('puffin.generators')]"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_every_content_code_has_a_case() -> None:
    assert set(CONTENT_CASES) == set(GENERATION_CODES) - ARGUMENT_CODES


@pytest.mark.parametrize("code", sorted(CONTENT_CASES))
def test_check_reports_generation_issue(code: str, example_path: Path) -> None:
    CONTENT_CASES[code](example_path)
    assert code in {i.code for i in load(example_path).check()}


@pytest.mark.parametrize("code", sorted(CONTENT_CASES))
def test_core_alone_does_not_report_generation_issues(code: str, example_path: Path) -> None:
    CONTENT_CASES[code](example_path)
    assert code not in {i.code for i in Project.load(example_path, LIBRARY).check()}


@pytest.mark.parametrize("case", ["example", *sorted(CONTENT_CASES)])
def test_check_clean_if_and_only_if_generation_succeeds(case: str, example_path: Path) -> None:
    if case != "example":
        CONTENT_CASES[case](example_path)
    project = load(example_path)
    assert (not errors(project)) == generate(project.model, project.library).ok


@pytest.mark.parametrize(
    "broken",
    [
        rewrite(("      mcu: U1\n", "      mcu: U9\n")),
        rewrite(("cs.imu: GPIO10", "cs.imu: GPIO99")),
        rewrite(("cs.imu: GPIO10", "cs.imu: GPIO9")),
        rewrite(("part: example/spi-imu", "part: nope/none")),
        rewrite(
            (
                "    part: espressif/esp32-s3-wroom-1\n    board: main",
                "    part: nope/mcu\n    board: main",
            )
        ),
    ],
)
def test_generation_checks_tolerate_invalid_models(
    broken: Callable[[Path], None], example_path: Path
) -> None:
    broken(example_path)
    assert errors(load(example_path))  # validation reports it; preflight must not crash


def test_edit_creating_name_collision_is_rejected(example_path: Path) -> None:
    project = load(example_path)
    assert project.connect("A", "U1.GPIO4").ok
    assert project.assign_pin("U1", "gpio", "io.a_b", "GPIO4").ok
    assert project.connect("B", "U1.GPIO5").ok
    before = project.dumps()
    result = project.assign_pin("U1", "gpio", "io.a.b", "GPIO5")
    assert not result.ok and not result.applied
    assert {issue.code for issue in result.errors} == {"name-collision"}
    assert project.dumps() == before


def test_same_edit_is_accepted_without_generator_checks(example_path: Path) -> None:
    project = Project.load(example_path)
    assert project.connect("A", "U1.GPIO4").ok
    assert project.assign_pin("U1", "gpio", "io.a_b", "GPIO4").ok
    assert project.connect("B", "U1.GPIO5").ok
    assert project.assign_pin("U1", "gpio", "io.a.b", "GPIO5").ok


def test_cli_check_includes_generation_issues(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rewrite(("framework: esp-idf", "framework: zephyr"))(example_path)
    assert main(["check", str(example_path)]) == 1
    output = capsys.readouterr().out
    assert "error[unsupported-framework] boards.main.firmware.framework" in output
