"""End-to-end browser tests (M4): the real server and the built UI, driven by Playwright.

Skipped, with the reason, when Playwright's Chromium isn't installed
(`uv run playwright install chromium`). The write-back round trip also needs kicad-cli.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from puffin.kicad.export import find_kicad_cli
from puffin.mcp.service import PuffinService
from tests.conftest import PROJECT

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")


def _chromium_installed() -> bool:
    try:
        with playwright.sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_installed(),
    reason="Chromium for Playwright missing: uv run playwright install chromium",
)
needs_kicad = pytest.mark.skipif(find_kicad_cli() is None, reason="kicad-cli not installed")

TOKEN = "e2e-token"
HEADER = "fw/main/main/include/puffin_pins.h"


@dataclass
class UI:
    url: str
    root: Path
    path: Path


@pytest.fixture(scope="module")
def browser() -> Iterator[playwright.Browser]:
    """One Chromium per module, fully shut down afterwards: Playwright's sync API runs an
    event loop on this thread, which must not outlive these tests (async tests follow)."""
    with playwright.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser: playwright.Browser) -> Iterator[playwright.Page]:
    context = browser.new_context()
    yield context.new_page()
    context.close()


@pytest.fixture
def ui(example_path: Path) -> Iterator[UI]:
    import uvicorn

    from puffin.web.server import create_app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(example_path, TOKEN, port), log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield UI(f"http://127.0.0.1:{port}/#token={TOKEN}", example_path.parent, example_path)
    server.should_exit = True
    thread.join(timeout=5)


def open_ui(page: playwright.Page, ui: UI) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(ui.url)
    page.wait_for_selector('g[data-pin="GPIO10"] .tag-bg')
    page.wait_for_selector(".live.on")


def drag(page: playwright.Page, source: str, target: str) -> None:
    tag = page.locator(f'g[data-pin="{source}"] .tag-bg').bounding_box()
    line = page.locator(
        f'g[data-pin="{target}"] .idle, g[data-pin="{target}"] .stub'
    ).first.bounding_box()
    assert tag and line
    x, y = tag["x"] + tag["width"] / 2, tag["y"] + tag["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(400)  # pointer down selects and fetches legal pins
    page.mouse.move(x - 20, y - 20, steps=3)
    page.mouse.move(line["x"] + 1, line["y"] + line["height"] / 2, steps=6)
    page.mouse.up()


def test_drag_review_apply_puffin_only_updates_header(page: playwright.Page, ui: UI) -> None:
    open_ui(page, ui)
    drag(page, "GPIO10", "GPIO4")
    review = page.locator('aside[aria-label="Review proposal"]')
    playwright.expect(review.locator("h2")).to_have_text(
        "Move U1.GPIO10 → GPIO4 (net IMU_CS; spi2.cs.imu)"
    )
    apply = review.get_by_role("button", name="Apply")
    playwright.expect(apply).to_be_disabled()  # the KiCad choice is required first
    review.get_by_text("Puffin only").click()
    review.get_by_role("button", name="Apply to puffin.yaml").click()
    playwright.expect(review).to_be_hidden()
    playwright.expect(page.locator(".toast")).to_contain_text("Regenerated")
    assert "IMU_CS: [U1.GPIO4, U3.CS]" in ui.path.read_text()
    assert "PUFFIN_SPI2_CS_IMU GPIO_NUM_4" in (ui.root / HEADER).read_text()
    playwright.expect(page.locator('g[data-pin="GPIO4"] .tag-text')).to_have_text("spi2 cs.imu")


def test_illegal_drop_is_refused_with_the_reason(page: playwright.Page, ui: UI) -> None:
    open_ui(page, ui)
    before = ui.path.read_bytes()
    drag(page, "GPIO10", "EN")
    playwright.expect(page.locator(".toast.refused")).to_contain_text("EN can't carry spi2.cs")
    playwright.expect(page.locator('aside[aria-label="Review proposal"]')).to_have_count(0)
    assert ui.path.read_bytes() == before


def test_external_edit_appears_live(page: playwright.Page, ui: UI) -> None:
    open_ui(page, ui)
    other = PuffinService(ui.path)  # an editor, the CLI, or Claude over MCP
    other.apply_edit(other.propose_edit([{"op": "rename_net", "old": "IMU_CS",
                                          "new": "CS_EXTERNAL"}])["proposal_id"])  # fmt: skip
    playwright.expect(page.locator('g[data-pin="GPIO10"] .net')).to_have_text(
        "CS_EXTERNAL", timeout=8000
    )


def test_stale_proposal_after_a_concurrent_mcp_edit(page: playwright.Page, ui: UI) -> None:
    open_ui(page, ui)
    drag(page, "GPIO10", "GPIO4")
    review = page.locator('aside[aria-label="Review proposal"]')
    playwright.expect(review.get_by_text("Puffin only")).to_be_visible()
    mcp = PuffinService(ui.path)  # Claude over MCP, in another process
    mcp.apply_edit(mcp.propose_edit([{"op": "rename_net", "old": "IMU_SCK",
                                      "new": "SPI_CLK"}])["proposal_id"])  # fmt: skip
    playwright.expect(review.locator(".stale")).to_contain_text("no longer applies", timeout=8000)
    playwright.expect(review.get_by_role("button", name="Apply")).to_be_disabled()
    review.get_by_role("button", name="Propose again").click()
    playwright.expect(review.locator(".stale")).to_have_count(0)
    review.get_by_text("Puffin only").click()
    review.get_by_role("button", name="Apply to puffin.yaml").click()
    playwright.expect(review).to_be_hidden()
    text = ui.path.read_text()
    assert "SPI_CLK: [U1.GPIO12, U3.SCK]" in text and "IMU_CS: [U1.GPIO4, U3.CS]" in text


@needs_kicad
def test_acceptance_round_trip_with_kicad(page: playwright.Page, ui: UI) -> None:
    """M4 acceptance: move IMU CS with write-back, confirm, then reverse it in 'KiCad'
    (the schematic file is replaced as a KiCad save would), see drift live, import, clean."""
    open_ui(page, ui)
    schematic = ui.root / "hw/robot/robot.kicad_sch"
    drag(page, "GPIO10", "GPIO4")
    review = page.locator('aside[aria-label="Review proposal"]')
    review.get_by_text("Puffin + schematic").click()
    playwright.expect(review.locator(".verified")).to_be_visible(timeout=30000)
    review.get_by_role("button", name="Apply to puffin.yaml + schematic").click()
    playwright.expect(review).to_be_hidden(timeout=30000)
    assert "(at 48.26 85.09 180)" in schematic.read_text()  # the label moved to GPIO4
    assert "PUFFIN_SPI2_CS_IMU GPIO_NUM_4" in (ui.root / HEADER).read_text()

    page.get_by_role("button", name="Issues").click()
    page.get_by_role("button", name="Confirm", exact=True).click()
    playwright.expect(page.locator(".checks")).to_have_text("0 errors · 0 warnings", timeout=8000)

    shutil.copy(PROJECT.parent / "hw/robot/robot.kicad_sch", schematic)  # moved back in KiCad
    drift = page.locator(".drift")
    playwright.expect(drift).to_contain_text("schematic changed since last import", timeout=8000)
    drift.get_by_role("button", name="Import from KiCad…").click()
    importer = page.locator('aside[aria-label="Review KiCad import"]')
    playwright.expect(importer).to_contain_text("U1 spi2.cs.imu: GPIO4 → GPIO10", timeout=30000)
    importer.get_by_role("button", name="Apply import").click()
    playwright.expect(importer).to_be_hidden(timeout=30000)
    playwright.expect(page.locator(".checks")).to_have_text("0 errors · 0 warnings", timeout=8000)
    playwright.expect(drift).to_have_count(0)
    assert "PUFFIN_SPI2_CS_IMU GPIO_NUM_10" in (ui.root / HEADER).read_text()
    assert PuffinService(ui.path).check()["warnings"] == 0


SPI_BUS = "SPI2 ×4: IMU_CS, IMU_MISO, IMU_MOSI, IMU_SCK"  # noqa: RUF001 (the UI's real label)


def test_block_diagram_loads_elkjs_on_demand(page: playwright.Page, ui: UI) -> None:
    loaded: list[str] = []
    page.on("request", lambda request: loaded.append(request.url))
    open_ui(page, ui)
    assert not [u for u in loaded if "/assets/elkjs-" in u], "elkjs must not load with the pin map"
    page.get_by_role("button", name="Block diagram").click()
    playwright.expect(page.locator(".node")).to_have_count(3, timeout=10000)
    playwright.expect(page.get_by_role("button", name=SPI_BUS)).to_be_visible()
    assert [u for u in loaded if "/assets/elkjs-" in u], "elkjs should load when the view opens"
    page.get_by_role("button", name=SPI_BUS).click()
    page.get_by_role("button", name="Pin map").click()
    playwright.expect(page.locator(".highlight")).to_have_count(4)  # the bus's pins are outlined
