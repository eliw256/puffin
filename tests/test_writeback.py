"""Scoped KiCad write-back (docs/WRITEBACK.md): geometry, shapes, verification, checkpoints."""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from puffin.cli.main import main
from puffin.core import Op, Project
from puffin.kicad import read_netlist
from puffin.kicad.export import KiCadCliError, export_netlist, find_kicad_cli
from puffin.kicad.schematic import fmt, read_schematic
from puffin.kicad.writeback import Move, Refusal, edit_move, lock_file, plan_writeback
from puffin.mcp.service import PuffinService, ToolError
from tests.conftest import PROJECT

SCH = "hw/robot/robot.kicad_sch"
FIXTURE_SCH = PROJECT.parent / SCH
needs_kicad = pytest.mark.skipif(find_kicad_cli() is None, reason="kicad-cli not installed")


def move(target: str, source: str = "GPIO10") -> list[dict[str, str]]:
    return [{"op": "move_pin", "component": "U1", "from_pin": source, "to_pin": target}]


def plans_for(root: Path, target: str, **kwargs: Any) -> list[Any]:
    project = Project.load(root / "puffin.yaml")
    ops = [Op.from_dict(op) for op in move(target)]
    clone = Project(project.path, project.dumps(), project.library)
    assert clone.batch(ops).ok
    return plan_writeback(root, project.model, clone.model, ops, project.library, **kwargs)


def edit_schematic(root: Path, *replacements: tuple[str, str]) -> None:
    path = root / SCH
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


# --- geometry -----------------------------------------------------------------------------


def test_every_placed_pin_lands_where_kicad_connects_it() -> None:
    sch = read_schematic(FIXTURE_SCH.read_text(encoding="utf-8"))
    netlist = read_netlist((PROJECT.parent / "hw/robot/robot.net").read_text(encoding="utf-8"))
    ends = {(p.ref, p.number): p.point for p in sch.all_pin_ends()}
    wire_ends = {w.a for w in sch.wires} | {w.b for w in sch.wires}
    no_connects = {m.at for m in sch.markers if m.kind == "no_connect"}
    label_points = {label.at for label in sch.labels}
    points = list(ends.values())
    for net in netlist.nets:
        for node in net.nodes:
            point = ends[(node.ref, node.pin)]
            if net.kind == "unconnected":
                assert point in no_connects, (node, point)
            else:
                assert point in wire_ends | label_points or points.count(point) > 1, (node, point)


def test_rotated_power_symbols_touch_their_wires() -> None:
    sch = read_schematic(FIXTURE_SCH.read_text(encoding="utf-8"))
    wire_ends = {w.a for w in sch.wires} | {w.b for w in sch.wires}
    rotated = {ref for ref, inst in sch.instances.items() if inst.angle in (90, 270)}
    assert rotated == {"#PWR03", "#FLG01", "#FLG02"}
    points = [p.point for p in sch.all_pin_ends()]
    for ref in rotated:
        for pin in sch.pin_ends(ref):
            assert pin.point in wire_ends or points.count(pin.point) > 1, pin


@pytest.mark.parametrize(
    ("pad", "point", "outward"),
    [("18", ("49.53", "100.33"), 180), ("22", ("49.53", "110.49"), 180),
     ("23", ("80.01", "85.09"), 0)],  # IO10, IO14 (left side), IO21 (right side)
)  # fmt: skip
def test_pin_end_table(pad: str, point: tuple[str, str], outward: int) -> None:
    pin = read_schematic(FIXTURE_SCH.read_text(encoding="utf-8")).pin_end("U1", pad)
    assert pin.point == (Decimal(point[0]), Decimal(point[1])) and pin.outward == outward


def test_coordinates_print_like_kicad() -> None:
    assert [fmt(Decimal(v)) for v in ("110.4900", "48.26", "-0.0", "3", "1.27000")] == [
        "110.49", "48.26", "0", "3", "1.27",
    ]  # fmt: skip


