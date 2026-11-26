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


# --- pin descriptions (datasheet Table 3-1, verbatim) -----------------------------------------


def test_every_wroom_pin_has_a_cited_verbatim_description() -> None:
    from puffin.library import PartLibrary

    part = PartLibrary().get("espressif/esp32-s3-wroom-1")
    assert part is not None
    assert all(pin.description for pin in part.pins.values())
    assert part.pin_description_cite is not None
    assert part.pin_description_cite.table == "Table 3-1"
    assert part.pins["EN"].description == (
        "High: on, enables the chip. Low: off, the chip powers off. "
        "Note: Do not leave the EN pin floating."
    )
    assert part.pins["GPIO10"].description == (
        "RTC_GPIO10, GPIO10, TOUCH10, ADC1_CH9, FSPICS0, FSPIIO4, SUBSPICS0"
    )
    assert part.pins["3V3"].description == "Power supply"
    assert "Octal SPI PSRAM" in (part.pins["GPIO35"].description or "")
    assert "1.8 V" in (part.pins["GPIO47"].description or "")


def test_placeholder_imu_has_no_descriptions() -> None:
    from puffin.library import PartLibrary

    imu = PartLibrary().get("example/spi-imu")
    assert imu is not None
    assert all(pin.description is None for pin in imu.pins.values())
    assert imu.pin_description_cite is None


def test_descriptions_without_a_source_are_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    from puffin.library.models import PartDefinition

    with pytest.raises(ValidationError, match="pin_description_cite"):
        PartDefinition.model_validate(
            {"id": "x/y", "kind": "mcu", "pins": {"A": {"description": "made up"}}}
        )


def test_each_pin_description_cites_its_own_table_row() -> None:
    from puffin.library import PartLibrary

    part = PartLibrary().get("espressif/esp32-s3-wroom-1")
    assert part is not None
    doc = "ESP32-S3-WROOM-1 DS v1.8"
    assert part.description_cite("EN") == {"document": doc, "location": "Table 3-1, pin 3 (EN)"}
    assert part.description_cite("GND") == {
        "document": doc,
        "location": "Table 3-1, pins 1, 40, 41 (GND)",
    }
    assert part.description_cite("GPIO10") == {
        "document": doc,
        "location": "Table 3-1, pin 18 (IO10)",
    }
    # the variant notes appended to a description are cited with it (b: Octal PSRAM, c: 1.8 V)
    assert part.description_cite("GPIO35")["location"].endswith("(IO35), note b")  # type: ignore[index]
    assert part.description_cite("GPIO47")["location"].endswith("(IO47), note c")  # type: ignore[index]
    noted = {n for n, p in part.pins.items() if p.description_note}
    assert noted == {"GPIO35", "GPIO36", "GPIO37", "GPIO47", "GPIO48"}


def test_a_description_note_needs_a_description() -> None:
    import pytest
    from pydantic import ValidationError

    from puffin.library.models import PartDefinition

    with pytest.raises(ValidationError, match="description_note without a description"):
        PartDefinition.model_validate(
            {"id": "x/y", "kind": "mcu", "pins": {"A": {"description_note": "note b"}}}
        )
