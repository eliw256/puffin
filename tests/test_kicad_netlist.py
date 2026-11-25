"""Netlist parsing and pin mapping (M2 Step 2)."""

from __future__ import annotations

import pytest

from puffin.kicad import NetlistError, Node, map_pin, read_netlist
from puffin.kicad.sexpr import SExprError, parse
from puffin.library import PartDefinition, PartLibrary
from tests.conftest import EXAMPLE

FIXTURE = EXAMPLE.parent / "hw" / "robot" / "robot.net"
LIBRARY = PartLibrary()


def netlist_text(body: str = "") -> str:
    return f'(export (version "E") {body})'


def test_sexpr_parses_quoted_and_bare_atoms() -> None:
    assert parse('(a "b c" d (e "f\\"g"))') == ["a", "b c", "d", ["e", 'f"g']]


@pytest.mark.parametrize("text", ["", "a", "(a", "(a))", '(a "b)'])
def test_sexpr_rejects_malformed_text(text: str) -> None:
    with pytest.raises(SExprError):
        parse(text)


def test_quoted_paren_is_text_not_structure() -> None:
    assert parse('(name "Net-(U1-EN)")') == ["name", "Net-(U1-EN)"]


def test_rejects_other_versions_and_non_netlists() -> None:
    with pytest.raises(NetlistError, match="version"):
        read_netlist('(export (version "D"))')
    with pytest.raises(NetlistError, match="export"):
        read_netlist('(kicad_sch (version "E"))')
    with pytest.raises(NetlistError):
        read_netlist("not a netlist")


def test_empty_fields_are_absent() -> None:
    text = netlist_text(
        '(components (comp (ref "U9") (fields (field (name "Puffin_Part") "a/b")'
        ' (field (name "Puffin_Variant")) (field (name "Puffin_Board") "  "))'
        ' (libsource (lib "") (part "X"))))'
    )
    comp = read_netlist(text).components["U9"]
    assert comp.puffin("Puffin_Part") == "a/b"
    assert comp.puffin("Puffin_Variant") is None
    assert comp.puffin("Puffin_Board") is None
    assert comp.libpart == "X"


def test_fixture_nets_and_kinds() -> None:
    netlist = read_netlist(FIXTURE.read_text(encoding="utf-8"))
    kinds = {net.name: net.kind for net in netlist.nets}
    assert kinds["/IMU_SCK"] == "labelled"
    assert kinds["+3V3"] == "labelled"
    assert kinds["Net-(U1-EN)"] == "auto"
    assert kinds["unconnected-(U1-IO1-Pad39)"] == "unconnected"
    sck = next(net for net in netlist.nets if net.name == "/IMU_SCK")
    assert sck.nodes == (
        Node("U1", "20", "IO12", "bidirectional"),
        Node("U3", "1", "SCK", "input"),
    )
    assert netlist.components["R1"].libpart == "Device:R"
    assert netlist.components["U1"].puffin("Puffin_Board") == "main"
    assert netlist.components["U2"].puffin("Puffin_Variant") == "N8"


def test_no_connect_pins_are_marked() -> None:
    netlist = read_netlist(FIXTURE.read_text(encoding="utf-8"))
    lone = next(net for net in netlist.nets if net.name == "unconnected-(U2-IO0-Pad27)")
    assert lone.nodes[0].no_connect


def wroom() -> PartDefinition:
    part = LIBRARY.get("espressif/esp32-s3-wroom-1")
    assert part is not None
    return part


@pytest.mark.parametrize(
    ("pad", "function", "expected"),
    [
        ("27", "IO0", "GPIO0"),  # by number
        ("40", "GND", "GND"),  # several pads, one pin
        ("41", "GND", "GND"),
        ("27", "wrong-name", "GPIO0"),  # number wins over name
        ("99", "IO12", "GPIO12"),  # no number match: datasheet_name
        ("99", "GPIO12", "GPIO12"),  # no number match: part pin key
        ("99", "NOPE", None),
        ("99", None, None),
    ],
)
def test_map_pin_wroom(pad: str, function: str | None, expected: str | None) -> None:
    assert map_pin(wroom(), Node("U1", pad, function, None)) == expected


def test_map_pin_by_name_for_part_without_numbers() -> None:
    imu = LIBRARY.get("example/spi-imu")
    assert imu is not None
    assert map_pin(imu, Node("U3", "1", "SCK", "input")) == "SCK"
    assert map_pin(imu, Node("U3", "7", "INT", "output")) is None


def test_every_fixture_mcu_node_maps() -> None:
    netlist = read_netlist(FIXTURE.read_text(encoding="utf-8"))
    part = wroom()
    for net in netlist.nets:
        for node in net.nodes:
            if node.ref in {"U1", "U2"}:
                assert map_pin(part, node) is not None, node


def test_fixture_u3_fields_with_empty_variant() -> None:
    u3 = read_netlist(FIXTURE.read_text(encoding="utf-8")).components["U3"]
    assert u3.puffin("Puffin_Part") == "example/spi-imu"
    assert u3.puffin("Puffin_Board") == "main"
    assert "Puffin_Variant" in u3.fields
    assert u3.puffin("Puffin_Variant") is None
    assert u3.libpart == "Placeholder:IMU_Placeholder"