# --- shapes and the text edit (kicad-cli verified) ----------------------------------------


@needs_kicad
def test_same_side_move_changes_only_label_stub_and_no_connect(example_path: Path) -> None:
    [plan] = plans_for(example_path.parent, "GPIO4")
    assert plan.available and plan.verified, plan.reason
    before = plan.original.decode().splitlines()
    after = plan.modified.decode().splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [
        ("\t\t(at 49.53 85.09)", "\t\t(at 49.53 100.33)"),  # GPIO4's no-connect moves to GPIO10
        ("\t\t\t(xy 48.26 100.33) (xy 49.53 100.33)", "\t\t\t(xy 48.26 85.09) (xy 49.53 85.09)"),
        ("\t\t(at 48.26 100.33 180)", "\t\t(at 48.26 85.09 180)"),  # the IMU_CS label
    ]
    assert plan.edits == ["label IMU_CS and its wire moved to U1.GPIO4",
                          "no-connect moved from U1.GPIO4 to GPIO10"]  # fmt: skip


@needs_kicad
def test_opposite_side_move_rotates_label_and_flips_justify(example_path: Path) -> None:
    [plan] = plans_for(example_path.parent, "GPIO21")
    assert plan.available and plan.verified, plan.reason
    assert "+\t\t(at 81.28 85.09 0)\n" in plan.diff
    assert "+\t\t\t(xy 81.28 85.09) (xy 80.01 85.09)\n" in plan.diff
    assert "-\t\t\t(justify right bottom)\n+\t\t\t(justify left bottom)\n" in plan.diff


@needs_kicad
def test_free_target_without_no_connect_gets_a_deterministic_one(example_path: Path) -> None:
    root = example_path.parent
    path = root / SCH
    gpio4_no_connect = r'\t\(no_connect\n\t\t\(at 49\.53 85\.09\)\n\t\t\(uuid "[^"]+"\)\n\t\)\n'
    text, removed = re.subn(gpio4_no_connect, "", path.read_text(encoding="utf-8"))
    assert removed == 1
    path.write_text(text, encoding="utf-8")
    [first] = plans_for(root, "GPIO4")
    [second] = plans_for(root, "GPIO4")
    assert first.available and first.verified, first.reason
    assert first.modified == second.modified
    added = r'\n\t\(no_connect\n\t\t\(at 49\.53 100\.33\)\n\t\t\(uuid "[0-9a-f-]{36}"\)\n\t\)\n'
    assert re.search(added, first.modified.decode())
    assert first.edits[-1] == "no-connect added on U1.GPIO10"


@needs_kicad
def test_label_directly_on_the_pin_is_supported(example_path: Path) -> None:
    root = example_path.parent
    edit_schematic(
        root,
        ("\t\t\t(xy 48.26 100.33) (xy 49.53 100.33)", "\t\t\t(xy 10 10) (xy 11.27 10)"),
        ("\t\t(at 48.26 100.33 180)", "\t\t(at 49.53 100.33 180)"),
    )
    [plan] = plans_for(root, "GPIO4")
    assert plan.available and plan.verified, plan.reason
    assert plan.edits[0] == "label IMU_CS moved to U1.GPIO4"


# --- refusals -----------------------------------------------------------------------------


def extra_label(name: str, at: str, uid: str) -> tuple[str, str]:
    """A replacement pair inserting one more local label before IMU_MISO's."""
    block = f'\t(label "{name}"\n\t\t(at {at})\n\t\t(uuid "{uid}")\n\t)\n'
    return '\t(label "IMU_MISO"', block + '\t(label "IMU_MISO"'


def refused(root: Path, target: str = "GPIO4") -> str:
    [plan] = plans_for(root, target, verify_with=None)
    assert not plan.available
    assert plan.reason
    return str(plan.reason)


