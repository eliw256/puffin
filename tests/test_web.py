"""Web UI checks (M4): palette contrast, Svelte 5 guardrail, svelte-check, symbol geometry."""

from __future__ import annotations

import itertools
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
    assert len(pairs) >= 10
    failures = [
        f"{fg} on {bg}: {contrast(palette[fg], palette[bg]):.2f} < {minimum}"
        for fg, bg, minimum in pairs
        if contrast(palette[fg], palette[bg]) < minimum
    ]
    assert not failures, failures


def test_spec_palette_is_used_verbatim() -> None:
    palette = {k: v.lower() for k, v in tokens().items()}
    assert palette["--bg"] == "#282226" and palette["--surface"] == "#303545"
    assert palette["--blue"] == "#31447c" and palette["--warning"] == "#ec8f00"
    assert palette["--error"] == "#e72b00"


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


# --- dither swatches (spec §7) ---------------------------------------------------------------

DITHER = WEB / "src" / "lib" / "dither.json"


def dither() -> dict:  # type: ignore[type-arg]
    import json

    return json.loads(DITHER.read_text())  # type: ignore[no-any-return]


def mask_share(pattern: str) -> float:
    return sum(row.count("1") for row in dither()["patterns"][pattern]["mask"]) / 16


def linear(hex_color: str) -> list[float]:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]


def oklab_chroma(rgb: list[float]) -> float:
    r, g, b = rgb
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return float((a * a + bb * bb) ** 0.5)


def colour(name: str) -> str:
    data = dither()
    return str({**data["colors"], **data["warm"]}[name])


def lightness(swatch: dict) -> float:  # type: ignore[type-arg]
    s = 0.0 if swatch["b"] is None else mask_share(swatch["pattern"])
    y = luminance(colour(swatch["a"])) * (1 - s) + (
        luminance(colour(swatch["b"])) * s if swatch["b"] else 0
    )
    return float(116 * y ** (1 / 3) - 16 if y > 216 / 24389 else 24389 / 27 * y)


def test_dither_colours_cover_the_spec_palette_exactly() -> None:
    spec = (Path(__file__).parents[1] / "PUFFIN_SPEC.md").read_text().split("## 7.", 1)[1]
    spec_hex = {h.lower() for h in re.findall(r"`(#[0-9A-Fa-f]{6})`", spec.split("Rules:", 1)[0])}
    data = dither()
    used = {v.lower() for group in ("colors", "dark_aliases", "warm") for v in data[group].values()}
    assert used == spec_hex
    assert {v.lower() for v in data["warm"].values()} == {"#e72b00", "#ec8f00"}


def test_bayer_masks_follow_the_bayer_matrix() -> None:
    data = dither()
    matrix = data["bayer4"]
    assert sorted(v for row in matrix for v in row) == list(range(16))
    for name, ratio in (("bayer25", 0.25), ("bayer50", 0.5), ("bayer75", 0.75)):
        expected = [
            "".join("1" if matrix[y][x] < ratio * 16 else "0" for x in range(4)) for y in range(4)
        ]
        assert data["patterns"][name]["mask"] == expected, name


def test_no_family_swatch_is_solid_red_or_orange_or_too_warm() -> None:
    """Spec §7: warm only as a dithered mix, share <= 50%, with dark/blue/bone, muted."""
    data = dither()
    warm, rules = set(data["warm"]), data["warm_rules"]
    for family, swatch in data["families"].items():
        a, b, pattern = swatch["a"], swatch["b"], swatch["pattern"]
        assert a not in warm, f"{family}: warm colour as the base (solid share) is not allowed"
        if b is None:
            assert pattern == "solid"
            continue
        assert b in data["colors"] or b in warm, family
        if b in warm:
            assert mask_share(pattern) <= rules["max_share"], f"{family}: warm share over 50%"
            assert a in rules["partners"], f"{family}: warm must mix with dark, blue or bone"
            s = mask_share(pattern)
            mix = [
                x * (1 - s) + y * s
                for x, y in zip(linear(colour(a)), linear(colour(b)), strict=True)
            ]
            ratio = oklab_chroma(mix) / oklab_chroma(linear(colour(b)))
            assert ratio <= rules["max_chroma_ratio"], f"{family}: reads near-solid ({ratio:.2f})"


def test_every_family_has_a_distinct_pattern() -> None:
    families = dither()["families"]
    assert set(families) == {"gpio", "spi", "i2c", "uart", "adc", "usb", "power", "ground"}
    patterns = [s["pattern"] for s in families.values()]
    assert len(set(patterns)) == len(patterns), "each family gets its own pattern geometry"


def test_families_stay_apart_in_greyscale() -> None:
    """Greyscale keeps both shape and tone: two families may be close in lightness only if
    their pattern geometry differs (GPIO checker vs SPI v-lines is such a chosen pair)."""
    families = list(dither()["families"].items())
    for (f1, s1), (f2, s2) in itertools.combinations(families, 2):
        if s1["pattern"] == s2["pattern"]:
            assert abs(lightness(s1) - lightness(s2)) >= 5.5, f"{f1}/{f2}: same pattern, close tone"


def test_families_are_visible_on_the_background() -> None:
    background = 116 * luminance(dither()["colors"]["dark"]) ** (1 / 3) - 16
    for family, swatch in dither()["families"].items():
        assert lightness(swatch) - background >= 12, family
