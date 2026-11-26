"""The UI's HTTP API (M4): a thin layer over PuffinService, and every security check in §i."""

from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from puffin.mcp.service import PuffinService
from puffin.web import server
from puffin.web.server import create_app

PORT = 47123
TOKEN = "test-token-abc"
BASE = f"http://127.0.0.1:{PORT}"
AUTH = {"authorization": f"Bearer {TOKEN}"}
ORIGIN = {"origin": BASE}
MOVE = [{"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO4"}]


@pytest.fixture
def client(example_path: Path) -> TestClient:
    return TestClient(create_app(example_path, TOKEN, PORT), base_url=BASE)


def api(client: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    if body is None:
        return client.get(path, headers=AUTH)
    return client.post(path, json=body, headers={**AUTH, **ORIGIN})


# --- the API is the service --------------------------------------------------------------------


def test_responses_are_the_service_results(client: TestClient, example_path: Path) -> None:
    service = PuffinService(example_path)
    assert api(client, "/api/pins?component=U1").json() == service.query_pins("U1")
    assert api(client, "/api/legal?component=U1&peripheral=spi2&signal=cs.imu").json() == (
        service.legal_pins("U1", "spi2", "cs.imu")
    )
    assert api(client, "/api/blocks").json() == service.block_view()
    assert api(client, "/api/symbol?component=U1").json() == service.symbol_view("U1")


def test_tool_errors_keep_their_code_and_hint(client: TestClient) -> None:
    response = api(client, "/api/pins?component=U9")
    assert response.status_code == 400
    [issue] = response.json()["errors"]
    assert issue["code"] == "not-found" and issue["hint"]


def test_propose_apply_generate_and_undo(client: TestClient, example_path: Path) -> None:
    proposal = api(client, "/api/propose", {"ops": MOVE}).json()
    assert proposal["ok"] and proposal["proposal_id"]
    assert "kicad-overwrite" in {w["code"] for w in proposal["warnings"]}
    applied = api(client, "/api/apply", {"proposal_id": proposal["proposal_id"]}).json()
    assert applied["applied"]
    generated = api(client, "/api/generate", {}).json()
    assert generated["ok"] and generated["files"]
    assert "U1.GPIO4" in example_path.read_text()
    undo = api(client, "/api/propose-undo", {}).json()
    assert undo["ok"] and undo["undo_of"]["summary"] == ["move_pin(U1.GPIO10->GPIO4)"]
    history = api(client, "/api/history").json()
    assert history["entries"][0]["can_undo"]


def test_stale_proposal_after_a_concurrent_edit(client: TestClient, example_path: Path) -> None:
    proposal = api(client, "/api/propose", {"ops": MOVE}).json()
    other = PuffinService(example_path)  # e.g. Claude over MCP, in another process
    other.apply_edit(other.propose_edit([{"op": "rename_net", "old": "IMU_SCK",
                                          "new": "SPI_CLK"}])["proposal_id"])  # fmt: skip
    response = api(client, "/api/apply", {"proposal_id": proposal["proposal_id"]})
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "stale-proposal"
    assert api(client, "/api/fingerprint").json()["project"] != proposal["base_sha256"]


def test_diagnostics_and_writeback_endpoints(client: TestClient) -> None:
    diagnostics = api(client, "/api/diagnostics").json()
    assert diagnostics["ok"] and diagnostics["targets"] == []
    assert api(client, "/api/writeback").json()["pending"] == 0
    assert api(client, "/api/writeback/confirm", {}).json()["confirmed"] == 0


def test_bad_request_bodies_are_refused(client: TestClient) -> None:
    response = client.post("/api/propose", content=b"not json", headers={**AUTH, **ORIGIN})
    assert response.status_code == 400 and response.json()["errors"][0]["code"] == "not-found"


# --- security (§i) -----------------------------------------------------------------------------


def test_no_token_is_refused(client: TestClient) -> None:
    assert client.get("/api/project").status_code == 401


def test_wrong_token_is_refused(client: TestClient) -> None:
    response = client.get("/api/project", headers={"authorization": "Bearer nope"})
    assert response.status_code == 401


def test_token_in_the_query_string_is_not_accepted(client: TestClient) -> None:
    assert client.get(f"/api/project?token={TOKEN}").status_code == 401


def test_wrong_host_is_refused_against_dns_rebinding(client: TestClient) -> None:
    response = client.get("/api/project", headers={**AUTH, "host": f"evil.example:{PORT}"})
    assert response.status_code == 421
    assert client.get("/", headers={"host": "evil.example"}).status_code == 421


def test_localhost_host_is_accepted(client: TestClient) -> None:
    assert (
        client.get("/api/project", headers={**AUTH, "host": f"localhost:{PORT}"}).status_code == 200
    )


def test_foreign_origin_is_refused(client: TestClient) -> None:
    response = client.get("/api/project", headers={**AUTH, "origin": "https://evil.example"})
    assert response.status_code == 403


def test_cross_site_fetch_is_refused(client: TestClient) -> None:
    response = client.get("/api/project", headers={**AUTH, "sec-fetch-site": "cross-site"})
    assert response.status_code == 403


def test_post_without_origin_is_refused(client: TestClient) -> None:
    response = client.post("/api/propose", json={"ops": MOVE}, headers=AUTH)
    assert response.status_code == 403


def test_preflight_is_refused_and_no_cors_headers_ever(client: TestClient) -> None:
    preflight = client.options("/api/propose", headers={
        "origin": "https://evil.example", "access-control-request-method": "POST"})  # fmt: skip
    assert preflight.status_code == 405
    for response in (preflight, api(client, "/api/project"), client.get("/api/project")):
        assert not [h for h in response.headers if h.lower().startswith("access-control-")]


def test_security_headers_on_every_response(client: TestClient) -> None:
    for response in (api(client, "/api/project"), client.get("/api/project"), client.get("/")):
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "script-src 'self'" in csp and "unsafe-eval" not in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_serve_binds_loopback_only_and_puts_the_token_in_the_fragment(
    example_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bound: list[socket.socket] = []

    def fake_run(self: Any, sockets: list[socket.socket]) -> None:
        bound.extend(sockets)

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    server.serve(example_path, open_browser=False)
    [sock] = bound
    assert sock.getsockname()[0] == "127.0.0.1"
    sock.close()
    url = capsys.readouterr().out.split()[2]
    assert url.startswith("http://127.0.0.1:") and "/#token=" in url and "?" not in url


def test_token_never_reaches_the_logs(example_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = TestClient(create_app(example_path, TOKEN, PORT), base_url=BASE)
    api(client, "/api/project")
    api(client, "/api/pins?component=U9")  # an error path too
    client.get("/api/project", headers={"authorization": "Bearer wrong"})
    assert TOKEN not in caplog.text


# --- static page and live events ---------------------------------------------------------------


def test_index_needs_no_token_and_holds_no_project_data(
    client: TestClient, example_path: Path
) -> None:
    response = client.get("/")
    assert response.status_code in (200, 503)  # 503 only if the bundle is missing
    assert "robot-mainboard" not in response.text and "GPIO10" not in response.text


class FakeRequest:
    def __init__(self, polls: int) -> None:
        self.polls = polls

    async def is_disconnected(self) -> bool:
        self.polls -= 1
        return self.polls < 0


def test_events_report_a_settled_file_change(
    example_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "POLL_SECONDS", 0.01)
    service = PuffinService(example_path)

    async def collect() -> list[str]:
        out: list[str] = []
        stream = server._watch(service, FakeRequest(polls=8))  # type: ignore[arg-type]
        async for chunk in stream:
            out.append(chunk)
            if chunk.startswith("event: hello"):
                example_path.write_text(example_path.read_text() + "\n# edited elsewhere\n")
        return out

    chunks = asyncio.run(collect())
    assert chunks[0].startswith("event: hello")
    assert 'event: change\ndata: {"type": "project"}\n\n' in chunks


def test_events_report_schematic_and_checkpoint_changes(
    example_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "POLL_SECONDS", 0.01)
    service = PuffinService(example_path)
    schematic = example_path.parent / "hw/robot/robot.kicad_sch"

    async def collect() -> list[str]:
        out: list[str] = []
        async for chunk in server._watch(service, FakeRequest(polls=8)):  # type: ignore[arg-type]
            out.append(chunk)
            if chunk.startswith("event: hello"):
                schematic.write_bytes(schematic.read_bytes() + b"\n")
        return out

    assert '{"type": "schematic"}' in "".join(asyncio.run(collect()))


def test_licences_are_served(client: TestClient) -> None:
    response = client.get("/licenses/elkjs-LICENSE.md")
    assert response.status_code == 200 and "Eclipse Public License" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
