from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffin.cli.main import main


def test_check_clean(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(example_path)]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out


def test_check_accepts_directory(example_path: Path) -> None:
    assert main(["check", str(example_path.parent)]) == 0


def test_check_reports_errors(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    example_path.write_text(example_path.read_text().replace("cs.imu: GPIO10", "cs.imu: GPIO9"))
    assert main(["check", str(example_path)]) == 1
    output = capsys.readouterr().out
    assert "assignment-net-mismatch" in output and "fix:" in output


def test_check_json(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(example_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_unreadable_project_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(tmp_path)]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_show(example_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["show", str(example_path)]) == 0
    output = capsys.readouterr().out
    assert "U1 spi2.cs.imu -> GPIO10" in output and "10,000,000 Hz" in output


def test_show_board_keeps_cross_board_net(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["show", str(example_path), "--board", "motor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["components"]) == {"U2"}
    assert "LINK_MAIN_TX" in data["nets"] and "IMU_SCK" not in data["nets"]


def test_show_unknown_board(example_path: Path) -> None:
    assert main(["show", str(example_path), "--board", "nope"]) == 2