def test_refuses_second_label_at_stub_end(example_path: Path) -> None:
    root = example_path.parent
    edit_schematic(
        root,
        extra_label("EXTRA", "48.26 100.33 180", "aaaaaaaa-" + "0" * 4 + "-0000-0000-" + "0" * 12),
    )
    assert "does not end in exactly one local label" in refused(root)
    assert refused(root).endswith("rewire in KiCad")


def test_refuses_junction_on_source_pin(example_path: Path) -> None:
    root = example_path.parent
    edit_schematic(
        root, ("\t(junction\n\t\t(at 149.86 55.88)", "\t(junction\n\t\t(at 49.53 100.33)")
    )
    assert "connects through" in refused(root)


def test_refuses_global_label(example_path: Path) -> None:
    root = example_path.parent
    edit_schematic(root, ('\t(label "IMU_CS"\n\t\t(at 48.26 100.33 180)',
                          '\t(global_label "IMU_CS"\n\t\t(at 48.26 100.33 180)'))  # fmt: skip
    assert "does not end in exactly one local label" in refused(root)


def test_refuses_target_that_is_not_free(example_path: Path) -> None:
    root = example_path.parent
    edit_schematic(
        root,
        extra_label("STRAY", "49.53 85.09 180", "bbbbbbbb-" + "0" * 4 + "-0000-0000-" + "0" * 12),
    )
    assert "target U1.GPIO4 is not free (found 1 labels" in refused(root)


def test_refuses_mirrored_symbol(example_path: Path) -> None:
    root = example_path.parent
    edit_schematic(root, ('\t\t(lib_id "RF_Module:ESP32-S3-WROOM-1")\n\t\t(at 64.77 92.71 0)\n',
                          '\t\t(lib_id "RF_Module:ESP32-S3-WROOM-1")\n\t\t(at 64.77 92.71 0)\n'
                          "\t\t(mirror y)\n"))  # fmt: skip
    assert "mirrored" in refused(root)


def test_refuses_while_kicad_has_it_open(example_path: Path) -> None:
    root = example_path.parent
    lock_file(root / SCH).write_text("{}")
    assert "open in KiCad" in refused(root)


def test_refuses_90_degree_moves() -> None:
    """A minimal two-pin symbol: pin 1 points left, pin 2 points down."""
    text = """(kicad_sch
\t(lib_symbols
\t\t(symbol "T:X"
\t\t\t(symbol "X_1_1"
\t\t\t\t(pin bidirectional line (at -5.08 0 0) (length 2.54) (name "A") (number "1"))
\t\t\t\t(pin bidirectional line (at 0 -5.08 90) (length 2.54) (name "B") (number "2"))
\t\t\t)
\t\t)
\t)
\t(no_connect
\t\t(at 50.8 55.88)
\t\t(uuid "cccccccc-0000-0000-0000-000000000000")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 44.45 50.8) (xy 45.72 50.8)
\t\t)
\t)
\t(label "N"
\t\t(at 44.45 50.8 180)
\t)
\t(symbol
\t\t(lib_id "T:X")
\t\t(at 50.8 50.8 0)
\t\t(unit 1)
\t\t(property "Reference" "U9")
\t)
)
"""
    with pytest.raises(Refusal, match="points 90 degrees"):
        edit_move(text, Move("U9", "A", "B", "N", "1", "2"), "sha")


def test_refuses_edits_beyond_pin_moves(example_path: Path) -> None:
    service = PuffinService(example_path)
    ops = [{"op": "set_net", "name": "IMU_CS", "endpoints": ["U1.GPIO4", "U3.CS"]},
           {"op": "set_assignment_pin", "component": "U1", "peripheral": "spi2",
            "signal": "cs.imu", "pin": "GPIO4"}]  # fmt: skip
    proposal = service.propose_edit(ops, writeback=True)
    assert not proposal["ok"] and proposal["proposal_id"] is None
    [error] = proposal["errors"]
    assert error["code"] == "writeback-unavailable" and "move_pin only" in error["message"]


