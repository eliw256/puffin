from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from puffin.core import Project

PROJECT = Path(__file__).parent / "fixtures" / "dual-esp32s3-robot" / "puffin.yaml"
"""Frozen copy of the example project (IMU CS on GPIO10); the golden headers pair with it.

Tests never read examples/: the example is free to change. See test_fixture_isolation.py.
"""


@pytest.fixture
def example_path(tmp_path: Path) -> Path:
    """A temp copy of the whole fixture project (graph, schematic, netlist, sync state)."""
    root = tmp_path / "example"
    shutil.copytree(
        PROJECT.parent, root, ignore=shutil.ignore_patterns("fw", "*.lck", ".DS_Store", "*.rpt")
    )
    return root / "puffin.yaml"


@pytest.fixture
def project(example_path: Path) -> Project:
    return Project.load(example_path)
