"""`puffin import kicad` end to end, and drift in `puffin check` (M2 Step 5 + acceptance)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from puffin.cli.main import main
from puffin.core import Project
from puffin.generators import generate
from puffin.kicad.export import find_kicad_cli
from puffin.kicad.state import STATE_PATH, schematic_hash
from tests.test_kicad_changes import SCK_U1, SCK_U3, edited, node, resistor

SCHEMATIC = "hw/robot/robot.kicad_sch"


@pytest.fixture(autouse=True)
def no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)


def write_netlist(example_path: Path, text: str) -> str:
    path = example_path.parent / "variant.net"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run(example_path: Path, text: str, *flags: str) -> int:
    return main(["import", "kicad", str(example_path), "--netlist",
                 write_netlist(example_path, text), *flags])  # fmt: skip


def header(example_path: Path, board: str = "main") -> list[str]:
    project = Project.load(example_path)
    result = generate(project.model, project.library, board)
    assert result.ok, result.issues
    return result.files[0].content.decode().splitlines()


# --- acceptance ---------------------------------------------------------------------------


def test_moved_sck_end_to_end(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = header(example_path)
    text = edited((SCK_U1, node("U1", "22", "IO14")))

    assert run(example_path, text, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "3. endpoint changes\n  IMU_SCK: -U1.GPIO12 +U1.GPIO14" in out
    assert "4. proposed assignment fixes\n  U1 spi2.sck: GPIO12 -> GPIO14 (net IMU_SCK)" in out
    assert "GPIO14" not in example_path.read_text()

    assert run(example_path, text, "--yes") == 0
    assert "applied 2 operation(s)" in capsys.readouterr().out
    assert "  IMU_SCK: [U1.GPIO14, U3.SCK]\n" in example_path.read_text()

    assert main(["check", str(example_path)]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out

    after = header(example_path)
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert len(changed) == 1
    assert changed[0][0].startswith("#define PUFFIN_SPI2_SCK")
    assert changed[0][1].startswith("#define PUFFIN_SPI2_SCK")
    assert "GPIO_NUM_14" in changed[0][1]


def test_rename_preserves_constraint(example_path: Path) -> None:
    text = edited(('(name "/IMU_SCK")', '(name "/SPI_CLK")'))
    assert run(example_path, text, "--yes") == 0
    model = Project.load(example_path).model
    assert [c.net for c in model.constraints] == ["SPI_CLK"]
    assert model.constraints[0].max_freq_hz == 10_000_000


def test_unmanaged_resistor_never_enters_the_graph(example_path: Path) -> None:
    text = edited((SCK_U1, node("U1", "22", "IO14")))
    assert run(example_path, text, "--yes") == 0
    yaml_text = example_path.read_text()
    for ref in ("R1", "R2", "R3", "C1", "SW1"):
        assert f"{ref}." not in yaml_text


def test_unmappable_pin_is_an_error(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = example_path.read_bytes()
    assert run(example_path, edited((SCK_U1, node("U1", "99", "IO99"))), "--yes") == 1
    out = capsys.readouterr().out
    assert "7. errors the change would introduce" in out
    assert "error[import-unmapped-pin] U1.99" in out
    assert example_path.read_bytes() == before


def test_ambiguous_fix_is_reported_not_guessed(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = example_path.read_bytes()
    text = edited((SCK_U1, node("U1", "22", "IO14") + " " + node("U1", "23", "IO21")))
    assert run(example_path, text, "--yes") == 1
    out = capsys.readouterr().out
    assert "4. proposed assignment fixes" not in out
    assert "5. ambiguous, left alone\n  U1 spi2.sck on GPIO12:" in out
    assert "error[assignment-unconnected]" in out
    assert example_path.read_bytes() == before


def test_series_passive_warning(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = edited(
        ('(comp (ref "SW1")', resistor("R5")),
        (SCK_U3, node("R5", "1", None, "passive")),
        ('(net (code "7")', '(net (code "99") (name "Net-(R5-Pad2)") (class "Default")\n      '
         + node("R5", "2", None, "passive") + "\n      " + SCK_U3 + ")\n    (net (code \"7\")"),
    )  # fmt: skip
    assert run(example_path, text, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "warning[import-series-passive] R5: IMU_SCK is split by R5 (Device:R)" in out
    assert "Device:R x4" in out


# --- review flow --------------------------------------------------------------------------


def test_refuses_without_yes_and_terminal(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = example_path.read_bytes()
    assert run(example_path, edited((SCK_U1, node("U1", "22", "IO14")))) == 1
    assert "refusing to apply without --yes" in capsys.readouterr().err
    assert example_path.read_bytes() == before


@pytest.mark.parametrize(("answer", "code", "applied"), [("y", 0, True), ("", 1, False)])
def test_prompts_on_a_terminal(
    example_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, code: int, applied: bool
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert run(example_path, edited((SCK_U1, node("U1", "22", "IO14")))) == code
    assert ("GPIO14" in example_path.read_text()) is applied


def test_json_output_is_stable(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = edited((SCK_U1, node("U1", "22", "IO14")))
    assert run(example_path, text, "--dry-run", "--json") == 0
    first = capsys.readouterr().out
    assert run(example_path, text, "--dry-run", "--json") == 0
    assert capsys.readouterr().out == first
    data = json.loads(first)
    assert data["applied"] is False and data["ok"] is True and data["in_sync"] is False
    assert data["schematics"][0]["assignment_fixes"][0]["new_pin"] == "GPIO14"
    assert data["schematics"][0]["ops"][0]["op"] == "set_net"


def test_in_sync_records_state_but_dry_run_never_does(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = example_path.parent / STATE_PATH
    state.unlink()
    fixture = (example_path.parent / "hw" / "robot" / "robot.net").read_text()
    assert run(example_path, fixture, "--dry-run") == 0
    assert not state.exists()
    assert run(example_path, fixture) == 0  # in sync: no --yes needed, nothing to apply
    assert "in sync" in capsys.readouterr().out
    recorded = json.loads(state.read_text())["schematics"][SCHEMATIC]["sha256"]
    assert recorded == schematic_hash(example_path.parent, SCHEMATIC)


def test_netlist_requires_a_single_schematic(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    example_path.write_text(
        example_path.read_text().replace(
            "  motor:\n    kicad_schematic: hw/robot/robot.kicad_sch",
            "  motor:\n    kicad_schematic: hw/motor/motor.kicad_sch",
        )
    )
    assert run(example_path, "(export)") == 2
    assert "exactly one schematic" in capsys.readouterr().err


def test_unreadable_netlist_exits_2(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(example_path, "garbage") == 2
    assert "not a KiCad netlist" in capsys.readouterr().err


def test_missing_kicad_cli_exits_2(
    example_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PUFFIN_KICAD_CLI", str(example_path.parent / "no-such-kicad-cli"))
    assert main(["import", "kicad", str(example_path), "--dry-run"]) == 2
    assert "could not run" in capsys.readouterr().err


@pytest.mark.skipif(find_kicad_cli() is None, reason="kicad-cli not installed")
def test_real_kicad_cli_finds_example_in_sync(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["import", "kicad", str(example_path), "--dry-run"]) == 0
    assert "in sync" in capsys.readouterr().out


# --- drift --------------------------------------------------------------------------------


def schematic(example_path: Path) -> Path:
    return example_path.parent / SCHEMATIC


def check_codes(example_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    main(["check", str(example_path)])
    return capsys.readouterr().out


def test_committed_state_matches_committed_schematic(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "schematic-" not in check_codes(example_path, capsys)


def test_edited_schematic_warns_drift(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = schematic(example_path)
    path.write_bytes(path.read_bytes().replace(b'"U3"', b'"U3"  ', 1))
    out = check_codes(example_path, capsys)
    assert "warning[schematic-drift]" in out and "may have changed" in out
    assert "0 error(s), 1 warning(s)" in out


def test_line_endings_do_not_count_as_drift(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = schematic(example_path)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert "schematic-" not in check_codes(example_path, capsys)


def test_missing_and_never_imported(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (example_path.parent / STATE_PATH).unlink()
    assert "warning[schematic-unsynced]" in check_codes(example_path, capsys)
    schematic(example_path).unlink()
    assert "warning[schematic-missing]" in check_codes(example_path, capsys)


def test_sub_sheets_are_part_of_the_hash(tmp_path: Path) -> None:
    (tmp_path / "root.kicad_sch").write_text(
        '(kicad_sch (sheet (property "Sheetname" "io") (property "Sheetfile" "io.kicad_sch")))'
    )
    (tmp_path / "io.kicad_sch").write_text("(kicad_sch)")
    first = schematic_hash(tmp_path, "root.kicad_sch")
    (tmp_path / "io.kicad_sch").write_text("(kicad_sch (changed))")
    assert schematic_hash(tmp_path, "root.kicad_sch") != first
