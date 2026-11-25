"""Structured validation results."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, order=True)
class Issue:
    """One finding: what is wrong, where, and the likely fix."""

    severity: Severity
    code: str
    where: str
    message: str
    hint: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.code, self.where, self.message)

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

    def __str__(self) -> str:
        text = f"{self.severity.value}[{self.code}] {self.where}: {self.message}"
        return f"{text}\n  fix: {self.hint}" if self.hint else text


def sort_issues(issues: Iterable[Issue]) -> list[Issue]:
    """Deduplicate and order issues: errors first, then by code, location, and message."""
    return sorted(
        set(issues), key=lambda i: (i.severity != Severity.ERROR, i.code, i.where, i.message)
    )
