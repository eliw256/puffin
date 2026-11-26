"""Service methods the UI uses (M4): families, symbol and block views, targets, undo."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from puffin.core import OPERATIONS, Op, Project
from puffin.core.project import _apply, _parse_model
from puffin.mcp.service import PuffinService, ToolError


def model_of(path: Path) -> dict[str, Any]:
    return Project.load(path).model.model_dump()


def test_query_pins_reports_each_pins_family(example_path: Path) -> None:
    pins = {p["pin"]: p for p in PuffinService(example_path).query_pins("U1")["pins"]}
    assert pins["GPIO10"]["family"] == "spi"
    assert pins["GPIO17"]["family"] == "uart"
    assert pins["GPIO0"]["family"] == "gpio"
    assert pins["3V3"]["family"] == "power"
    assert pins["GND"]["family"] == "ground"
    assert pins["EN"]["family"] == "unused"  # on a net, but no assignment


def test_symbol_view_uses_the_schematic_symbol(example_path: Path) -> None:
    view = PuffinService(example_path).symbol_view("U1")
    assert view["source"] == "schematic"
    assert view["pin_by_number"]["18"] == "GPIO10"
    imu = PuffinService(example_path).symbol_view("U3")
    assert imu["source"] == "schematic"
    assert set(imu["pin_by_number"].values()) == {"SCK", "SDI", "SDO", "CS", "VDD", "GND"}


def test_symbol_view_falls_back_to_a_generated_box(example_path: Path) -> None:
    service = PuffinService(example_path)
    proposal = service.propose_edit(
        [{"op": "add_component", "ref": "U4", "part": "example/spi-imu", "board": "main"}]
    )
    service.apply_edit(proposal["proposal_id"])
    view = service.symbol_view("U4")
    assert view["source"] == "generated"
    pins = view["symbol"]["pins"]
    assert [p["outward"] for p in pins] == [180, 180, 180, 0, 0, 0]
    assert set(view["pin_by_number"].values()) == {"SCK", "SDI", "SDO", "CS", "VDD", "GND"}


def test_block_view_groups_buses_by_peripheral(example_path: Path) -> None:
    view = PuffinService(example_path).block_view()
    bus = {(e["source"], e["target"]): e for e in view["edges"] if e["kind"] == "bus"}
    spi = bus[("U1", "U3")]
    assert spi["label"] == "SPI2 ×4" and len(spi["nets"]) == 4  # noqa: RUF001
    assert not spi["cross_board"]
    link = bus[("U1", "U2")]
    assert link["label"] == "UART1 ×2" and link["cross_board"]  # noqa: RUF001
    assert {e["label"] for e in view["edges"] if e["kind"] == "rail"} >= {"+3V3", "GND"}
    assert {b["id"]: sorted(b["components"]) for b in view["boards"]} == {
        "main": ["U1", "U3"],
        "motor": ["U2"],
    }


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("U1.GPIO0", {"kind": "pin", "component": "U1", "pin": "GPIO0"}),
        ("nets.IMU_SCK", {"kind": "net", "net": "IMU_SCK"}),
        ("assignments.U1.spi2.cs.imu", {"kind": "pin", "component": "U1", "pin": "GPIO10"}),
        ("components.U3", {"kind": "component", "component": "U3"}),
        ("boards.main.firmware.mcu", {"kind": "board", "board": "main"}),
        ("constraints[0]", {"kind": "net", "net": "IMU_SCK"}),
        ("overrides[0]", {"kind": "pin", "component": "U1", "pin": "GPIO0"}),
        ("kicad_schematic hw/robot/robot.kicad_sch", {"kind": "schematic",
                                                      "schematic": "hw/robot/robot.kicad_sch"}),
        (".puffin/backup", {"kind": "writeback"}),
        ("something else", {"kind": "none"}),
    ],
)  # fmt: skip
def test_issue_targets(example_path: Path, where: str, expected: dict[str, str]) -> None:
    from puffin.mcp.targets import issue_target

    assert issue_target(Project.load(example_path).model, where) == expected


def test_diagnostics_adds_a_target_per_issue(example_path: Path) -> None:
    example_path.write_text(example_path.read_text().replace("cs.imu: GPIO10", "cs.imu: GPIO9"))
    result = PuffinService(example_path).diagnostics()
    assert len(result["targets"]) == len(result["issues"])
    assert {"kind": "pin", "component": "U1", "pin": "GPIO9"} in result["targets"]


# --- undo: every operation's inverse restores the model ---------------------------------------

ROUND_TRIPS: dict[str, list[dict[str, Any]]] = {
    "add_component": [{"op": "add_component", "ref": "U4", "part": "example/spi-imu",
                       "board": "main"}],
    "remove_component": [{"op": "add_component", "ref": "U4", "part": "example/spi-imu",
                          "board": "main", "variant": None},
                         {"op": "remove_component", "ref": "U4"}],
    "set_component": [{"op": "set_component", "ref": "U2", "part": "espressif/esp32-s3-wroom-1",
                       "board": "motor", "variant": "N16R8"}],
    "connect": [{"op": "connect", "net": "BOOT_MAIN", "endpoints": ["U1.GPIO4"]}],
    "disconnect": [{"op": "disconnect", "net": "+3V3", "endpoints": ["U3.VDD"]}],
    "set_net": [{"op": "set_net", "name": "+3V3", "endpoints": ["U1.3V3", "U2.3V3"]}],
    "remove_net": [{"op": "remove_net", "name": "AUTO(U2.EN)"}],
    "rename_net": [{"op": "rename_net", "old": "IMU_SCK", "new": "SPI_CLK"}],
    "assign_pin": [{"op": "connect", "net": "X", "endpoints": ["U1.GPIO4"]},
                   {"op": "assign_pin", "component": "U1", "peripheral": "gpio",
                    "signal": "io.led", "pin": "GPIO4"}],
    "unassign_pin": [{"op": "unassign_pin", "component": "U2", "peripheral": "uart1",
                      "signal": "rx"}],
    "set_assignment_pin": [{"op": "set_assignment_pin", "component": "U1", "peripheral": "spi2",
                            "signal": "cs.imu", "pin": "GPIO4"},
                           {"op": "move_pin", "component": "U1", "from_pin": "GPIO10",
                            "to_pin": "GPIO4"}],
    "move_pin": [{"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO4"}],
    "add_override": [{"op": "add_override", "pin": "U1.GPIO3", "allow": "strapping",
                      "note": "test"}],
    "remove_override": [{"op": "remove_override", "pin": "U1.GPIO0", "allow": "strapping"}],
}  # fmt: skip


def test_every_operation_has_a_round_trip_case() -> None:
    assert set(ROUND_TRIPS) == set(OPERATIONS)


@pytest.mark.parametrize("name", sorted(ROUND_TRIPS))
def test_inverse_restores_the_model(name: str, example_path: Path) -> None:
    """Apply the ops, then their recorded inverse, to a copy of the document: same model.
    (Raw application: some single steps are invalid on their own, which is fine for undo.)"""
    project = Project.load(example_path)
    ops = ROUND_TRIPS[name]
    inverse = project.inverse(ops)
    assert inverse is not None
    document = copy.deepcopy(project._document)
    for op in [*ops, *inverse]:
        _apply(document, Op.from_dict(op))
    assert _parse_model(document).model_dump() == project.model.model_dump()


def test_inverse_of_ops_that_do_not_apply_is_none(example_path: Path) -> None:
    assert Project.load(example_path).inverse([{"op": "remove_net", "name": "NOPE"}]) is None


def test_history_and_undo_through_the_normal_pipeline(example_path: Path) -> None:
    service = PuffinService(example_path)
    with pytest.raises(ToolError, match="nothing to undo"):
        service.propose_undo()
    before = model_of(example_path)
    move = [{"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO4"}]
    service.apply_edit(service.propose_edit(move)["proposal_id"])
    [entry] = service.history()["entries"]
    assert entry["can_undo"] and entry["summary"] == ["move_pin(U1.GPIO10->GPIO4)"]
    undo = service.propose_undo()
    assert undo["ok"] and undo["undo_of"]["summary"] == ["move_pin(U1.GPIO10->GPIO4)"]
    assert not undo["changed_since"]
    assert "-  IMU_CS: [U1.GPIO4, U3.CS]" in undo["diff"]
    service.apply_edit(undo["proposal_id"])
    assert model_of(example_path) == before
    assert len(service.history()["entries"]) == 2


def test_undo_of_entries_without_inverse_is_refused(example_path: Path) -> None:
    service = PuffinService(example_path)
    history = example_path.parent / ".puffin" / "history.jsonl"
    history.parent.mkdir(exist_ok=True)
    history.write_text('{"proposal_id": "p_old", "summary": ["old"], "sha256": "x"}\n')
    with pytest.raises(ToolError, match="recorded before undo support"):
        service.propose_undo()
    assert service.history()["entries"][0]["can_undo"] is False


def test_fingerprint_and_writeback_status(example_path: Path) -> None:
    service = PuffinService(example_path)
    fingerprint = service.fingerprint()
    assert set(fingerprint["schematics"]) == {"hw/robot/robot.kicad_sch"}
    proposal = service.propose_edit(
        [{"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO4"}]
    )
    assert proposal["base_sha256"] == fingerprint["project"]
    assert service.writeback_status() == {"ok": True, "pending": 0, "files": []}
    assert service.writeback_confirm()["confirmed"] == 0
    assert service.writeback_restore()["restored"] == 0
