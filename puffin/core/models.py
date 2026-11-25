"""Pydantic models for the project graph (`puffin.yaml`)."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PositiveInt, field_validator

SCHEMA_VERSION = "0.1"

_ENDPOINT = re.compile(r"^[^.\s]+\.[^.\s]+$")


def split_endpoint(endpoint: str) -> tuple[str, str]:
    """Split `U1.GPIO12` into (`U1`, `GPIO12`)."""
    component, pin = endpoint.split(".", 1)
    return component, pin


def _check_endpoint(value: str) -> str:
    if not _ENDPOINT.match(value):
        raise ValueError(
            f"'{value}' is not a pin reference; expected COMPONENT.PIN, e.g. U1.GPIO12"
        )
    return value


def _check_note(value: str) -> str:
    if not value.strip():
        raise ValueError("an override needs a note explaining why it is intentional")
    return value


Endpoint = Annotated[str, AfterValidator(_check_endpoint)]


class _GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Firmware(_GraphModel):
    framework: str
    target: str
    mcu: str
    output: str


class Board(_GraphModel):
    kicad_schematic: str | None = None
    firmware: Firmware | None = None


class Component(_GraphModel):
    part: str
    board: str
    variant: str | None = None


class Constraint(_GraphModel):
    net: str
    max_freq_hz: PositiveInt | None = None


class Override(_GraphModel):
    pin: Endpoint
    allow: str
    note: Annotated[str, AfterValidator(_check_note)]


class ProjectModel(_GraphModel):
    puffin: str
    project: str
    boards: dict[str, Board] = Field(default_factory=dict)
    components: dict[str, Component] = Field(default_factory=dict)
    nets: dict[str, list[Endpoint]] = Field(default_factory=dict)
    assignments: dict[str, dict[str, dict[str, str]]] = Field(default_factory=dict)
    constraints: list[Constraint] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    intent: dict[str, str] = Field(default_factory=dict)

    @field_validator("puffin", mode="before")
    @classmethod
    def _version_is_quoted(cls, value: object) -> object:
        # A bare 0.1 is a YAML float: 0.1 and 0.10 would be indistinguishable.
        if isinstance(value, int | float) and not isinstance(value, bool):
            raise ValueError(
                f'schema version must be a quoted string, e.g. puffin: "{SCHEMA_VERSION}"'
            )
        return value

    @field_validator("puffin")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f'unsupported schema version "{value}"; expected "{SCHEMA_VERSION}"')
        return value
