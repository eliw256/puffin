"""Checks the bundled ESP32-S3 module against facts in datasheet v1.8, Table 3-1."""

from __future__ import annotations

from puffin.library import PartLibrary


def part():  # type: ignore[no-untyped-def]
    loaded = PartLibrary().get("espressif/esp32-s3-wroom-1")
    assert loaded is not None
    return loaded


def test_all_41_module_pins_present_once() -> None:
    numbers = sorted(n for pin in part().pins.values() for n in pin.numbers)
    assert numbers == list(range(1, 42))


def test_gpio_set_matches_module() -> None:
    gpios = {int(name[4:]) for name in part().pins if name.startswith("GPIO")}
    assert gpios == {*range(0, 22), *range(35, 49)} - {22, 23, 24, 25} - {*range(26, 35)}
    assert len(gpios) == 36


def test_table_rows() -> None:
    pins = part().pins
    assert pins["GPIO12"].numbers == [20]
    assert "spi2.sck" in pins["GPIO12"].functions
    assert "adc2.ch1" in pins["GPIO12"].functions
    assert pins["GPIO43"].datasheet_name == "TXD0" and pins["GPIO43"].numbers == [37]
    assert pins["GPIO44"].datasheet_name == "RXD0" and pins["GPIO44"].numbers == [36]
    assert pins["GND"].numbers == [1, 40, 41]
    assert "usb_serial_jtag.dp" in pins["GPIO20"].functions
    assert "jtag.tms" in pins["GPIO42"].functions


def test_flags() -> None:
    p = part()
    assert {n for n, pin in p.pins.items() if "strapping" in pin.flags} == {
        "GPIO0",
        "GPIO3",
        "GPIO45",
        "GPIO46",
    }
    assert "octal_psram" in p.effective_flags("GPIO36", "N8R8")
    assert "octal_psram" not in p.effective_flags("GPIO36", "N8R2")
    assert "vdd_spi_1v8" in p.effective_flags("GPIO48", "N16R16VA7")
