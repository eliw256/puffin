"""MCP server (M3) through the SDK's in-memory client, plus one stdio smoke test."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from puffin.core import OPERATIONS
from puffin.mcp.ops import MODELS
from puffin.mcp.server import build_server
from tests.test_kicad_changes import SCK_U1, edited, node

pytestmark = pytest.mark.anyio

HEADER = "fw/main/main/include/puffin_pins.h"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(example_path: Path) -> AsyncIterator[Client]:
    async with Client(build_server(example_path)) as connected:
        yield connected


async def call(client: Client, tool: str, **arguments: Any) -> tuple[dict[str, Any], bool]:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None, result
    text = result.content[0].text  # type: ignore[union-attr]
    assert json.loads(text) == result.structured_content
    return result.structured_content, bool(result.is_error)


async def ok(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    data, is_error = await call(client, tool, **arguments)
    assert not is_error and data["ok"], data
    return data


async def error_code(client: Client, tool: str, **arguments: Any) -> str:
    data, is_error = await call(client, tool, **arguments)
    assert is_error and not data["ok"], data
    [issue] = data["errors"]
    assert issue["hint"] or issue["code"] in {"stale-proposal", "path-escape"}
    return str(issue["code"])


# --- surface --------------------------------------------------------------------------------

TOOLS = {
    "get_project", "query_pins", "query_net", "legal_pins", "get_part", "search_docs",
    "read_doc_section", "check", "propose_edit", "propose_import_kicad", "apply_edit", "generate",
}  # fmt: skip


async def test_tool_surface_and_annotations(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    writers = {n for n, t in tools.items() if not t.annotations.read_only_hint}  # type: ignore[union-attr]
    assert writers == {"apply_edit", "generate"}


async def test_propose_edit_schema_lists_every_operation(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    schema = json.dumps(tools["propose_edit"].input_schema)
    for name in OPERATIONS:
        assert f'"{name}"' in schema, name
    assert {m.model_fields["op"].annotation.__args__[0] for m in MODELS} == set(OPERATIONS)  # type: ignore[union-attr]


async def test_contract_in_instructions_prompt_and_resource(client: Client) -> None:
    assert client.instructions is not None
    assert 'say "the docs don\'t say"' in client.instructions
    prompt = await client.get_prompt("puffin-contract")
    assert "propose_edit" in prompt.messages[0].content.text  # type: ignore[union-attr]
    resource = await client.read_resource("puffin://contract")
    assert "contractor" in resource.contents[0].text  # type: ignore[union-attr]


async def test_resources(client: Client, example_path: Path) -> None:
    project = await client.read_resource("puffin://project")
    assert project.contents[0].text == example_path.read_text()  # type: ignore[union-attr]
    part = await client.read_resource("puffin://parts/espressif/esp32-s3-wroom-1")
    assert "id: espressif/esp32-s3-wroom-1" in part.contents[0].text  # type: ignore[union-attr]
    header = await client.read_resource("puffin://generated/main")
    assert "#define PUFFIN_SPI2_CS_IMU GPIO_NUM_10" in header.contents[0].text  # type: ignore[union-attr]


# --- read tools -----------------------------------------------------------------------------


async def test_get_project_and_board_filter(client: Client) -> None:
    data = await ok(client, "get_project", board="motor")
    assert data["puffin_api"] == 1 and set(data["components"]) == {"U2"}
    assert await error_code(client, "get_project", board="nope") == "not-found"


async def test_query_pins_shows_flags_net_assignment_override(client: Client) -> None:
    data = await ok(client, "query_pins", component="U1", pins=["GPIO0"])
    [pin] = data["pins"]
    assert pin["flags"] == ["strapping"] and pin["flags_source"] == {"strapping": "part"}
    assert pin["net"] == "BOOT_MAIN"
    assert pin["assignments"] == [{"peripheral": "gpio", "signal": "io.boot_button"}]
    assert pin["overrides"] == [{"allow": "strapping", "note": "boot button, intentional"}]
    used = await ok(client, "query_pins", component="U3", used_only=True)
    assert [p["pin"] for p in used["pins"]] == ["SCK", "SDI", "SDO", "CS", "VDD", "GND"]
    assert await error_code(client, "query_pins", component="U9") == "not-found"


async def test_query_net_has_constraint(client: Client) -> None:
    data = await ok(client, "query_net", net="IMU_SCK")
    assert [e["endpoint"] for e in data["endpoints"]] == ["U1.GPIO12", "U3.SCK"]
    assert data["constraints"] == [{"max_freq_hz": 10_000_000}]
    link = await ok(client, "query_net", net="LINK_MAIN_TX")
    assert link["cross_board"] and link["boards"] == ["main", "motor"]


async def test_legal_pins_for_spi2_cs(client: Client) -> None:
    data = await ok(client, "legal_pins", component="U1", peripheral="spi2", signal="cs.imu")
    status = {c["pin"]: c["status"] for c in data["candidates"]}
    assert data["current_pin"] == "GPIO10" and status["GPIO10"] == "current"
    assert status["GPIO4"] == "free"
    assert status["GPIO17"] == "in_use"  # uart1.tx
    assert status["GPIO3"] == "needs_override"  # strapping
    assert status["GPIO0"] == "in_use"
    gpio3 = next(c for c in data["candidates"] if c["pin"] == "GPIO3")
    assert gpio3["needs_override"] == ["strapping"]
    assert "EN" not in status and "3V3" not in status
    assert await error_code(client, "legal_pins", component="U1", peripheral="spi9",
                            signal="cs") == "not-found"  # fmt: skip


async def test_placeholder_imu_has_no_datasheet(client: Client) -> None:
    imu = await ok(client, "get_part", part="example/spi-imu")
    assert imu["has_datasheet"] is False and imu["doc_sections"] == []
    assert imu["sources"] == ["Placeholder: no datasheet"]
    wroom = await ok(client, "get_part", part="espressif/esp32-s3-wroom-1")
    assert wroom["has_datasheet"] is True
    assert "espressif/esp32-s3-wroom-1#strapping-pins-table-4-1" in wroom["doc_sections"]
    imu_docs = await ok(client, "search_docs", query="SPI clock", part="example/spi-imu")
    assert imu_docs["results"] == []


async def test_search_and_read_doc_section_with_citation(client: Client) -> None:
    found = await ok(client, "search_docs", query="strapping GPIO0")
    top = found["results"][0]
    assert top["section_id"] == "espressif/esp32-s3-wroom-1#strapping-pins-table-4-1"
    assert top["doc"] == "espressif/docs/esp32-s3-wroom-1.md" and "GPIO0" in top["snippet"]
    section = await ok(client, "read_doc_section", section_id=top["section_id"])
    assert section["citation"] == "espressif/esp32-s3-wroom-1 #strapping-pins-table-4-1"
    assert "GPIO0" in section["text"] and section["sources"]
    missing = await error_code(client, "read_doc_section", section_id="espressif/x#nope")
    assert missing == "not-found"


async def test_check_matches_cli(client: Client) -> None:
    data = await ok(client, "check")
    assert (data["errors"], data["warnings"]) == (0, 0)


# --- acceptance: propose -> apply -> generate ------------------------------------------------


async def test_move_imu_cs_end_to_end(client: Client, example_path: Path) -> None:
    root = example_path.parent
    assert (await ok(client, "generate"))["files"][0]["action"] == "create"
    before = (root / HEADER).read_text().splitlines()

    legal = await ok(client, "legal_pins", component="U1", peripheral="spi2", signal="cs.imu")
    target = next(c["pin"] for c in legal["candidates"] if c["status"] == "free")
    proposal = await ok(client, "propose_edit", ops=[
        {"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": target}
    ])  # fmt: skip
    assert proposal["proposal_id"].startswith("p_")
    assert f"+  IMU_CS: [U1.{target}, U3.CS]" in proposal["diff"]
    assert f"+#define PUFFIN_SPI2_CS_IMU GPIO_NUM_{target[4:]}" in proposal["generated"][0]["diff"]
    assert f"U1.{target}" not in example_path.read_text()  # proposing writes nothing
    [overwrite] = [w for w in proposal["warnings"] if w["code"] == "kicad-overwrite"]
    assert "KiCad-managed net IMU_CS" in overwrite["message"]

    applied = await ok(client, "apply_edit", proposal_id=proposal["proposal_id"])
    assert applied["applied"] and applied["changed_artifacts"] == ["puffin.yaml"]
    generated = await ok(client, "generate")
    assert [(f["action"], f["lines_added"], f["lines_removed"]) for f in generated["files"]
            if f["board"] == "main"] == [("update", 1, 1)]  # fmt: skip

    after = (root / HEADER).read_text().splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [(
        "#define PUFFIN_SPI2_CS_IMU GPIO_NUM_10 /* net IMU_CS */",
        f"#define PUFFIN_SPI2_CS_IMU GPIO_NUM_{target[4:]} /* net IMU_CS */",
    )]  # fmt: skip

    history = (root / ".puffin" / "history.jsonl").read_text().splitlines()
    [entry] = [json.loads(line) for line in history]
    assert entry["proposal_id"] == proposal["proposal_id"] and entry["kind"] == "edit"
    assert entry["sha256"] == hashlib.sha256(example_path.read_bytes()).hexdigest()
    assert entry["ops"] == [{"op": "move_pin", "component": "U1", "from_pin": "GPIO10",
                             "to_pin": target}]  # fmt: skip

    again = await error_code(client, "apply_edit", proposal_id=proposal["proposal_id"])
    assert again == "proposal-unknown"  # single use


async def test_stale_proposal_after_external_edit(client: Client, example_path: Path) -> None:
    proposal = await ok(client, "propose_edit", ops=[
        {"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO4"}
    ])  # fmt: skip
    example_path.write_text(example_path.read_text().replace("robot-mainboard", "renamed"))
    before = example_path.read_bytes()
    assert await error_code(client, "apply_edit", proposal_id=proposal["proposal_id"]) == (
        "stale-proposal"
    )
    assert example_path.read_bytes() == before
    # The server reloaded: every response reflects the current file.
    assert (await ok(client, "get_project"))["project"] == "renamed"


async def test_illegal_move_is_proposed_with_errors_and_cannot_be_applied(
    client: Client, example_path: Path
) -> None:
    before = example_path.read_bytes()
    data, is_error = await call(client, "propose_edit", ops=[
        {"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO3"}
    ])  # fmt: skip
    assert not is_error and not data["ok"]
    assert data["proposal_id"] is None
    assert {e["code"] for e in data["introduced_errors"]} == {"flagged-pin"}
    assert await error_code(client, "apply_edit", proposal_id="p_000000000000") == (
        "proposal-unknown"
    )
    assert example_path.read_bytes() == before


async def test_invalid_op_arguments_are_rejected_by_schema(client: Client) -> None:
    result = await client.call_tool("propose_edit", {"ops": [{"op": "explode"}]})
    assert result.is_error


async def test_proposals_are_deterministic(client: Client) -> None:
    ops = [{"op": "move_pin", "component": "U1", "from_pin": "GPIO10", "to_pin": "GPIO4"}]
    first = await ok(client, "propose_edit", ops=ops)
    assert await ok(client, "propose_edit", ops=ops) == first


# --- confinement ----------------------------------------------------------------------------


async def test_doc_path_escape_refused(client: Client) -> None:
    for section_id in ("../../etc/passwd#x", "espressif/esp32-s3-wroom-1#../x"):
        assert await error_code(client, "read_doc_section", section_id=section_id) == (
            "path-escape"
        )


async def test_part_symlink_escape_refused(
    client: Client, example_path: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("id: evil/part\nkind: x\npins: {}\n")
    (example_path.parent / "parts" / "evil").mkdir(parents=True)
    (example_path.parent / "parts" / "evil" / "part.yaml").symlink_to(outside)
    assert await error_code(client, "get_part", part="evil/part") == "path-escape"


async def test_output_path_escape_refused(client: Client, example_path: Path) -> None:
    example_path.write_text(
        example_path.read_text().replace("output: fw/main/main", "output: ../../escape")
    )
    data, _ = await call(client, "generate")
    assert not data["ok"]
    assert "output-path-escape" in {i["code"] for i in data["issues"]}


# --- imports and errors ---------------------------------------------------------------------


async def test_import_proposal_applies_and_records_sync(
    client: Client, example_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = edited((SCK_U1, node("U1", "22", "IO14")))
    monkeypatch.setattr("puffin.mcp.service.export_netlist", lambda _path: variant)
    proposal = await ok(client, "propose_import_kicad")
    assert not proposal["in_sync"]
    assert proposal["schematics"][0]["assignment_fixes"][0]["new_pin"] == "GPIO14"
    assert "kicad-overwrite" not in json.dumps(proposal)
    await ok(client, "apply_edit", proposal_id=proposal["proposal_id"])
    assert "IMU_SCK: [U1.GPIO14, U3.SCK]" in example_path.read_text()
    assert (await ok(client, "check"))["warnings"] == 0  # a real sync: no drift either side


async def test_import_proposal_goes_stale_when_schematic_changes(
    client: Client, example_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = (example_path.parent / "hw" / "robot" / "robot.net").read_text()
    monkeypatch.setattr("puffin.mcp.service.export_netlist", lambda _path: fixture)
    proposal = await ok(client, "propose_import_kicad")
    assert proposal["in_sync"]
    schematic = example_path.parent / "hw" / "robot" / "robot.kicad_sch"
    schematic.write_bytes(schematic.read_bytes() + b"\n")
    assert await error_code(client, "apply_edit", proposal_id=proposal["proposal_id"]) == (
        "stale-proposal"
    )


async def test_kicad_cli_failure_is_a_coded_error(
    client: Client, monkeypatch: pytest.MonkeyPatch, example_path: Path
) -> None:
    monkeypatch.setenv("PUFFIN_KICAD_CLI", str(example_path.parent / "no-such-kicad-cli"))
    assert await error_code(client, "propose_import_kicad") == "kicad-cli-failed"


async def test_generate_refused_while_graph_has_errors(client: Client, example_path: Path) -> None:
    example_path.write_text(example_path.read_text().replace("cs.imu: GPIO10", "cs.imu: GPIO9"))
    data, is_error = await call(client, "generate")
    assert not data["ok"] and not is_error
    assert "assignment-unconnected" in {e["code"] for e in data["errors"]}
    assert not (example_path.parent / HEADER).exists()


async def test_unreadable_project_is_a_coded_error(client: Client, example_path: Path) -> None:
    example_path.write_text("puffin: [")
    assert await error_code(client, "get_project") == "project-unreadable"


async def test_unexpected_exception_has_no_stack_trace(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("puffin.mcp.service.PuffinService.check", boom)
    data, is_error = await call(client, "check")
    assert is_error
    [issue] = data["errors"]
    assert issue["code"] == "internal-error" and issue["message"] == "RuntimeError: kaboom"
    assert "Traceback" not in json.dumps(data)


# --- stdio smoke test -----------------------------------------------------------------------

STRAY_PRINT_SERVER = """
import sys
from pathlib import Path
from puffin.mcp import server, service
original = service.PuffinService.get_project
def noisy(self, board=None):
    print("stray print from a tool")
    return original(self, board)
