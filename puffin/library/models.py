"""Pydantic models for part definitions (`parts/<vendor>/<part>.yaml`)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _PartModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Supply(_PartModel):
    nominal_v: float
    min_v: float | None = None
    max_v: float | None = None


_RAIL = re.compile(r"^[a-z0-9][a-z0-9_.+-]*$")


class PartPin(_PartModel):
    """One pin. `rail` names the supply a power pin (`type: P`) belongs to.

    Rails are short lowercase names compared as plain strings: `gnd` for ground, and the
    nominal voltage for a supply (`"3v3"`, `"1v8"`, `"5v"`). Two power pins with different
    rails on one net is a `supply-conflict` error; power pins without a rail are not checked.
    """

    numbers: list[int] = Field(default_factory=list)
    datasheet_name: str | None = None
    description: str | None = None  # verbatim from the datasheet; see pin_description_cite
    description_note: str | None = None  # a table note appended to the description, e.g. "note b"
    type: str | None = None
    rail: str | None = None
    functions: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rail_on_power_pins_only(self) -> PartPin:
        if self.rail is not None:
            if self.type != "P":
                raise ValueError("rail is only allowed on power pins (type: P)")
            if not _RAIL.match(self.rail):
                raise ValueError(f"rail '{self.rail}' must be lowercase, e.g. gnd or 3v3")
        return self

    @property
    def is_power(self) -> bool:
        return self.type == "P"


Direction = Literal["out", "in", "bidir"]


class Peripheral(_PartModel):
    """A peripheral's signals and how they reach pins.

    `signals` maps each signal to its direction as seen from this part: `out` (drives),
    `in` (receives), `bidir`, or null when unknown. A plain list means every direction is
    unknown, so `[tx, rx]` and `{tx: null, rx: null}` are equivalent.
    """

    signals: dict[str, Direction | None]
    routable: Literal["gpio_matrix", "fixed"] = "fixed"

    @field_validator("signals", mode="before")
    @classmethod
    def _accept_list_form(cls, value: Any) -> Any:
        return dict.fromkeys(value) if isinstance(value, list) else value

    def direction(self, signal: str) -> Direction | None:
        """Direction of a signal; a sub-name such as `cs.imu` uses its base `cs`."""
        return self.signals.get(signal.split(".", 1)[0])


class Variant(_PartModel):
    description: str | None = None
    extra_flags: dict[str, list[str]] = Field(default_factory=dict)


class FlagPolicy(_PartModel):
    description: str | None = None
    exempt_peripherals: list[str] = Field(default_factory=list)


class PartDocs(_PartModel):
    datasheet: str | None = None
    source_url: str | None = None


class DescriptionCite(BaseModel):
    """The source of a part's pin descriptions: a short document name with its version
    (e.g. "ESP32-S3-WROOM-1 DS v1.8") and the table they were copied from ("Table 3-1")."""

    model_config = ConfigDict(extra="forbid")

    document: str
    table: str


class PartDefinition(_PartModel):
    id: str
    kind: str
    supply: Supply | None = None
    pins: dict[str, PartPin]
    peripherals: dict[str, Peripheral] = Field(default_factory=dict)
    variants: dict[str, Variant] = Field(default_factory=dict)
    flag_policy: dict[str, FlagPolicy] = Field(default_factory=dict)
    docs: PartDocs = Field(default_factory=PartDocs)
    sources: list[str] = Field(default_factory=list)
    pin_description_cite: DescriptionCite | None = None  # where pin descriptions were copied from
    timing: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _descriptions_are_cited(self) -> PartDefinition:
        if any(pin.description for pin in self.pins.values()) and not self.pin_description_cite:
            raise ValueError("pin descriptions need pin_description_cite citing the datasheet")
        noted = [
            name for name, pin in self.pins.items() if pin.description_note and not pin.description
        ]
        if noted:
            raise ValueError(f"description_note without a description on {', '.join(noted)}")
        return self

    def description_cite(self, pin: str) -> dict[str, str] | None:
        """Where this pin's description comes from: the document, and the table row by pin
        number and datasheet name (plus any appended note), e.g. "Table 3-1, pin 3 (EN)"."""
        definition = self.pins.get(pin)
        if not definition or not definition.description or not self.pin_description_cite:
            return None
        numbers = ", ".join(str(n) for n in definition.numbers)
        label = "pins" if len(definition.numbers) > 1 else "pin"
        row = f"{label} {numbers} ({definition.datasheet_name or pin})"
        location = f"{self.pin_description_cite.table}, {row}"
        if definition.description_note:
            location += f", {definition.description_note}"
        return {"document": self.pin_description_cite.document, "location": location}

    def effective_flags(self, pin: str, variant: str | None) -> frozenset[str]:
        """Flags on `pin`. With no variant, assume every variant's restrictions."""
        definition = self.pins.get(pin)
        flags = set(definition.flags) if definition else set()
        if variant is None:
            for candidate in self.variants.values():
                flags.update(candidate.extra_flags.get(pin, []))
        elif variant in self.variants:
            flags.update(self.variants[variant].extra_flags.get(pin, []))
        return frozenset(flags)

    def legal_pins(self, peripheral: str, signal: str) -> list[str]:
        """Pins that can carry `peripheral.signal`, in part-file order."""
        block = self.peripherals.get(peripheral)
        if block is None:
            return []
        token = f"{peripheral}.{signal}"
        return [
            name
            for name, pin in self.pins.items()
            if token in pin.functions
            or (block.routable == "gpio_matrix" and "gpio" in pin.functions)
        ]
