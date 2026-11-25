"""Change-set builder (M2 Step 4), driven by hand-edited copies of the netlist fixture."""

from __future__ import annotations

import json
from pathlib import Path

from puffin.core import Issue, Project, Severity
from puffin.kicad import read_netlist
from puffin.kicad.changes import ChangeSet, build_changes
from tests.conftest import EXAMPLE

FIXTURE_TEXT = (EXAMPLE.parent / "hw" / "robot" / "robot.net").read_text(encoding="utf-8")
SCHEMATIC = "hw/robot/robot.kicad_sch"


def node(ref: str, pin: str, function: str | None, pintype: str = "bidirectional") -> str:
    fn = f' (pinfunction "{function}")' if function else ""
    return f'(node (ref "{ref}") (pin "{pin}"){fn} (pintype "{pintype}"))'


SCK_U1 = node("U1", "20", "IO12")
SCK_U3 = node("U3", "1", "SCK", "input")


def edited(*replacements: tuple[str, str]) -> str:
    text = FIXTURE_TEXT
    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new, 1)
    return text


def changes_for(project: Project, text: str = FIXTURE_TEXT) -> ChangeSet:
    return build_changes(project.model, project.library, read_netlist(text), SCHEMATIC)


def codes(issues: list[Issue]) -> list[str]:
    return [issue.code for issue in issues]


def resistor(ref: str) -> str:
    return (
        f'(comp (ref "{ref}") (value "33") (libsource (lib "Device") (part "R")'
        ' (description "Resistor")))\n    (comp (ref "SW1")'
    )


def test_committed_example_is_in_sync(project: Project) -> None:
    changes = changes_for(project)
    assert changes.empty and not changes.warnings and not changes.errors
    assert changes.unmanaged == {"Device:C": 4, "Device:R": 3, "Switch:SW_Push": 1}


def test_moved_pin_proposes_endpoint_change_and_assignment_move(project: Project) -> None:
    changes = changes_for(project, edited((SCK_U1, node("U1", "22", "IO14"))))
    [change] = changes.endpoint_changes
    assert (change.net, change.removed, change.added) == ("IMU_SCK", ("U1.GPIO12",),
                                                          ("U1.GPIO14",))  # fmt: skip
    [fix] = changes.assignment_fixes
    assert (fix.peripheral, fix.signal, fix.old_pin, fix.new_pin) == ("spi2", "sck", "GPIO12",
                                                                      "GPIO14")  # fmt: skip
    assert not changes.ambiguous
    result = project.batch(changes.ops)
    assert result.ok, result.errors
    assert project.model.nets["IMU_SCK"] == ["U1.GPIO14", "U3.SCK"]


def test_swap_in_kicad_resolves_in_one_batch(project: Project) -> None:
    mosi_u1 = node("U1", "19", "IO11")
    changes = changes_for(project, edited((SCK_U1, "@SCK@"), (mosi_u1, SCK_U1), ("@SCK@", mosi_u1)))
    assert {(f.signal, f.new_pin) for f in changes.assignment_fixes} == {
        ("sck", "GPIO11"),
        ("mosi", "GPIO12"),
    }
    assert project.batch(changes.ops).ok


def test_rename_preserves_constraint(project: Project) -> None:
    changes = changes_for(project, edited(('(name "/IMU_SCK")', '(name "/SPI_CLK")')))
    assert changes.nets_renamed == [("IMU_SCK", "SPI_CLK")]
    assert not changes.nets_added and not changes.nets_removed
    assert project.batch(changes.ops).ok
    assert project.model.constraints[0].net == "SPI_CLK"
    assert "SPI_CLK" in project.model.nets and "IMU_SCK" not in project.model.nets


def test_trading_labels_is_an_endpoint_swap_not_a_rename(project: Project) -> None:
    text = edited(
        ('(name "/IMU_SCK")', '(name "@A@")'),
        ('(name "/IMU_MOSI")', '(name "/IMU_SCK")'),
        ('(name "@A@")', '(name "/IMU_MOSI")'),
    )
    changes = changes_for(project, text)
    assert not changes.nets_renamed
    assert len(changes.endpoint_changes) == 2


