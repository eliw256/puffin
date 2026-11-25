from __future__ import annotations

import difflib
from pathlib import Path

from puffin.core import Project


def changed_lines(before: bytes, after: bytes) -> tuple[list[str], list[str]]:
    diff = list(difflib.ndiff(before.decode().splitlines(), after.decode().splitlines()))
    return (
        [line[2:] for line in diff if line.startswith("- ")],
        [line[2:] for line in diff if line.startswith("+ ")],
    )


def test_move_pin_updates_net_and_assignment_only(project: Project) -> None:
    before = project.dumps()
    result = project.move_pin("U1", "GPIO10", "GPIO9")
    assert result.ok and result.applied
    assert result.changed_artifacts == ["puffin.yaml"]
    removed, added = changed_lines(before, project.dumps())
    assert removed == ["  IMU_CS: [U1.GPIO10, U3.CS]", "      cs.imu: GPIO10"]
    assert added == ["  IMU_CS: [U1.GPIO9, U3.CS]", "      cs.imu: GPIO9"]


def test_rejected_edit_changes_nothing(project: Project) -> None:
    before = project.dumps()
    result = project.move_pin("U1", "GPIO10", "GPIO45")
    assert not result.ok and not result.applied
    assert {issue.code for issue in result.errors} == {"flagged-pin"}
    assert not project.dirty
    assert project.dumps() == before


def test_move_onto_used_pin_is_rejected(project: Project) -> None:
    result = project.move_pin("U1", "GPIO10", "GPIO12")
    assert not result.ok
    assert "pin-net-conflict" in {issue.code for issue in result.errors}


def test_dry_run_reports_without_applying(project: Project) -> None:
    before = project.dumps()
    result = project.move_pin("U1", "GPIO10", "GPIO9", dry_run=True)
    assert result.ok and not result.applied
    assert project.dumps() == before


def test_add_component_adds_only_its_lines(project: Project) -> None:
    before = project.dumps()
    assert project.add_component("U4", "example/spi-imu", "motor").ok
    removed, added = changed_lines(before, project.dumps())
    assert removed == []
    assert added == ["  U4:", "    part: example/spi-imu", "    board: motor"]
    text = project.dumps().decode()
    assert "    board: motor\n\nnets:" in text


def test_add_component_rejects_unknown_part_and_duplicate(project: Project) -> None:
    assert not project.add_component("U4", "nope/none", "main").ok
    assert not project.add_component("U1", "example/spi-imu", "main").ok


def test_references_lists_every_kind_in_file_order(project: Project) -> None:
    imu = [(r.kind, r.where, r.detail) for r in project.references("U3")]
    assert imu == [
        ("net", "nets.IMU_SCK", "U3.SCK"),
        ("net", "nets.IMU_MOSI", "U3.SDI"),
        ("net", "nets.IMU_MISO", "U3.SDO"),
        ("net", "nets.IMU_CS", "U3.CS"),
        ("net", "nets.3V3", "U3.VDD"),
        ("net", "nets.GND", "U3.GND"),
        ("constraint", "constraints[0]", "IMU_SCK"),
        ("intent", "intent.U3", "IMU for balance control; read at 400 Hz"),
    ]
    kinds = [r.kind for r in project.references("U1")]
    assert kinds.count("assignment") == 7
    assert {"net", "override", "firmware"} <= set(kinds)
    assert ("firmware", "boards.main.firmware.mcu") in {
        (r.kind, r.where) for r in project.references("U1")
    }


def test_references_of_unknown_or_unused_component_is_empty(project: Project) -> None:
    assert project.references("U99") == []
    assert project.add_component("U4", "example/spi-imu", "main").ok
    assert project.references("U4") == []


def test_references_is_read_only(project: Project) -> None:
    before = project.dumps()
    project.references("U1")
    assert project.dumps() == before and not project.dirty


def test_remove_component_in_use_lists_blockers(project: Project) -> None:
    before = project.dumps()
    result = project.remove_component("U3")
    assert not result.ok and not result.applied
    [issue] = result.errors
    assert issue.code == "edit-rejected"
    assert "8 place(s)" in issue.message
    assert "nets.IMU_SCK (U3.SCK)" in issue.hint
    assert "constraints[0] (IMU_SCK)" in issue.hint
    assert "intent.U3" in issue.hint
    assert project.dumps() == before


def test_intent_alone_blocks_removal(example_path: Path) -> None:
    text = example_path.read_text()
    text = text.replace(
        "components:\n", "components:\n  U4:\n    part: example/spi-imu\n    board: main\n"
    )
    example_path.write_text(text + "  U4: spare\n")
    project = Project.load(example_path)
    assert project.check() == []
    result = project.remove_component("U4")
    assert not result.ok
    assert "intent.U4 (spare)" in result.errors[0].hint


def test_removing_last_entries_restores_original_bytes(project: Project) -> None:
    before = project.dumps()
    assert project.connect("LED", "U1.GPIO4").ok
    assert project.assign_pin("U1", "gpio", "io.led", "GPIO4").ok
    assert project.unassign_pin("U1", "gpio", "io.led").ok
    assert project.disconnect("LED", "U1.GPIO4").ok
    assert project.add_override("U2.GPIO46", "strapping", "temporary").ok
    assert project.remove_override("U2.GPIO46", "strapping").ok
    assert project.dumps() == before


def test_unreferenced_component_can_be_removed(project: Project) -> None:
    before = project.dumps()
    assert project.add_component("U4", "example/spi-imu", "main").ok
    assert project.remove_component("U4").ok
    assert project.dumps() == before


