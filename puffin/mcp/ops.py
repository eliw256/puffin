"""Typed input schema for `propose_edit.ops`: one model per `Project.batch` operation.

The tool's JSON schema is a `oneOf` discriminated by `op`, so a model sees every operation and
its arguments. Each model dumps to exactly the dict `Project.batch` accepts.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_op(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class AddComponent(_Op):
    """Add a component on a board."""

    op: Literal["add_component"]
    ref: str
    part: str
    board: str
    variant: str | None = None


class SetComponent(_Op):
    """Create a component, or set its part, board and variant in place (variant null removes)."""

    op: Literal["set_component"]
    ref: str
    part: str
    board: str
    variant: str | None = None


class RemoveComponent(_Op):
    """Remove a component; rejected while anything still references it."""

    op: Literal["remove_component"]
    ref: str


class Connect(_Op):
    """Add pins (COMPONENT.PIN) to a net, creating it if needed."""

    op: Literal["connect"]
    net: str
    endpoints: list[str] = Field(min_length=1)


class Disconnect(_Op):
    """Remove pins from a net; an emptied net is deleted."""

    op: Literal["disconnect"]
    net: str
    endpoints: list[str] = Field(min_length=1)


class SetNet(_Op):
    """Create a net or replace its pins."""

    op: Literal["set_net"]
    name: str
    endpoints: list[str] = Field(min_length=1)


class RenameNet(_Op):
    """Rename a net; constraints and an intent entry follow the new name."""

    op: Literal["rename_net"]
    old: str
    new: str


class RemoveNet(_Op):
    """Delete a net."""

    op: Literal["remove_net"]
    name: str


class AssignPin(_Op):
    """Assign a peripheral signal (e.g. spi2 cs.imu) to an MCU pin."""

    op: Literal["assign_pin"]
    component: str
    peripheral: str
    signal: str
    pin: str


class UnassignPin(_Op):
    """Remove a signal assignment."""

    op: Literal["unassign_pin"]
    component: str
    peripheral: str
    signal: str


class SetAssignmentPin(_Op):
    """Point an existing assignment at another pin; nets are left alone."""

    op: Literal["set_assignment_pin"]
    component: str
    peripheral: str
    signal: str
    pin: str


class MovePin(_Op):
    """Move a pin's net connections and assignments to another pin, together."""

    op: Literal["move_pin"]
    component: str
    from_pin: str
    to_pin: str


class AddOverride(_Op):
    """Record an intentional use of a flagged pin; the note says why."""

    op: Literal["add_override"]
    pin: str
    allow: str
    note: str = Field(min_length=1)


class RemoveOverride(_Op):
    """Remove an override."""

    op: Literal["remove_override"]
    pin: str
    allow: str


EditOp = Annotated[
    AddComponent
    | SetComponent
    | RemoveComponent
    | Connect
    | Disconnect
    | SetNet
    | RenameNet
    | RemoveNet
    | AssignPin
    | UnassignPin
    | SetAssignmentPin
    | MovePin
    | AddOverride
    | RemoveOverride,
    Field(discriminator="op"),
]

MODELS: tuple[type[_Op], ...] = (
    AddComponent, SetComponent, RemoveComponent, Connect, Disconnect, SetNet, RenameNet,
    RemoveNet, AssignPin, UnassignPin, SetAssignmentPin, MovePin, AddOverride, RemoveOverride,
)  # fmt: skip
