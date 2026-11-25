"""Every rule has one passing and one failing case. A new rule without cases fails the suite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from puffin.core import RULES, ProjectModel, validate
from puffin.library import PartLibrary
from puffin.library.loader import bundled_parts_dir

Data = dict[str, Any]
Mutator = Callable[[Data], None]
WROOM = "espressif/esp32-s3-wroom-1"
LIBRARY = PartLibrary([Path(__file__).parent / "fixtures" / "parts", bundled_parts_dir()])


def base() -> Data:
    return {
        "puffin": "0.1",
        "project": "t",
        "boards": {
            "a": {
                "firmware": {
                    "framework": "esp-idf",
                    "target": "esp32s3",
                    "mcu": "U1",
                    "output": "p.h",
                }
            },
            "b": {},
        },
        "components": {
            "U1": {"part": WROOM, "board": "a", "variant": "N16R8"},
            "U2": {"part": "example/spi-imu", "board": "a"},
        },
        "nets": {
            "SCK": ["U1.GPIO12", "U2.SCK"],
            "CS": ["U1.GPIO10", "U2.CS"],
            "VCC": ["U1.3V3", "U2.VDD"],
        },
        "assignments": {"U1": {"spi2": {"sck": "GPIO12", "cs.imu": "GPIO10"}}},
    }


def codes(data: Data) -> set[str]:
    return {i.code for i in validate(ProjectModel.model_validate(data), LIBRARY)}


def assign(data: Data, peripheral: str, signal: str, pin: str, net: str | None = None) -> None:
    data["assignments"]["U1"].setdefault(peripheral, {})[signal] = pin
    if net:
        data["nets"][net] = [f"U1.{pin}"]


def override(pin: str, allow: str) -> Mutator:
    def mutate(data: Data) -> None:
        data.setdefault("overrides", []).append({"pin": pin, "allow": allow, "note": "intentional"})

    return mutate


def chain(*mutators: Mutator) -> Mutator:
    def mutate(data: Data) -> None:
        for mutator in mutators:
            mutator(data)

    return mutate


def use(pin: str) -> Mutator:
    return lambda d: d["nets"].__setitem__("X", [f"U1.{pin}"])


def short_sck_and_cs(data: Data) -> None:
    """Two assigned signals of one MCU end up on the same net."""
    data["nets"]["SCK"].append("U1.GPIO10")
    del data["nets"]["CS"]


def link_two_mcus(data: Data) -> None:
    """Assigned signals of two different MCUs on one net are a normal link."""
    data["components"]["U3"] = {"part": WROOM, "board": "b", "variant": "N8"}
    data["nets"]["LINK"] = ["U1.GPIO17", "U3.GPIO18"]
    assign(data, "uart1", "tx", "GPIO17")
    data["assignments"]["U3"] = {"uart1": {"rx": "GPIO18"}}


def part_of(ref: str, part: str) -> Mutator:
    return lambda d: d["components"][ref].update(part=part)


def add_imu(ref: str) -> Mutator:
    return lambda d: d["components"].__setitem__(ref, {"part": "example/spi-imu", "board": "a"})


def move_firmware_to_b(data: Data) -> None:
    data["components"]["U1"]["board"] = "b"
    data["boards"]["b"] = data["boards"].pop("a")
    data["components"]["U2"]["board"] = "b"
    data["boards"]["a"] = {}


# Each rule: (near-miss that must be fully clean, minimal change that must trigger the rule).
CASES: dict[str, tuple[Mutator, Mutator]] = {
    "unknown-part": (add_imu("U4"), part_of("U2", "nope/none")),
    "invalid-part": (part_of("U2", "local/imu"), part_of("U2", "local/broken")),
    "unknown-board": (
        lambda d: d["components"]["U2"].update(board="b"),
        lambda d: d["components"]["U2"].update(board="zzz"),
    ),
    "unknown-variant": (
        lambda d: d["components"]["U1"].update(variant="N8"),
        lambda d: d["components"]["U1"].update(variant="N99"),
    ),
    "unknown-component": (
        chain(add_imu("U4"), lambda d: d["nets"]["VCC"].append("U4.VDD")),
        lambda d: d["nets"]["VCC"].append("U9.VDD"),
    ),
    "unknown-pin": (use("GPIO21"), use("GPIO99")),
    "unknown-net": (
        lambda d: d.update(constraints=[{"net": "SCK", "max_freq_hz": 1}]),
        lambda d: d.update(constraints=[{"net": "NOPE", "max_freq_hz": 1}]),
    ),
    "pin-net-conflict": (
        lambda d: d["nets"]["CS"].append("U1.GPIO4"),
        lambda d: d["nets"]["CS"].append("U1.GPIO12"),
    ),
    "unknown-peripheral": (
        lambda d: assign(d, "i2c0", "sda", "GPIO4", "X"),
        lambda d: assign(d, "spi9", "sda", "GPIO4", "X"),
    ),
    "unknown-signal": (
        lambda d: assign(d, "spi2", "mosi", "GPIO4", "X"),
        lambda d: assign(d, "spi2", "bogus", "GPIO4", "X"),
    ),
    "pin-function": (
        lambda d: assign(d, "adc1", "ch3", "GPIO4", "X"),
        lambda d: assign(d, "adc1", "ch3", "GPIO5", "X"),
    ),
    "pin-double-assigned": (
        lambda d: assign(d, "spi2", "mosi", "GPIO11", "X"),
        lambda d: assign(d, "spi2", "mosi", "GPIO12"),
    ),
    "flagged-pin": (chain(use("GPIO0"), override("U1.GPIO0", "strapping")), use("GPIO0")),
    "assignment-unconnected": (
        lambda d: (
            d["nets"].update(SCK=["U1.GPIO14", "U2.SCK"]),
            assign(d, "spi2", "sck", "GPIO14"),
        ),
        lambda d: d["nets"].update(SCK=["U1.GPIO14", "U2.SCK"]),
    ),
    "signals-shorted": (link_two_mcus, short_sck_and_cs),
    "signal-on-power-net": (
        lambda d: assign(d, "spi2", "mosi", "GPIO4", "X"),
        lambda d: (assign(d, "spi2", "mosi", "GPIO4"), d["nets"]["VCC"].append("U1.GPIO4")),
    ),
    "supply-conflict": (
        lambda d: d["nets"].update(GND=["U1.GND", "U2.GND"]),
        lambda d: d["nets"]["VCC"].append("U1.GND"),
    ),
    "firmware-mcu-board": (move_firmware_to_b, lambda d: d["components"]["U1"].update(board="b")),
    "unused-override": (
        chain(use("GPIO0"), override("U1.GPIO0", "strapping")),
        override("U1.GPIO4", "strapping"),
    ),
}


def test_every_rule_has_cases() -> None:
    assert set(CASES) == set(RULES)


def test_baseline_is_clean() -> None:
    assert codes(base()) == set()


@pytest.mark.parametrize("code", sorted(CASES))
def test_rule_passes(code: str) -> None:
    data = base()
    CASES[code][0](data)
    assert codes(data) == set()


@pytest.mark.parametrize("code", sorted(CASES))
def test_rule_fails(code: str) -> None:
    data = base()
    CASES[code][1](data)
    assert code in codes(data)


def test_chip_pin_absent_from_module_is_unknown() -> None:
    """GPIO33 exists on the ESP32-S3 chip but is not brought out on the WROOM-1 module."""
    data = base()
    use("GPIO33")(data)
    assert "unknown-pin" in codes(data)


@pytest.mark.parametrize(
    ("variant", "pin", "flagged"),
    [
        ("N8", "GPIO35", False),
        ("N16R8", "GPIO35", True),
        (None, "GPIO35", True),
        ("N16R8", "GPIO47", False),
        ("N16R16VA7", "GPIO47", True),
    ],
)
def test_variant_reserved_pins(variant: str | None, pin: str, flagged: bool) -> None:
    data = base()
    data["components"]["U1"]["variant"] = variant
    use(pin)(data)
    assert ("flagged-pin" in codes(data)) is flagged


def test_flag_exempt_when_used_for_protected_function() -> None:
    data = base()
    assign(data, "usb_serial_jtag", "dm", "GPIO19", "USB_DM")
    assert "flagged-pin" not in codes(data)
    data = base()
    assign(data, "gpio", "io.led", "GPIO19", "LED")
    assert "flagged-pin" in codes(data)


def test_supply_conflict_names_each_rail_and_its_pins() -> None:
    data = base()
    data["nets"]["VCC"].append("U1.GND")
    [issue] = [
        i
        for i in validate(ProjectModel.model_validate(data), LIBRARY)
        if i.code == "supply-conflict"
    ]
    assert issue.where == "nets.VCC"
    assert issue.message == "net joins supply rails 3v3 (U1.3V3, U2.VDD) and gnd (U1.GND)"


def test_power_pins_without_a_rail_are_not_checked() -> None:
    data = base()
    data["components"]["U4"] = {"part": "local/imu", "board": "a"}  # VDD and GND have no rail
    data["nets"]["X"] = ["U4.VDD", "U4.GND"]
    assert "supply-conflict" not in codes(data)


def test_cross_board_net_is_allowed() -> None:
    data = base()
    data["components"]["U3"] = {"part": WROOM, "board": "b", "variant": "N8"}
    data["nets"]["LINK"] = ["U1.GPIO17", "U3.GPIO18"]
    assert codes(data) == set()


def test_validation_is_deterministic() -> None:
    data = base()
    CASES["pin-net-conflict"][1](data)
    CASES["unknown-pin"][1](data)
    library = LIBRARY
    model = ProjectModel.model_validate(data)
    assert validate(model, library) == validate(model, library)
