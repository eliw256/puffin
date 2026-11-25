"""ESP-IDF pin header generator.

Output is one C header per board, plain ASCII with LF line endings.

Define names
    ``PUFFIN_<PERIPHERAL>_<SIGNAL>``, uppercased. A ``.`` in the signal and every other
    character outside ``A-Z0-9`` becomes ``_``. For the ``gpio`` peripheral the ``io`` base
    is dropped when a sub-name follows:

    ==================  ========================
    assignment          define
    ==================  ========================
    spi2.sck            PUFFIN_SPI2_SCK
    spi2.cs.imu         PUFFIN_SPI2_CS_IMU
    gpio.io.boot_button PUFFIN_GPIO_BOOT_BUTTON
    gpio.io             PUFFIN_GPIO_IO
    ==================  ========================

    A ``max_freq_hz`` constraint on the signal's net adds ``<define>_MAX_HZ``. Two
    assignments that map to the same name are an error, never a silent overwrite.

Order
    Peripherals and signals appear in ``puffin.yaml`` order. Nothing is column-aligned, so
    moving one pin changes exactly one line.

Checks
    ``preflight`` reports non-GPIO pins and define-name collisions as errors. ``render``
    assumes it found none. Identifiers are always ASCII; in comments each non-ASCII
    character becomes ``?`` and preflight warns, naming the field.

Cross-board links
    A net is listed when this board's MCU and another board's MCU both have a non-power pin
    on it. The arrow comes from part data: ``->`` when this side drives and the other only
    receives, ``<-`` for the reverse, and ``<->`` whenever either direction is unknown or
    it is not the case that exactly one side drives (``out`` and ``bidir`` count as driving).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from puffin.core.issues import Issue
from puffin.core.models import ProjectModel, split_endpoint
from puffin.generators.types import MARKER, BoardJob, gen_issue
from puffin.library import PartDefinition
from puffin.library.models import Direction

_GPIO = re.compile(r"^GPIO(\d+)$")
_NOT_IDENT = re.compile(r"[^A-Z0-9]")
_SPACE = re.compile(r"\s+")


def define_name(peripheral: str, signal: str) -> str:
    base, _, sub = signal.partition(".")
    parts = (
        [peripheral, sub] if peripheral == "gpio" and base == "io" and sub else [peripheral, signal]
    )
    return "PUFFIN_" + "_".join(_NOT_IDENT.sub("_", part.upper()) for part in parts)


def _comment(text: str) -> str:
    """Make user text safe inside a one-line C block comment, in plain ASCII."""
    text = "".join(ch if ch.isascii() else "?" for ch in text)
    return _SPACE.sub(" ", text).strip().replace("*/", "* /")


def _arrow(local: Direction | None, remote: Direction | None) -> str:
    if local is None or remote is None:
        return "<->"
    local_drives = local in ("out", "bidir")
    remote_drives = remote in ("out", "bidir")
    if local_drives and not remote_drives:
        return "->"
    if remote_drives and not local_drives:
        return "<-"
    return "<->"


@dataclass(frozen=True)
class _Define:
    name: str
    where: str
    pin: str
    value: str | None  # None: the pin has no GPIO_NUM value
    comment: str


def _defines(job: BoardJob) -> Iterator[tuple[str, list[_Define]]]:
    """Defines per peripheral, in file order. Skips pins validation reports as unknown."""
    model = job.model
    nets_by_pin = _nets_by_pin(model)
    for peripheral, signals in model.assignments.get(job.mcu, {}).items():
        defines: list[_Define] = []
        for signal, pin in signals.items():
            if pin not in job.part.pins:
                continue
            where = f"assignments.{job.mcu}.{peripheral}.{signal}"
            name = define_name(peripheral, signal)
            endpoint = f"{job.mcu}.{pin}"
            net = nets_by_pin.get(endpoint)
            notes = [f"net {net}"] + [
                f"override {o.allow}: {o.note}" for o in model.overrides if o.pin == endpoint
            ]
            match = _GPIO.match(pin)
            value = f"GPIO_NUM_{int(match[1])}" if match else None
            defines.append(_Define(name, where, pin, value, "; ".join(notes)))
            for index, constraint in enumerate(model.constraints):
                if net is not None and constraint.net == net and constraint.max_freq_hz:
                    defines.append(
                        _Define(
                            f"{name}_MAX_HZ",
                            f"constraints[{index}]",
                            pin,
                            str(constraint.max_freq_hz),
                            f"constraint on net {net}",
                        )
                    )
        yield peripheral, defines


class EspIdfGenerator:
    framework = "esp-idf"

    def preflight(self, job: BoardJob) -> list[Issue]:
        issues: list[Issue] = []
        owners: dict[str, str] = {}
        for _, defines in _defines(job):
            for define in defines:
                if define.value is None:
                    issues.append(
                        gen_issue(
                            "unsupported-pin",
                            define.where,
                            f"{define.pin} is not a GPIO, so it has no GPIO_NUM value",
                            "assign the signal to a GPIO pin",
                        )
                    )
                elif define.name in owners:
                    issues.append(
                        gen_issue(
                            "name-collision",
                            define.where,
                            f"define {define.name} is also produced by {owners[define.name]}",
                            "rename one of the signals so their names differ",
                        )
                    )
                else:
                    owners[define.name] = define.where
        for where, text in _header_fields(job):
            if not text.isascii():
                issues.append(
                    gen_issue(
                        "non-ascii",
                        where,
                        f"contains non-ASCII text {text!a}; each non-ASCII character is"
                        " written as '?' in the header",
                        "use plain ASCII in this field to keep the text as written",
                    )
                )
        return issues

    def render(self, job: BoardJob) -> bytes:
        model = job.model
        variant = model.components[job.mcu].variant
        mcu_detail = job.part.id + (f", variant {variant}" if variant else "")
        lines = [
            MARKER,
            "/*",
            f" * project: {_comment(model.project)}",
            f" * board: {_comment(job.board)}",
            f" * mcu: {_comment(job.mcu)} ({_comment(mcu_detail)})",
            " */",
            "",
            "#pragma once",
            "",
            '#include "hal/gpio_types.h"',
        ]
        for peripheral, defines in _defines(job):
            lines += ["", f"/* {_comment(peripheral)} */"]
            lines += [f"#define {d.name} {d.value} /* {_comment(d.comment)} */" for d in defines]
        links = _links(job)
        if links:
            lines += [
                "",
                "/*",
                " * Cross-board links (nets shared with another board's MCU).",
                " * Arrows point from the driving signal to the receiving one;"
                " <-> if neither drives.",
                *(f" * {link}" for link in links),
                " */",
            ]
        return ("\n".join(lines) + "\n").encode("ascii")


def _nets_by_pin(model: ProjectModel) -> dict[str, str]:
    """First net of each endpoint. Validation guarantees at most one."""
    found: dict[str, str] = {}
    for net, endpoints in model.nets.items():
        for endpoint in endpoints:
            found.setdefault(endpoint, net)
    return found


def _other_mcus(job: BoardJob) -> dict[str, str]:
    """MCU reference -> board name, for every other board with firmware."""
    return {
        board.firmware.mcu: name
        for name, board in job.model.boards.items()
        if board.firmware is not None and name != job.board and board.firmware.mcu != job.mcu
    }


def _header_fields(job: BoardJob) -> Iterator[tuple[str, str]]:
    """User-written text that can reach this board's header, with its location."""
    model = job.model
    yield "project", model.project
    yield f"boards.{job.board}", job.board
    yield f"components.{job.mcu}", job.mcu
    variant = model.components[job.mcu].variant
    if variant is not None:
        yield f"components.{job.mcu}.variant", variant
    mcus = {job.mcu: job.board, **_other_mcus(job)}
    for ref, board in mcus.items():
        yield f"boards.{board}", board
        yield f"components.{ref}", ref
        for peripheral, signals in model.assignments.get(ref, {}).items():
            yield f"assignments.{ref}.{peripheral}", peripheral
            for signal in signals:
                yield f"assignments.{ref}.{peripheral}.{signal}", signal
    for net, endpoints in model.nets.items():
        if any(split_endpoint(e)[0] in mcus for e in endpoints):
            yield f"nets.{net}", net
    for index, override in enumerate(model.overrides):
        if split_endpoint(override.pin)[0] == job.mcu:
            yield f"overrides[{index}].allow", override.allow
            yield f"overrides[{index}].note", override.note


