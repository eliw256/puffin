"""Loads part definitions from project-local and bundled part directories."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from puffin.library.models import PartDefinition

_PART_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*$")


class PartLoadError(Exception):
    """A part definition exists but cannot be used."""


def bundled_parts_dir() -> Path:
    package_root = Path(__file__).resolve().parents[1]
    installed = package_root / "_bundled_parts"
    return installed if installed.is_dir() else package_root.parent / "parts"


class PartLibrary:
    """Resolves part ids. Earlier roots take priority over later ones."""

    def __init__(self, roots: Sequence[Path] | None = None) -> None:
        self.roots = list(roots) if roots is not None else [bundled_parts_dir()]
        self._cache: dict[str, PartDefinition | None] = {}

    @classmethod
    def for_project(cls, project_dir: Path) -> PartLibrary:
        return cls([project_dir / "parts", bundled_parts_dir()])

    def get(self, part_id: str) -> PartDefinition | None:
        """Return the part, None if absent, or raise PartLoadError if malformed."""
        if part_id not in self._cache:
            self._cache[part_id] = self._load(part_id)
        return self._cache[part_id]

    def _load(self, part_id: str) -> PartDefinition | None:
        if not _PART_ID.match(part_id) or ".." in part_id:
            return None
        for root in self.roots:
            path = root / f"{part_id}.yaml"
            if path.is_file():
                return self._parse(part_id, path)
        return None

    @staticmethod
    def _parse(part_id: str, path: Path) -> PartDefinition:
        try:
            data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
            part = PartDefinition.model_validate(data)
        except (OSError, UnicodeDecodeError, YAMLError, ValidationError) as exc:
            raise PartLoadError(f"{path}: {exc}") from exc
        if part.id != part_id:
            raise PartLoadError(f"{path}: file declares id '{part.id}', expected '{part_id}'")
        return part
