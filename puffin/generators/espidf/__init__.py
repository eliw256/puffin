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

Cross-board links
    A net is listed when this board's MCU and another board's MCU both have a non-power pin
    on it. The arrow comes from part data: ``->`` when this side drives and the other only
    receives, ``<-`` for the reverse, and ``<->`` whenever either direction is unknown or
    it is not the case that exactly one side drives (``out`` and ``bidir`` count as driving).
"""

from __future__ import annotations

import re

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
    """Make user text safe inside a C block comment on one line."""
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


class EspIdfGenerator:
    framework = "esp-idf"

    def render(self, job: BoardJob) -> tuple[bytes, list[Issue]]:
        model = job.model
        issues: list[Issue] = []
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
        nets_by_pin = _nets_by_pin(model)
        names: dict[str, str] = {}

        def claim(name: str, where: str) -> bool:
            if name in names:
                issues.append(
                    gen_issue(
                        "name-collision",
                        where,
                        f"define {name} is also produced by {names[name]}",
                        "rename one of the signals so their names differ",
                    )
                )
                return False
            names[name] = where
            return True

        for peripheral, signals in model.assignments.get(job.mcu, {}).items():
            lines += ["", f"/* {_comment(peripheral)} */"]
            for signal, pin in signals.items():
                where = f"assignments.{job.mcu}.{peripheral}.{signal}"
                match = _GPIO.match(pin)
                if match is None:
                    issues.append(
                        gen_issue(
                            "unsupported-pin",
                            where,
                            f"{pin} is not a GPIO, so it has no GPIO_NUM value",
                            "assign the signal to a GPIO pin",
                        )
                    )
                    continue
                name = define_name(peripheral, signal)
                endpoint = f"{job.mcu}.{pin}"
                net = nets_by_pin[endpoint]
                notes = [f"net {net}"] + [
                    f"override {o.allow}: {o.note}" for o in model.overrides if o.pin == endpoint
                ]
                if claim(name, where):
                    comment = _comment("; ".join(notes))
                    lines.append(f"#define {name} GPIO_NUM_{int(match[1])} /* {comment} */")
                for index, constraint in enumerate(model.constraints):
                    if constraint.net == net and constraint.max_freq_hz is not None:
                        limit = f"{name}_MAX_HZ"
                        if claim(limit, f"constraints[{index}]"):
                            lines.append(
                                f"#define {limit} {constraint.max_freq_hz}"
                                f" /* constraint on net {_comment(net)} */"
                            )

        links = _links(job, nets_by_pin)
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

        for number, line in enumerate(lines, start=1):
            if not line.isascii():
                issues.append(
                    gen_issue(
                        "non-ascii",
                        f"boards.{job.board}",
                        f"line {number} of the header would contain non-ASCII text: {line!a}",
                        "use plain ASCII in puffin.yaml names, notes, and project name",
                    )
                )
        text = "\n".join(lines) + "\n"
        return text.encode("ascii", errors="replace"), issues


def _nets_by_pin(model: ProjectModel) -> dict[str, str]:
    """First net of each endpoint. Validation guarantees at most one."""
    found: dict[str, str] = {}
    for net, endpoints in model.nets.items():
        for endpoint in endpoints:
            found.setdefault(endpoint, net)
    return found


def _links(job: BoardJob, nets_by_pin: dict[str, str]) -> list[str]:
    model = job.model
    other_mcus = {
        board.firmware.mcu: name
        for name, board in model.boards.items()
        if board.firmware is not None and name != job.board and board.firmware.mcu != job.mcu
    }
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
