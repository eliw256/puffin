from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from puffin.core import Project

EXAMPLE = Path(__file__).parents[1] / "examples" / "dual-esp32s3-robot" / "puffin.yaml"


@pytest.fixture
def example_path(tmp_path: Path) -> Path:
    target = tmp_path / "puffin.yaml"
    shutil.copy(EXAMPLE, target)
    return target


@pytest.fixture
def project(example_path: Path) -> Project:
    return Project.load(example_path)
