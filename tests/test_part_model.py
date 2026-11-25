from __future__ import annotations

import pytest
from pydantic import ValidationError

from puffin.library import PartLibrary
from puffin.library.models import PartPin, Peripheral


def test_list_form_means_unknown_direction() -> None:
    block = Peripheral.model_validate({"signals": ["tx", "rx"]})
    assert block.signals == {"tx": None, "rx": None}
    assert block.direction("tx") is None


def test_mapping_form_declares_directions() -> None:
    block = Peripheral.model_validate({"signals": {"sck": None, "cs": "out", "miso": "in"}})
    assert block.direction("cs.imu") == "out"
    assert block.direction("miso") == "in"
    assert block.direction("sck") is None
    assert block.direction("missing") is None


def test_invalid_direction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Peripheral.model_validate({"signals": {"tx": "output"}})


def test_esp32s3_uart_directions_and_spi_unknown() -> None:
    part = PartLibrary().get("espressif/esp32-s3-wroom-1")
    assert part is not None
    for uart in ("uart0", "uart1"):
        assert part.peripherals[uart].signals == {
            "tx": "out",
            "rx": "in",
            "rts": "out",
            "cts": "in",
        }
    assert set(part.peripherals["uart2"].signals.values()) == {None}
    assert set(part.peripherals["spi2"].signals.values()) == {None}


def test_rail_belongs_on_power_pins_only() -> None:
    assert PartPin(type="P", rail="3v3").rail == "3v3"
    with pytest.raises(ValidationError, match="only allowed on power pins"):
        PartPin(type="I", rail="gnd")
    with pytest.raises(ValidationError, match="must be lowercase"):
        PartPin(type="P", rail="GND")


def test_esp32s3_and_example_imu_rails() -> None:
    library = PartLibrary()
    wroom = library.get("espressif/esp32-s3-wroom-1")
    imu = library.get("example/spi-imu")
    assert wroom is not None and imu is not None
    assert (wroom.pins["GND"].rail, wroom.pins["3V3"].rail) == ("gnd", "3v3")
    assert (imu.pins["GND"].rail, imu.pins["VDD"].rail) == ("gnd", "3v3")