def _links(job: BoardJob) -> list[str]:
    model = job.model
    other_mcus = _other_mcus(job)
    signal_of = {
        f"{ref}.{pin}": (peripheral, signal)
        for ref, peripherals in model.assignments.items()
        for peripheral, signals in peripherals.items()
        for signal, pin in signals.items()
    }
    parts: dict[str, PartDefinition | None] = {job.mcu: job.part}
    for ref in other_mcus:
        parts[ref] = job.library.get(model.components[ref].part)

    def signal_pin(endpoint: str) -> bool:
        ref, pin = split_endpoint(endpoint)
        part = parts.get(ref)
        definition = part.pins.get(pin) if part else None
        return definition is not None and not definition.is_power

    def describe(endpoint: str) -> tuple[str, Direction | None]:
        assigned = signal_of.get(endpoint)
        if assigned is None:
            return endpoint, None
        peripheral, signal = assigned
        part = parts.get(split_endpoint(endpoint)[0])
        block = part.peripherals.get(peripheral) if part else None
        direction = block.direction(signal) if block else None
        return f"{endpoint} ({peripheral}.{signal})", direction

    lines: list[str] = []
    for net, endpoints in model.nets.items():
        local = [e for e in endpoints if split_endpoint(e)[0] == job.mcu and signal_pin(e)]
        remote = [e for e in endpoints if split_endpoint(e)[0] in other_mcus and signal_pin(e)]
        for mine in local:
            mine_text, mine_dir = describe(mine)
            for theirs in remote:
                theirs_text, theirs_dir = describe(theirs)
                board = other_mcus[split_endpoint(theirs)[0]]
                arrow = _arrow(mine_dir, theirs_dir)
                lines.append(_comment(f"{net}: {mine_text} {arrow} {board} {theirs_text}"))
    return lines
