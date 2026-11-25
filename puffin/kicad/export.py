"""Runs `kicad-cli` to export a netlist. The only module here that starts a process."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAC_BUNDLE = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
TIMEOUT_S = 120


class KiCadCliError(Exception):
    """kicad-cli is missing or the export failed."""


def find_kicad_cli() -> Path | None:
    """`$PUFFIN_KICAD_CLI`, then `PATH`, then the macOS app bundle."""
    configured = os.environ.get("PUFFIN_KICAD_CLI")
    if configured:
        return Path(configured)
    found = shutil.which("kicad-cli")
    if found:
        return Path(found)
    return MAC_BUNDLE if MAC_BUNDLE.is_file() else None


def export_netlist(schematic: Path, executable: Path | None = None) -> str:
    exe = executable or find_kicad_cli()
    if exe is None:
        raise KiCadCliError(
            "kicad-cli not found; set PUFFIN_KICAD_CLI, add it to PATH, "
            "or pass --netlist with a pre-exported netlist"
        )
    if not schematic.is_file():
        raise KiCadCliError(f"schematic not found: {schematic}")
    with tempfile.TemporaryDirectory(prefix="puffin-") as tmp:
        output = Path(tmp) / "netlist.net"
        command = [str(exe), "sch", "export", "netlist", "--format", "kicadsexpr",
                   "--output", str(output), str(schematic)]  # fmt: skip
        try:
            # kicad-cli prints harmless Fontconfig warnings on stderr; only the status counts.
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise KiCadCliError(f"could not run {exe}: {exc}") from exc
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or [""]
            raise KiCadCliError(
                f"kicad-cli failed on {schematic} (exit {completed.returncode}) {detail[0]}"
            )
        return output.read_text(encoding="utf-8")
