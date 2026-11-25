from __future__ import annotations

import pytest
from pydantic import ValidationError

from puffin.library import PartLibrary
from puffin.library.models import Peripheral


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
