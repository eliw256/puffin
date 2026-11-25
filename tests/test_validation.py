"""Every rule has one passing and one failing case. A new rule without cases fails the suite."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from puffin.core import RULES, ProjectModel, validate
from puffin.library import PartLibrary

Data = dict[str, Any]
Mutator = Callable[[Data], None]
WROOM = "espressif/esp32-s3-wroom-1"


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
    return {i.code for i in validate(ProjectModel.model_validate(data), PartLibrary())}


def noop(_: Data) -> None:
    return None


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


def set_invalid_part(data: Data) -> None:
    data["components"]["U2"]["part"] = "bad/part"


CASES: dict[str, tuple[Mutator, Mutator]] = {
    "unknown-part": (noop, lambda d: d["components"]["U2"].update(part="nope/none")),
    "unknown-board": (
        lambda d: d["components"]["U2"].update(board="b"),
        lambda d: d["components"]["U2"].update(board="zzz"),
    ),
    "unknown-variant": (
        lambda d: d["components"]["U1"].update(variant="N8"),
        lambda d: d["components"]["U1"].update(variant="N99"),
    ),
    "unknown-component": (noop, lambda d: d["nets"]["SCK"].append("U9.GPIO1")),
    "unknown-pin": (noop, lambda d: d["nets"]["SCK"].append("U1.GPIO99")),
    "unknown-net": (
        lambda d: d.update(constraints=[{"net": "SCK", "max_freq_hz": 1}]),
        lambda d: d.update(constraints=[{"net": "NOPE", "max_freq_hz": 1}]),
    ),
    "pin-net-conflict": (noop, lambda d: d["nets"]["CS"].append("U1.GPIO12")),
    "unknown-peripheral": (noop, lambda d: assign(d, "spi9", "sck", "GPIO4", "X")),
    "unknown-signal": (
        lambda d: assign(d, "spi2", "mosi", "GPIO4", "X"),
        lambda d: assign(d, "spi2", "bogus", "GPIO4", "X"),
    ),
    "pin-function": (
        lambda d: assign(d, "adc1", "ch3", "GPIO4", "X"),
        lambda d: assign(d, "adc1", "ch3", "GPIO5", "X"),
    ),
    "pin-double-assigned": (noop, lambda d: assign(d, "spi2", "mosi", "GPIO12")),
    "flagged-pin": (chain(use("GPIO0"), override("U1.GPIO0", "strapping")), use("GPIO0")),
    "assignment-unconnected": (noop, lambda d: d["nets"].update(SCK=["U1.GPIO14", "U2.SCK"])),
    "signals-shorted": (link_two_mcus, short_sck_and_cs),
    "signal-on-power-net": (
        lambda d: assign(d, "spi2", "mosi", "GPIO4", "X"),
        lambda d: (assign(d, "spi2", "mosi", "GPIO4"), d["nets"]["VCC"].append("U1.GPIO4")),
    ),
    "firmware-mcu-board": (noop, lambda d: d["components"]["U1"].update(board="b")),
    "unused-override": (
        chain(use("GPIO0"), override("U1.GPIO0", "strapping")),
        override("U1.GPIO4", "strapping"),
    ),
    "invalid-part": (noop, set_invalid_part),
}


def test_every_rule_has_cases() -> None:
    assert set(CASES) == set(RULES)


@pytest.mark.parametrize("code", sorted(set(CASES) - {"invalid-part"}))
def test_rule_passes(code: str) -> None:
    data = base()
    CASES[code][0](data)
    assert code not in codes(data)


@pytest.mark.parametrize("code", sorted(set(CASES) - {"invalid-part"}))
def test_rule_fails(code: str) -> None:
    data = base()
    CASES[code][1](data)
    assert code in codes(data)


def test_invalid_part_rule(tmp_path: Any) -> None:
    data = base()
    assert "invalid-part" not in codes(data)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "part.yaml").write_text("id: bad/part\nunexpected: 1\n")
    set_invalid_part(data)
    found = validate(ProjectModel.model_validate(data), PartLibrary([tmp_path]))
    assert "invalid-part" in {i.code for i in found}


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


def test_cross_board_net_is_allowed() -> None:
    data = base()
    data["components"]["U3"] = {"part": WROOM, "board": "b", "variant": "N8"}
    data["nets"]["LINK"] = ["U1.GPIO17", "U3.GPIO18"]
    assert codes(data) == set()


def test_validation_is_deterministic() -> None:
    data = base()
    CASES["pin-net-conflict"][1](data)
    CASES["unknown-pin"][1](data)
    library = PartLibrary()
    model = ProjectModel.model_validate(data)
    assert validate(model, library) == validate(model, library)
