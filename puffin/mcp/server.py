"""MCP server for one Puffin project (docs/M3_MCP.md). stdio only; stdout is the protocol."""

from __future__ import annotations

import io
import json
import logging
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from puffin import __version__
from puffin.core import Issue
from puffin.generators import generate as render_all
from puffin.mcp.contract import CONTRACT
from puffin.mcp.docs import PathEscape, inside
from puffin.mcp.ops import EditOp
from puffin.mcp.service import MCP_CODES, PuffinService, ToolError

log = logging.getLogger("puffin.mcp")

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)


def tool_result(call: Callable[[], dict[str, Any]]) -> CallToolResult:
    """Run a tool body; a ToolError or unexpected exception becomes an isError result."""
    is_error = False
    try:
        data = call()
    except ToolError as exc:
        data, is_error = {"ok": False, "errors": [exc.issue.to_dict()]}, True
    except Exception as exc:  # never leak a stack trace into the result
        log.error("internal error\n%s", traceback.format_exc())
        issue = Issue(
            MCP_CODES["internal-error"],
            "internal-error",
            "server",
            f"{type(exc).__name__}: {exc}",
            "details are in the server's stderr log",
        )
        data, is_error = {"ok": False, "errors": [issue.to_dict()]}, True
    text = json.dumps(data, indent=2)
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structured_content=data, is_error=is_error
    )


