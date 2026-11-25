"""Web UI checks (M4): palette contrast, Svelte 5 guardrail, svelte-check, symbol geometry."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from puffin.kicad.schematic import read_schematic, symbol_graphics
from tests.conftest import PROJECT

WEB = Path(__file__).parents[1] / "web"
THEME = WEB / "src" / "lib" / "theme.css"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed (web checks skipped)")
needs_modules = pytest.mark.skipif(
    NODE is None or not (WEB / "node_modules").is_dir(),
    reason="web/node_modules missing: run `npm install` in web/ (svelte-check skipped)",
)


# --- palette ------------------------------------------------------------------------------


def tokens() -> dict[str, str]:
    root = THEME.read_text().split(":root {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", root))


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def declared_pairs() -> list[tuple[str, str, float]]:
    block = THEME.read_text().split("CONTRAST (", 1)[1].split("*/", 1)[0]
    pattern = r"(--[a-z0-9-]+) on (--[a-z0-9-]+) ([\d.]+)"
    return [(fg, bg, float(ratio)) for fg, bg, ratio in re.findall(pattern, block)]


def test_every_declared_contrast_pair_meets_its_minimum() -> None:
    palette = tokens()
    pairs = declared_pairs()
    assert len(pairs) >= 15
    failures = [
        f"{fg} on {bg}: {contrast(palette[fg], palette[bg]):.2f} < {minimum}"
        for fg, bg, minimum in pairs
        if contrast(palette[fg], palette[bg]) < minimum
    ]
    assert not failures, failures


def test_every_family_colour_is_checked_against_ink() -> None:
    families = {name for name in tokens() if name.startswith("--fam-") and name != "--fam-unused"}
    checked = {bg for fg, bg, minimum in declared_pairs() if fg == "--ink" and minimum >= 4.5}
    assert families <= checked


def test_spec_palette_is_used_verbatim() -> None:
    palette = {k: v.lower() for k, v in tokens().items()}
    assert palette["--bg"] == "#282226" and palette["--surface"] == "#303545"
    assert palette["--blue"] == "#31447c" and palette["--warning"] == "#ec8f00"
    assert palette["--error"] == "#e72b00"


def test_no_family_colour_is_red_or_orange() -> None:
    """Red and orange are reserved for errors and warnings (spec §7)."""
    for name, value in tokens().items():
        if not name.startswith("--fam-"):
            continue
        r, g, b = (int(value[i : i + 2], 16) for i in (1, 3, 5))
        hue_is_warm = r > g + 40 and r > b + 40
        assert not hue_is_warm, f"{name} {value} reads as red/orange"


# --- Svelte 5 guardrail -------------------------------------------------------------------


@needs_node
def test_guardrail_catches_svelte4_syntax(tmp_path: Path) -> None:
    (tmp_path / "Old.svelte").write_text(
        "<script>\n  export let name;\n  $: upper = name.toUpperCase();\n</script>\n"
        "<button on:click={() => {}}>{upper}</button>\n"
    )
    result = subprocess.run(
        [NODE or "node", "scripts/check-svelte5.mjs", str(tmp_path)],
        cwd=WEB, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1
    for rule in ("export let", "$: reactive statement", "on: event directive"):
        assert rule in result.stderr, result.stderr


@needs_node
def test_web_sources_use_svelte5_syntax_only() -> None:
    result = subprocess.run(
        [NODE or "node", "scripts/check-svelte5.mjs"], cwd=WEB, capture_output=True, text=True,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr


@needs_modules
def test_svelte_check_passes_with_no_warnings() -> None:
    result = subprocess.run(
        ["npx", "--no-install", "svelte-check", "--tsconfig", "./tsconfig.json",
         "--fail-on-warnings", "--output", "machine"],
        cwd=WEB, capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"COMPLETED \d+ FILES 0 ERRORS 0 WARNINGS", result.stdout)


def test_runes_mode_is_forced() -> None:
    assert "runes: true" in (WEB / "svelte.config.js").read_text()


# --- drawable symbol ----------------------------------------------------------------------


def test_symbol_graphics_matches_placed_pins() -> None:
    sch = read_schematic((PROJECT.parent / "hw/robot/robot.kicad_sch").read_text())
    view = symbol_graphics(sch, "U1")
    inst = sch.instances["U1"]
    placed = {p.number: p for p in sch.pin_ends("U1")}
    for pin in view["pins"]:  # type: ignore[attr-defined]
        absolute = placed[pin["number"]].point
        assert pin["point"] == [float(absolute[0] - inst.at[0]), float(absolute[1] - inst.at[1])]
        assert pin["outward"] == placed[pin["number"]].outward
    io10 = next(p for p in view["pins"] if p["number"] == "18")  # type: ignore[attr-defined]
    assert io10["name"] == "IO10" and io10["body"] == [-12.7, 7.62]
    hidden = sorted(p["number"] for p in view["pins"] if p["hidden"])  # type: ignore[attr-defined]
    assert hidden == ["40", "41"]  # stacked GND pads
    assert view["shapes"][0] == {"kind": "rect", "x": -12.7, "y": -25.4, "w": 25.4, "h": 50.8}  # type: ignore[index]
    assert view["skipped"] == []