# --- verification -------------------------------------------------------------------------


def test_missing_kicad_cli_makes_writeback_unavailable(example_path: Path) -> None:
    def no_cli(_path: Path) -> str:
        raise KiCadCliError("kicad-cli not found")

    [plan] = plans_for(example_path.parent, "GPIO4", verify_with=no_cli)
    assert not plan.available and "kicad-cli is needed to verify write-back" in plan.reason


def test_wrong_connectivity_fails_verification(example_path: Path) -> None:
    original = (example_path.parent / "hw/robot/robot.net").read_text()
    [plan] = plans_for(example_path.parent, "GPIO4", verify_with=lambda _path: original)
    assert not plan.available and plan.reason.startswith("verification failed")
    assert not plan.verified


# --- transactions, checkpoint, CLI and MCP (kicad-cli) ------------------------------------


def snapshot(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel in ("puffin.yaml", SCH, ".puffin/state.json")}


def write_back(service: PuffinService, target: str, source: str = "GPIO10") -> dict[str, Any]:
    proposal = service.propose_edit(move(target, source), writeback=True)
    assert proposal["ok"], proposal
    return service.apply_edit(proposal["proposal_id"])


def check_codes(root: Path, capsys: pytest.CaptureFixture[str]) -> str:
    main(["check", str(root)])
    return capsys.readouterr().out