def build_server(path: Path) -> MCPServer:
    service = PuffinService(path)
    server = MCPServer(
        name="puffin",
        title="Puffin",
        description="Project graph, checks, generators and docs for one Puffin project",
        instructions=CONTRACT,
        version=__version__,
    )
    server.service = service  # type: ignore[attr-defined]  # tests reach in for state

    # --- read tools ---------------------------------------------------------------------

    @server.tool(annotations=READ)
    def get_project(
        board: Annotated[str | None, Field(description="limit to one board")] = None,
    ) -> CallToolResult:
        """The whole project graph (or one board's slice): boards, components, nets,
        assignments, constraints, overrides and intent."""
        return tool_result(lambda: service.get_project(board))

    @server.tool(annotations=READ)
    def query_pins(
        component: Annotated[str, Field(description="reference designator, e.g. U1")],
        pins: Annotated[list[str] | None, Field(description="only these pins")] = None,
        used_only: Annotated[bool, Field(description="only pins on a net or assigned")] = False,
    ) -> CallToolResult:
        """Every pin of a component with its functions, flags (variant applied), net,
        assignments, overrides and current issues."""
        return tool_result(lambda: service.query_pins(component, pins, used_only))

    @server.tool(annotations=READ)
    def query_net(net: Annotated[str, Field(description="net name")]) -> CallToolResult:
        """A net's pins (with board, type, rail and assignments), constraints and issues."""
        return tool_result(lambda: service.query_net(net))

    @server.tool(annotations=READ)
    def legal_pins(
        component: Annotated[str, Field(description="MCU reference designator, e.g. U1")],
        peripheral: Annotated[str, Field(description="peripheral, e.g. spi2")],
        signal: Annotated[str, Field(description="signal, e.g. cs or cs.imu")],
    ) -> CallToolResult:
        """Which pins can carry a peripheral signal, each with a status: current, free,
        connected (on a net), in_use (another signal assigned) or needs_override (flagged).
        Use this instead of reasoning over pin tables."""
        return tool_result(lambda: service.legal_pins(component, peripheral, signal))

    @server.tool(annotations=READ)
    def get_part(
        part: Annotated[str, Field(description="part id, e.g. espressif/esp32-s3-wroom-1")],
    ) -> CallToolResult:
        """A part definition, whether it has a datasheet, its sources and doc section ids."""
        return tool_result(lambda: service.get_part(part))

    @server.tool(annotations=READ)
    def search_docs(
        query: Annotated[str, Field(description="keywords")],
        part: Annotated[str | None, Field(description="limit to one part id")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 8,
    ) -> CallToolResult:
        """Keyword search over parts' extracted datasheet docs. Returns section ids to read
        and cite."""
        return tool_result(lambda: service.search_docs(query, part, limit))

    @server.tool(annotations=READ)
    def read_doc_section(
        section_id: Annotated[str, Field(description="e.g. espressif/esp32-s3-wroom-1#pins")],
    ) -> CallToolResult:
        """One doc section's text, its sources, and the citation to quote."""
        return tool_result(lambda: service.read_doc_section(section_id))

    @server.tool(annotations=READ)
    def check() -> CallToolResult:
        """Validation, generator checks and KiCad drift: everything `puffin check` reports."""
        return tool_result(service.check)

    # --- propose / apply ----------------------------------------------------------------

    @server.tool(annotations=READ)
    def propose_edit(
        ops: Annotated[list[EditOp], Field(min_length=1, description="applied in order")],
    ) -> CallToolResult:
        """Dry-run graph edits: returns a proposal id, the puffin.yaml diff, the generated
        header diffs and validation. Writes nothing. Show the diff before apply_edit."""
        return tool_result(lambda: service.propose_edit([op.to_op() for op in ops]))

    @server.tool(annotations=READ)
    def propose_import_kicad() -> CallToolResult:
        """Export each KiCad schematic with kicad-cli and propose the changes that bring
        puffin.yaml in line. Writes nothing; apply with apply_edit."""
        return tool_result(service.propose_import_kicad)

    @server.tool(annotations=WRITE)
    def apply_edit(
        proposal_id: Annotated[
            str, Field(description="id from propose_edit or propose_import_kicad")
        ],
    ) -> CallToolResult:
        """Apply a proposal the engineer approved. Refused with stale-proposal if puffin.yaml
        changed since; propose again then."""
        return tool_result(lambda: service.apply_edit(proposal_id))  # fmt: skip

    @server.tool(annotations=WRITE)
    def generate(
        board: Annotated[str | None, Field(description="one board only")] = None,
        check_only: Annotated[bool, Field(description="report, write nothing")] = False,
    ) -> CallToolResult:
        """Regenerate firmware pin headers from the graph (refused while it has errors)."""
        return tool_result(lambda: service.generate(board, check_only))

    # --- resources ----------------------------------------------------------------------

    @server.resource("puffin://project", mime_type="application/yaml")
    def project_file() -> str:
        """puffin.yaml as it is on disk."""
        return service.path.read_text(encoding="utf-8")

    @server.resource("puffin://contract", mime_type="text/markdown")
    def contract() -> str:
        """The AI behavior contract for working in Puffin."""
        return CONTRACT

    @server.resource("puffin://parts/{vendor}/{name}", mime_type="application/yaml")
    def part_file(vendor: str, name: str) -> str:
        """A part definition YAML."""
        return _library_file(service, f"{vendor}/{name}.yaml")

    @server.resource("puffin://docs/{vendor}/{name}", mime_type="text/markdown")
    def doc_file(vendor: str, name: str) -> str:
        """An extracted datasheet doc."""
        return _library_file(service, f"{vendor}/docs/{name}.md")

    @server.resource("puffin://generated/{board}", mime_type="text/x-c")
    def generated(board: str) -> str:
        """A board's header as `generate` would write it (read-only; never edit it)."""
        project = service.project()
        result = render_all(project.model, project.library, board)
        if not result.ok:
            raise ValueError("; ".join(str(i) for i in result.issues))
        return result.files[0].content.decode()

    # --- prompts ------------------------------------------------------------------------

    @server.prompt(name="puffin-contract", title="Puffin contract")
    def puffin_contract() -> str:
        """The rules for working in a Puffin project."""
        return CONTRACT

    @server.prompt(name="review-kicad-import", title="Review a KiCad import")
    def review_kicad_import() -> str:
        """Propose a KiCad import and walk the engineer through it."""
        return (
            CONTRACT
            + "\nTask: call propose_import_kicad. Present the change set section by section: "
            "components, nets, endpoint changes, proposed assignment fixes, ambiguous fixes, "
            "warnings, and errors the import would introduce. Ambiguous fixes and errors are "
            "the engineer's to resolve. Call apply_edit only when they say so."
        )

    return server


def _library_file(service: PuffinService, relative: str) -> str:
    if ".." in relative.split("/"):
        raise PathEscape(relative)
    for root in service.project().library.roots:
        candidate = root / relative
        if candidate.is_file():
            if not inside(candidate, root):
                raise PathEscape(relative)
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(relative)


def serve(path: Path) -> None:
    """Run over stdio; nothing but the protocol may reach stdout.

    While serving, the SDK writes the protocol to a private duplicate of fd 1 and points fd 1
    itself at stderr. That alone is not enough: `sys.stdout` is block-buffered on a pipe, so a
    stray `print` would sit in its buffer and be flushed at exit, after fd 1 is restored to
    the wire. Making `sys.stdout` unbuffered sends every such write to fd 1 immediately, while
    it still points at stderr.
    """
    logging.basicConfig(level=logging.WARNING, format="puffin-mcp %(levelname)s %(message)s")
    sys.stdout.flush()
    sys.stdout = io.TextIOWrapper(
        io.FileIO(1, "w", closefd=False), encoding="utf-8", write_through=True
    )
    build_server(path).run("stdio")
