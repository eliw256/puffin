"""Transactions: `Project.batch` applies operations together and judges the end state once."""

from __future__ import annotations

import json

from puffin.core import OPERATIONS, Op, Project


def sck_mosi_swap() -> list[Op]:
    return [
        Op("set_net", {"name": "IMU_SCK", "endpoints": ["U1.GPIO11", "U3.SCK"]}),
        Op("set_net", {"name": "IMU_MOSI", "endpoints": ["U1.GPIO12", "U3.SDI"]}),
        Op("set_assignment_pin", {"component": "U1", "peripheral": "spi2", "signal": "sck",
                                  "pin": "GPIO11"}),
        Op("set_assignment_pin", {"component": "U1", "peripheral": "spi2", "signal": "mosi",
                                  "pin": "GPIO12"}),
    ]  # fmt: skip


def test_swap_succeeds_as_one_batch(project: Project) -> None:
    result = project.batch(sck_mosi_swap())
    assert result.ok and result.applied, result.errors
    assert result.operation == "batch(4 ops)"
    assert project.model.assignments["U1"]["spi2"]["sck"] == "GPIO11"
    assert project.model.nets["IMU_SCK"] == ["U1.GPIO11", "U3.SCK"]
    assert not [i for i in project.check() if i.severity == "error"]


def test_one_step_of_a_swap_alone_is_rejected(project: Project) -> None:
    """Moving SCK onto MOSI's pin is a double assignment until MOSI moves too."""
    before = project.dumps()
    result = project.batch(sck_mosi_swap()[2:3])
    assert not result.ok and not result.applied
    assert {e.code for e in result.errors} == {"pin-double-assigned"}
    assert project.dumps() == before


def test_dry_run_changes_nothing(project: Project) -> None:
    before = project.dumps()
    result = project.batch(sck_mosi_swap(), dry_run=True)
    assert result.ok and not result.applied
    assert project.dumps() == before and not project.dirty


def test_failing_op_names_its_index_and_leaves_project_unchanged(project: Project) -> None:
    before = project.dumps()
    result = project.batch([*sck_mosi_swap(), Op("remove_net", {"name": "NOPE"})])
    assert not result.ok
    assert [(e.code, e.where) for e in result.errors] == [
        ("edit-rejected", "ops[4] remove_net(NOPE)")
    ]
    assert project.dumps() == before


def test_unknown_operation_and_bad_arguments(project: Project) -> None:
    unknown = project.batch([Op("explode")])
    assert "unknown operation 'explode'" in unknown.errors[0].message
    bad = project.batch([Op("remove_net", {"nme": "IMU_SCK"})])
    assert "bad arguments for remove_net" in bad.errors[0].message


def test_empty_batch_is_a_no_op(project: Project) -> None:
    result = project.batch([])
    assert result.ok and not result.applied and not project.dirty


def test_ops_round_trip_through_json(project: Project) -> None:
    wire = json.loads(json.dumps([op.to_dict() for op in sck_mosi_swap()]))
    assert wire[0] == {"op": "set_net", "name": "IMU_SCK", "endpoints": ["U1.GPIO11", "U3.SCK"]}
    assert project.batch(wire).ok


def test_rename_net_carries_constraint_intent_position_and_comment(project: Project) -> None:
    assert project.batch([Op("set_net", {"name": "LINK", "endpoints": ["U1.GPIO4"]})]).ok
    text = (
        project.dumps()
        .decode()
        .replace("  U3: IMU for balance", "  LINK: spare line\n  U3: IMU for balance")
    )
    edited = Project(project.path, text.encode(), project.library)

    result = edited.batch([
        Op("rename_net", {"old": "IMU_SCK", "new": "SPI_CLK"}),
        Op("rename_net", {"old": "LINK", "new": "SPARE"}),
    ])  # fmt: skip
    assert result.ok, result.errors
    out = edited.dumps().decode()
    assert out.index("SPI_CLK: [U1.GPIO12") < out.index("IMU_MOSI:")
    assert "- net: SPI_CLK\n" in out and "IMU_SCK" not in out
    assert "  SPARE: spare line\n" in out
    assert "# Inter-MCU link, crossed TX/RX.\n  LINK_MAIN_TX" in out


def test_rename_net_rejects_existing_target(project: Project) -> None:
    result = project.batch([Op("rename_net", {"old": "IMU_SCK", "new": "IMU_MOSI"})])
    assert "already exists" in result.errors[0].message


def test_set_net_replaces_in_place_and_rejects_empty(project: Project) -> None:
    assert project.batch([Op("set_net", {"name": "BOOT_MAIN", "endpoints": ["U1.GPIO0"]})]).ok
    assert project.batch([Op("set_net", {"name": "+3V3",
                                         "endpoints": ["U1.3V3", "U2.3V3"]})]).ok  # fmt: skip
    out = project.dumps().decode()
    assert "  +3V3: [U1.3V3, U2.3V3]\n  GND:" in out
    empty = project.batch([Op("set_net", {"name": "GND", "endpoints": []})])
    assert "use remove_net" in empty.errors[0].hint


def test_set_component_updates_in_place(project: Project) -> None:
    op = Op("set_component", {"ref": "U2", "part": "espressif/esp32-s3-wroom-1",
                              "board": "motor", "variant": None})  # fmt: skip
    assert project.batch([op]).ok
    assert project.model.components["U2"].variant is None
    assert "    board: motor\n  U3:\n" in project.dumps().decode()


def test_remove_component_sees_earlier_ops_in_the_batch(project: Project) -> None:
    add = Op("add_component", {"ref": "U4", "part": "example/spi-imu", "board": "main"})
    connect = Op("connect", {"net": "X", "endpoints": ["U4.SCK"]})
    disconnect = Op("disconnect", {"net": "X", "endpoints": ["U4.SCK"]})
    remove = Op("remove_component", {"ref": "U4"})
    assert project.batch([add, connect, disconnect, remove], dry_run=True).ok
    blocked = project.batch([add, connect, remove])
    assert "still referenced" in blocked.errors[0].message
    assert "nets.X (U4.SCK)" in blocked.errors[0].hint


def test_single_edits_are_one_op_batches(project: Project) -> None:
    result = project.connect("A", "U1.GPIO4")
    assert result.operation == "connect(A)"
    assert set(OPERATIONS) >= {
        "add_component", "remove_component", "connect", "disconnect", "assign_pin",
        "unassign_pin", "move_pin", "add_override", "remove_override", "set_net",
        "rename_net", "remove_net", "set_component", "set_assignment_pin",
    }  # fmt: skip