service.PuffinService.get_project = noisy
server.serve(Path(sys.argv[1]))
"""


async def test_stdio_stdout_carries_only_protocol(example_path: Path, tmp_path: Path) -> None:
    """Spawn the real server; tee its stdout so every byte on the wire can be inspected."""
    wire = tmp_path / "stdout.log"
    script = tmp_path / "noisy_server.py"
    script.write_text(STRAY_PRINT_SERVER)
    command = f"{sys.executable} -I {script} {example_path} | tee {wire}"
    params = StdioServerParameters(
        command="/bin/sh", args=["-c", command], env={"PYTHONPATH": str(Path.cwd())},
        cwd=str(tmp_path),
    )  # fmt: skip
    async with Client(params) as stdio_client:
        assert "puffin" in (stdio_client.server_info.name if stdio_client.server_info else "")
        tools = await stdio_client.list_tools()
        assert {t.name for t in tools.tools} == TOOLS
        data, is_error = await call(stdio_client, "get_project")
        assert not is_error and data["project"] == "robot-mainboard"
    lines = [line for line in wire.read_text().splitlines() if line.strip()]
    assert lines, "no protocol traffic captured"
    for line in lines:
        message = json.loads(line)  # a stray print would fail here
        assert message.get("jsonrpc") == "2.0"
    assert "stray print" not in wire.read_text()


def test_puffin_mcp_command_starts(example_path: Path) -> None:
    """`puffin mcp` with stdin closed starts, sees EOF, and exits without writing stdout."""
    completed = subprocess.run(
        [sys.executable, "-m", "puffin.cli.main", "mcp", str(example_path)],
        input=b"", capture_output=True, timeout=60, check=False,
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
    )  # fmt: skip
    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stdout == b""