def test_unmanaged_parts_never_enter_the_graph(project: Project) -> None:
    changes = changes_for(project, edited((SCK_U1, node("U1", "22", "IO14"))))
    project.batch(changes.ops)
    everything = json.dumps(changes.to_dict()) + project.dumps().decode()
    for ref in ("R1", "R2", "R3", "C1", "C2", "SW1"):
        assert f'"{ref}.' not in everything and f" {ref}." not in everything


def test_unmappable_pin_is_an_error(project: Project) -> None:
    changes = changes_for(project, edited((SCK_U1, node("U1", "99", "IO99"))))
    assert codes(changes.errors) == ["import-unmapped-pin"]
    assert changes.errors[0].where == "U1.99"
    assert "pad 99 (IO99)" in changes.errors[0].message


def test_two_arrivals_are_ambiguous_not_guessed(project: Project) -> None:
    changes = changes_for(
        project, edited((SCK_U1, node("U1", "22", "IO14") + " " + node("U1", "23", "IO21")))
    )
    assert not changes.assignment_fixes
    [ambiguity] = changes.ambiguous
    assert (ambiguity.signal, ambiguity.pin) == ("sck", "GPIO12")
    assert "1 pin(s) left net IMU_SCK and 2 arrived" in ambiguity.reason


def test_removed_net_reports_assignments_and_constraint_blocks_apply(project: Project) -> None:
    text = edited((f"{SCK_U1}\n      {SCK_U3}", node("U9", "1", None)))
    changes = changes_for(project, text)
    assert changes.nets_removed == [("IMU_SCK", ("U1.GPIO12", "U3.SCK"))]
    assert [(a.signal, a.pin) for a in changes.ambiguous] == [("sck", "GPIO12")]
    preview = project.batch(changes.ops, dry_run=True)
    assert not preview.ok
    assert {"unknown-net", "assignment-unconnected"} <= set(codes(preview.errors))


def test_moved_to_another_net_names_it(project: Project) -> None:
    # GPIO12 leaves IMU_SCK and joins IMU_CS: neither net has the 1-out/1-in shape.
    cs_u1 = node("U1", "18", "IO10")
    changes = changes_for(project, edited((SCK_U1, ""), (cs_u1, cs_u1 + " " + SCK_U1)))
    reasons = {a.signal: a.reason for a in changes.ambiguous}
    assert "GPIO12 is now on IMU_CS" in reasons["sck"]


def test_series_passive_warns_once_and_names_the_constrained_half(project: Project) -> None:
    text = edited(
        ('(comp (ref "SW1")', resistor("R5")),
        (SCK_U3, node("R5", "1", None, "passive")),
        ('(net (code "7")', '(net (code "99") (name "Net-(R5-Pad2)") (class "Default")\n      '
         + node("R5", "2", None, "passive") + "\n      " + SCK_U3 + ")\n    (net (code \"7\")"),
    )  # fmt: skip
    changes = changes_for(project, text)
    series = [w for w in changes.warnings if w.code == "import-series-passive"]
    assert [w.message for w in series] == [
        "IMU_SCK is split by R5 (Device:R); the far side imports as AUTO(U3.SCK); "
        "the constraint covers only the U1.GPIO12 side, net IMU_SCK"
    ]
    assert series[0].severity is Severity.WARNING
    assert ("AUTO(U3.SCK)", ("U3.SCK",)) in changes.nets_added
    assert changes.unmanaged["Device:R"] == 4


def test_pull_ups_and_decoupling_are_not_series_passives(project: Project) -> None:
    # R1 (pull-up to +3V3) bridges two managed nets but one is a power rail.
    assert not [w for w in changes_for(project).warnings if w.code == "import-series-passive"]


def test_unlabelled_net_on_assigned_pin_warns(project: Project) -> None:
    changes = changes_for(project, edited(('(name "/IMU_SCK")', '(name "Net-(U1-IO12)")')))
    assert "import-unlabelled-net" in codes(changes.warnings)
    assert changes.nets_renamed == [("IMU_SCK", "AUTO(U1.GPIO12,U3.SCK)")]