def test_connect_new_net_keeps_section_spacing(project: Project) -> None:
    before = project.dumps()
    assert project.connect("LED", "U1.GPIO4").ok
    removed, added = changed_lines(before, project.dumps())
    assert removed == []
    assert added == ["  LED: [U1.GPIO4]"]
    assert "  LED: [U1.GPIO4]\n\nassignments:" in project.dumps().decode()


def test_connect_existing_net_and_disconnect(project: Project) -> None:
    before = project.dumps()
    assert project.connect("IMU_CS", "U2.GPIO4").ok
    assert "IMU_CS: [U1.GPIO10, U3.CS, U2.GPIO4]" in project.dumps().decode()
    assert project.disconnect("IMU_CS", "U2.GPIO4").ok
    assert project.dumps() == before


def test_connect_rejects_bad_input(project: Project) -> None:
    assert not project.connect("IMU_CS", "U1.GPIO10").ok
    assert not project.connect("X", "not-a-pin").ok
    assert not project.connect("X", "U1.GPIO99").ok
    assert not project.connect("X").ok


def test_disconnect_assigned_pin_is_rejected(project: Project) -> None:
    result = project.disconnect("IMU_CS", "U1.GPIO10")
    assert not result.ok
    assert "assignment-unconnected" in {issue.code for issue in result.errors}


def test_assign_and_unassign(project: Project) -> None:
    before = project.dumps()
    assert project.connect("LED", "U1.GPIO4").ok
    connected = project.dumps()
    assert project.assign_pin("U1", "gpio", "io.led", "GPIO4").ok
    removed, added = changed_lines(connected, project.dumps())
    assert (removed, added) == ([], ["      io.led: GPIO4"])
    assert project.unassign_pin("U1", "gpio", "io.led").ok
    assert project.disconnect("LED", "U1.GPIO4").ok
    assert project.dumps() == before


def test_assign_rejects_illegal_function_and_unconnected_pin(project: Project) -> None:
    assert project.connect("X", "U1.GPIO5").ok
    illegal = project.assign_pin("U1", "adc1", "ch3", "GPIO5")
    assert {issue.code for issue in illegal.errors} == {"pin-function"}
    unconnected = project.assign_pin("U1", "gpio", "io.led", "GPIO6")
    assert {issue.code for issue in unconnected.errors} == {"assignment-unconnected"}


def test_override_unblocks_flagged_pin(project: Project) -> None:
    assert not project.connect("STRAP", "U2.GPIO46").ok
    assert project.add_override("U2.GPIO46", "strapping", "pulled low on board").ok
    assert project.connect("STRAP", "U2.GPIO46").ok
    assert not any(i.code == "unused-override" for i in project.check())


def test_override_requires_note(project: Project) -> None:
    assert not project.add_override("U2.GPIO46", "strapping", "  ").ok


def test_add_override_to_empty_list_reloads(tmp_path: Path) -> None:
    path = tmp_path / "puffin.yaml"
    path.write_text(
        "puffin: '0.1'\nproject: x\nboards:\n  a: {}\ncomponents:\n  U1:\n"
        "    part: espressif/esp32-s3-wroom-1\n    board: a\n    variant: N8\n"
        "nets:\n  BOOT: [U1.GPIO0]\n\noverrides: []  # none yet\n\nintent: {}\n"
    )
    project = Project.load(path)
    assert [issue.code for issue in project.check()] == ["flagged-pin"]
    assert project.add_override("U1.GPIO0", "strapping", "boot button").ok
    project.save()
    reloaded = Project.load(path)
    assert reloaded.check() == []
    assert [item.note for item in reloaded.model.overrides] == ["boot button"]
    assert "# none yet" in path.read_text()


def test_two_mcus_may_share_a_net_but_one_mcu_may_not_short_signals(project: Project) -> None:
    assert project.check() == []
    assert project.connect("LINK_MAIN_TX", "U1.GPIO4").ok
    result = project.assign_pin("U1", "gpio", "io.extra", "GPIO4")
    assert not result.ok
    assert {issue.code for issue in result.errors} == {"signals-shorted"}


def test_edit_can_fix_one_error_while_another_remains(tmp_path: Path, example_path: Path) -> None:
    text = example_path.read_text().replace(
        "IMU_CS: [U1.GPIO10, U3.CS]", "IMU_CS: [U1.GPIO9, U3.CS]"
    )
    text = text.replace("IMU_MISO: [U1.GPIO13, U3.SDO]", "IMU_MISO: [U1.GPIO14, U3.SDO]")
    example_path.write_text(text)
    project = Project.load(example_path)
    assert len([i for i in project.check() if i.code == "assignment-unconnected"]) == 2
    result = project.disconnect("IMU_CS", "U1.GPIO9")
    assert result.ok
    assert project.connect("IMU_CS", "U1.GPIO10").ok
    assert len([i for i in project.check() if i.code == "assignment-unconnected"]) == 1


def test_edit_save_reload_preserves_comments(project: Project, example_path: Path) -> None:
    assert project.move_pin("U1", "GPIO10", "GPIO9").ok
    assert project.save() is True
    text = example_path.read_text()
    assert "# Inter-MCU link, crossed TX/RX." in text
    assert "10_000_000" in text
    assert 'puffin: "0.1"' in text
    reloaded = Project.load(example_path)
    assert reloaded.check() == []
    assert reloaded.save() is False
