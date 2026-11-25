from __future__ import annotations

from pathlib import Path

import pytest

from puffin.core import Project, ProjectLoadError, yamlio
from tests.conftest import PROJECT


def test_example_is_clean() -> None:
    assert Project.load(PROJECT).check() == []


def test_save_is_byte_identical(example_path: Path) -> None:
    original = example_path.read_bytes()
    project = Project.load(example_path)
    assert project.save() is False
    assert example_path.read_bytes() == original


def test_serializer_itself_round_trips_example() -> None:
    original = PROJECT.read_bytes()
    assert yamlio.dump(yamlio.parse(original)) == original


def test_unedited_noncanonical_formatting_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "puffin.yaml"
    source = b"puffin: '0.1'\nproject:   spaced   # odd spacing\nnets:\n  A:    []\n"
    path.write_bytes(source)
    project = Project.load(path)
    project.save()
    assert path.read_bytes() == source


@pytest.mark.parametrize(
    "source",
    [
        b"puffin: [unclosed",
        b"- a\n- list\n",
        b"puffin: '0.2'\nproject: x\n",
        b"puffin: '0.10'\nproject: x\n",
        b"puffin: '0.1'\nproject: x\ntypo_section: {}\n",
        b"puffin: '0.1'\nproject: x\nnets:\n  A: [not-an-endpoint]\n",
        b"puffin: '0.1'\nproject: x\nproject: y\n",
        b"puffin: '0.1'\nproject: x\n"
        b"overrides:\n  - {pin: U1.GPIO0, allow: strapping, note: ' '}\n",
    ],
)
def test_bad_files_raise_load_error(tmp_path: Path, source: bytes) -> None:
    path = tmp_path / "puffin.yaml"
    path.write_bytes(source)
    with pytest.raises(ProjectLoadError):
        Project.load(path)


@pytest.mark.parametrize("bare", ["0.1", "0.10", "1"])
def test_bare_number_version_asks_for_quotes(tmp_path: Path, bare: str) -> None:
    path = tmp_path / "puffin.yaml"
    path.write_text(f"puffin: {bare}\nproject: x\n")
    with pytest.raises(ProjectLoadError, match=r'must be a quoted string, e\.g\. puffin: "0\.1"'):
        Project.load(path)


def test_quoted_version_round_trips(tmp_path: Path) -> None:
    for quote in ('"', "'"):
        path = tmp_path / "puffin.yaml"
        source = f"puffin: {quote}0.1{quote}\nproject: x\n".encode()
        path.write_bytes(source)
        project = Project.load(path)
        assert project.model.puffin == "0.1"
        project.save()
        assert path.read_bytes() == source


def test_missing_file_raises_load_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectLoadError):
        Project.load(tmp_path)