def test_symbol_without_part_field_matches_by_reference_with_warning(project: Project) -> None:
    text = edited(('(field (name "Puffin_Part") "example/spi-imu")', ""))
    changes = changes_for(project, text)
    assert changes.empty
    [warning] = changes.warnings
    assert (warning.code, warning.where) == ("import-no-part-field", "U3")


def test_new_component_needs_a_board(project: Project) -> None:
    fields = '(fields (field (name "Puffin_Part") "example/spi-imu"))'
    comp = f'(comp (ref "U4") {fields} (libsource (lib "X") (part "Y")))\n    (comp (ref "SW1")'
    changes = changes_for(project, edited(('(comp (ref "SW1")', comp)))
    assert codes(changes.errors) == ["import-missing-board"]

    with_board = comp.replace('"example/spi-imu")', '"example/spi-imu") '
                              '(field (name "Puffin_Board") "main")')  # fmt: skip
    changes = changes_for(project, edited(('(comp (ref "SW1")', with_board)))
    assert [(c.ref, c.board) for c in changes.components_added] == [("U4", "main")]
    assert project.batch(changes.ops).ok


def test_part_and_variant_updates_come_from_kicad(project: Project) -> None:
    text = edited(
        ('(field (name "Puffin_Variant") "N8")', '(field (name "Puffin_Variant") "N16R8")')
    )
    changes = changes_for(project, text)
    assert [(c.ref, c.field, c.old, c.new) for c in changes.components_updated] == [
        ("U2", "variant", "N8", "N16R8")
    ]


def test_removed_symbol_is_listed_and_blocked_by_references(project: Project) -> None:
    start = FIXTURE_TEXT.index('    (comp (ref "U3")')
    end = FIXTURE_TEXT.index("  (libparts")
    text = FIXTURE_TEXT[:start].rstrip() + ")\n" + FIXTURE_TEXT[end:]
    text = text.replace(f"{SCK_U1}\n      {SCK_U3}", SCK_U1)
    changes = changes_for(project, text)
    assert changes.components_removed == ["U3"]
    preview = project.batch(changes.ops, dry_run=True)
    assert not preview.ok and "still referenced" in preview.errors[0].message


def test_component_on_another_schematic_is_an_error(example_path: Path) -> None:
    text = example_path.read_text().replace(
        "  motor:\n    kicad_schematic: hw/robot/robot.kicad_sch",
        "  motor:\n    kicad_schematic: hw/motor/motor.kicad_sch",
    )
    example_path.write_text(text)
    changes = changes_for(Project.load(example_path))
    assert codes(changes.errors) == ["import-foreign-component"]
    assert changes.errors[0].where == "U2"


def test_overlap_tie_is_remove_plus_add(project: Project) -> None:
    # One imported net overlapping two graph nets equally must not pick either.
    merged = edited(
        (f"{SCK_U1}\n      {SCK_U3}", f"{SCK_U1}\n      {node('U1', '19', 'IO11')}"),
        (f"{node('U1', '19', 'IO11')}\n      {node('U3', '2', 'SDI', 'input')}",
         node("U3", "2", "SDI", "input")),
        ('(name "/IMU_SCK")', '(name "/MERGED")'),
        ('(name "/IMU_MOSI")', '(name "/OTHER")'),
    )  # fmt: skip
    changes = changes_for(project, merged)
    assert ("MERGED", ("U1.GPIO11", "U1.GPIO12")) in changes.nets_added
    assert "IMU_SCK" in {name for name, _ in changes.nets_removed}
    assert not changes.nets_renamed


def test_output_is_deterministic(project: Project) -> None:
    text = edited((SCK_U1, node("U1", "22", "IO14")))
    first = json.dumps(changes_for(project, text).to_dict(), sort_keys=False)
    assert first == json.dumps(changes_for(project, text).to_dict(), sort_keys=False)
