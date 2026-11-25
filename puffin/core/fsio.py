"""Filesystem helpers shared by project saving and file generation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(target: Path, data: bytes) -> None:
    """Replace `target` with `data` in one step, keeping its permissions if it exists."""
    descriptor, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        if target.exists():
            os.chmod(temp, target.stat().st_mode)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