@needs_kicad
def test_proposal_shows_both_diffs_and_apply_leaves_no_drift(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = example_path.parent
    service = PuffinService(example_path)
    proposal = service.propose_edit(move("GPIO4"), writeback=True)
    assert proposal["ok"]
    assert "+  IMU_CS: [U1.GPIO4, U3.CS]" in proposal["diff"]
    [plan] = proposal["writeback"]
    assert plan["verified"] and "(at 48.26 85.09 180)" in plan["diff"]
    codes = [w["code"] for w in proposal["warnings"]]
    assert "kicad-writeback" in codes and "kicad-overwrite" not in codes
    assert (root / SCH).read_bytes() == FIXTURE_SCH.read_bytes()  # proposing writes nothing

    applied = service.apply_edit(proposal["proposal_id"])
    assert applied["changed_artifacts"] == ["puffin.yaml", SCH]
    out = check_codes(root, capsys)
    assert "drift" not in out and "schematic-" not in out
    assert "warning[writeback-unconfirmed] .puffin/backup: 1 unconfirmed write-back(s)" in out
    assert main(["import", "kicad", str(root), "--dry-run"]) == 0
    assert "in sync" in capsys.readouterr().out
    history = (root / ".puffin/history.jsonl").read_text().splitlines()
    assert json.loads(history[-1])["kind"] == "edit+writeback"


@needs_kicad
def test_two_writebacks_then_restore_undoes_both(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = example_path.parent
    original = snapshot(root)
    service = PuffinService(example_path)
    write_back(service, "GPIO4")
    write_back(service, "GPIO5", source="GPIO4")
    assert "2 unconfirmed write-back(s)" in check_codes(root, capsys)
    assert main(["writeback", "restore", str(root)]) == 0
    assert "undoing 2 write-back(s)" in capsys.readouterr().out
    assert snapshot(root) == original
    assert not (root / ".puffin/backup").exists()
    assert "0 error(s), 0 warning(s)" in check_codes(root, capsys)


@needs_kicad
def test_two_writebacks_then_confirm_clears_the_checkpoint(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = example_path.parent
    service = PuffinService(example_path)
    write_back(service, "GPIO4")
    write_back(service, "GPIO5", source="GPIO4")
    assert main(["writeback", "confirm", str(root)]) == 0
    assert "confirmed 2 write-back(s)" in capsys.readouterr().out
    assert not (root / ".puffin/backup").exists()
    out = check_codes(root, capsys)
    assert "0 error(s), 0 warning(s)" in out
    assert "IMU_CS: [U1.GPIO5, U3.CS]" in example_path.read_text()


@needs_kicad
def test_failure_mid_apply_changes_nothing(
    example_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = example_path.parent
    original = snapshot(root)
    service = PuffinService(example_path)
    proposal = service.propose_edit(move("GPIO4"), writeback=True)

    def broken(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("puffin.mcp.service.record_sync", broken)
    with pytest.raises(OSError, match="disk full"):
        service.apply_edit(proposal["proposal_id"])
    assert snapshot(root) == original
    assert not (root / ".puffin/backup").exists()
    assert service.get_project()["nets"]["IMU_CS"] == ["U1.GPIO10", "U3.CS"]


@needs_kicad
def test_schematic_edited_after_proposal_is_stale(example_path: Path) -> None:
    root = example_path.parent
    service = PuffinService(example_path)
    proposal = service.propose_edit(move("GPIO4"), writeback=True)
    (root / SCH).write_bytes((root / SCH).read_bytes() + b"\n")
    with pytest.raises(ToolError) as caught:
        service.apply_edit(proposal["proposal_id"])
    assert caught.value.issue.code == "stale-proposal"


@needs_kicad
def test_kicad_opened_after_proposal_is_refused(example_path: Path) -> None:
    root = example_path.parent
    service = PuffinService(example_path)
    proposal = service.propose_edit(move("GPIO4"), writeback=True)
    lock_file(root / SCH).write_text("{}")
    with pytest.raises(ToolError) as caught:
        service.apply_edit(proposal["proposal_id"])
    assert caught.value.issue.code == "writeback-unavailable"
    assert (root / SCH).read_bytes() == FIXTURE_SCH.read_bytes()


def test_overwrite_hint_offers_writeback_or_says_why_not(example_path: Path) -> None:
    service = PuffinService(example_path)
    [warning] = [w for w in service.propose_edit(move("GPIO4"))["warnings"]
                 if w["code"] == "kicad-overwrite"]  # fmt: skip
    assert warning["hint"].startswith("write-back can apply this to hw/robot/robot.kicad_sch")
    lock_file(example_path.parent / SCH).write_text("{}")
    [blocked] = [w for w in service.propose_edit(move("GPIO4"))["warnings"]
                 if w["code"] == "kicad-overwrite"]  # fmt: skip
    assert blocked["hint"].startswith("write-back can't apply this (robot.kicad_sch is open")


@needs_kicad
def test_cli_move_pin_with_writeback(
    example_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = example_path.parent
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    args = ["move-pin", "U1", "GPIO10", "GPIO4", "--path", str(root), "--writeback"]
    assert main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "+\t\t(at 48.26 85.09 180)" in out and "dry run" in out
    assert main(args) == 1
    assert "refusing to apply without --yes" in capsys.readouterr().err
    assert main([*args, "--yes"]) == 0
    assert "applied to puffin.yaml, hw/robot/robot.kicad_sch" in capsys.readouterr().out


def test_writeback_commands_with_nothing_pending(
    example_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["writeback", "confirm", str(example_path.parent)]) == 0
    assert main(["writeback", "restore", str(example_path.parent)]) == 0
    out = capsys.readouterr().out
    assert "nothing to confirm" in out and "nothing to restore" in out


@needs_kicad
def test_unmanaged_connectivity_change_fails_verification(example_path: Path) -> None:
    """The graph only sees managed parts; verification compares every part's connectivity."""
    calls: list[Path] = []

    def tampered(path: Path) -> str:
        calls.append(path)
        text = export_netlist(path)
        if len(calls) == 2:  # the edited schematic's export: C4 silently drops off GND
            text = text.replace('(node (ref "C4") (pin "2") (pintype "passive"))', "", 1)
        return text

    [plan] = plans_for(example_path.parent, "GPIO4", verify_with=tampered)
    assert not plan.available
    assert plan.reason == (
        "verification failed: kicad-cli's netlist of the edited schematic differs at GND; "
        "nothing was changed"
    )
