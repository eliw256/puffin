"""Local web UI server (docs/M4_UI.md): a thin HTTP layer over PuffinService.

Every /api route calls one service method and returns its dict unchanged. Security (§i):
127.0.0.1 only, a per-run bearer token, Host and Origin checks, no CORS, strict headers.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import socket
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from puffin.core import Issue
from puffin.kicad import checkpoint
from puffin.kicad.state import STATE_PATH, schematic_files
from puffin.mcp.service import MCP_CODES, PuffinService, ToolError

log = logging.getLogger("puffin.web")

STATIC = Path(__file__).parent / "static"
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0

SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


class Guard:
    """ASGI middleware: Host, Origin, Sec-Fetch-Site and token checks; security headers."""

    def __init__(self, app: ASGIApp, port: int, token: str) -> None:
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        method = scope["method"]
        path = scope["path"]
        refusal = self.refuse(method, path, headers)
        if refusal is not None:
            status, message = refusal
            response = JSONResponse({"ok": False, "error": message}, status_code=status)
            await self.secured(response)(scope, receive, send)
            return

        async def send_secured(message: Any) -> None:
            if message["type"] == "http.response.start":
                existing = [(k, v) for k, v in message.get("headers", [])
                            if k.decode("latin-1").lower() not in SECURITY_HEADERS]  # fmt: skip
                message["headers"] = existing + [
                    (k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()
                ]
            await send(message)

        await self.app(scope, receive, send_secured)

    def refuse(self, method: str, path: str, headers: dict[str, str]) -> tuple[int, str] | None:
        if headers.get("host") not in self.hosts:
            return 421, "unexpected Host header (DNS rebinding protection)"
        if method == "OPTIONS":
            return 405, "no CORS"
        if headers.get("sec-fetch-site") == "cross-site":
            return 403, "cross-site request refused"
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            return 403, "foreign Origin refused"
        if method not in ("GET", "HEAD") and origin is None:
            return 403, "state-changing requests must carry Origin"
        if path.startswith("/api/"):
            supplied = headers.get("authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                return 401, "missing or wrong token"
        return None

    @staticmethod
    def secured(response: Response) -> Response:
        for key, value in SECURITY_HEADERS.items():
            response.headers[key] = value
        return response


def _error(issue: Issue, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "errors": [issue.to_dict()]}, status_code=status)


def create_app(project: Path, token: str, port: int) -> ASGIApp:
    service = PuffinService(project)
    lock = threading.Lock()

    def call(method: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
        with lock:  # PuffinService is written for one caller at a time
            return method(*args, **kwargs)

    async def run(method: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> Response:
        try:
            return JSONResponse(await run_in_threadpool(call, method, *args, **kwargs))
        except ToolError as exc:
            return _error(exc.issue, 400)
        except Exception as exc:  # never a stack trace in a response
            log.error("internal error\n%s", traceback.format_exc())
            message = f"{type(exc).__name__}: {exc}"
            hint = "details are in the server log"
            issue = Issue(MCP_CODES["internal-error"], "internal-error", "server", message, hint)
            return _error(issue, 500)

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def q(request: Request, name: str) -> str | None:
        return request.query_params.get(name)

    async def project_view(request: Request) -> Response:
        return await run(service.get_project, q(request, "board"))

    async def pins(request: Request) -> Response:
        return await run(service.query_pins, q(request, "component") or "")

    async def symbol(request: Request) -> Response:
        return await run(service.symbol_view, q(request, "component") or "")

    async def legal(request: Request) -> Response:
        return await run(service.legal_pins, q(request, "component") or "",
                         q(request, "peripheral") or "", q(request, "signal") or "")  # fmt: skip

    async def part(request: Request) -> Response:
        return await run(service.get_part, q(request, "part") or "")

    async def docs_search(request: Request) -> Response:
        limit = int(q(request, "limit") or 8)
        return await run(service.search_docs, q(request, "q") or "", q(request, "part"), limit)

    async def docs_section(request: Request) -> Response:
        return await run(service.read_doc_section, q(request, "id") or "")

    async def diagnostics(_: Request) -> Response:
        return await run(service.diagnostics)

    async def blocks(_: Request) -> Response:
        return await run(service.block_view)

    async def fingerprint(_: Request) -> Response:
        return await run(service.fingerprint)

    async def history(request: Request) -> Response:
        return await run(service.history, int(q(request, "limit") or 10))

    async def writeback_status(_: Request) -> Response:
        return await run(service.writeback_status)

    async def propose(request: Request) -> Response:
        data = await body(request)
        ops = data.get("ops")
        if not isinstance(ops, list) or not ops:
            return _error(Issue(MCP_CODES["not-found"], "not-found", "ops", "no ops given",
                                "send {\"ops\": [...]}"), 400)  # fmt: skip
        return await run(service.propose_edit, ops, bool(data.get("writeback")))

    async def propose_import(_: Request) -> Response:
        return await run(service.propose_import_kicad)

    async def propose_undo(request: Request) -> Response:
        return await run(service.propose_undo, bool((await body(request)).get("writeback")))

    async def apply(request: Request) -> Response:
        return await run(service.apply_edit, str((await body(request)).get("proposal_id", "")))

    async def generate(request: Request) -> Response:
        data = await body(request)
        return await run(service.generate, data.get("board"), bool(data.get("check_only")))

    async def confirm(_: Request) -> Response:
        return await run(service.writeback_confirm)

    async def restore(_: Request) -> Response:
        return await run(service.writeback_restore)

    async def events(request: Request) -> Response:
        return StreamingResponse(_watch(service, request), media_type="text/event-stream")

    async def index(_: Request) -> Response:
        page = STATIC / "index.html"
        if not page.is_file():
            return Response("Puffin UI bundle missing: run `npm run build` in web/.", 503)
        return FileResponse(page)

    routes: list[Route | Mount] = [
        Route("/api/project", project_view),
        Route("/api/pins", pins),
        Route("/api/symbol", symbol),
        Route("/api/legal", legal),
        Route("/api/part", part),
        Route("/api/docs/search", docs_search),
        Route("/api/docs/section", docs_section),
        Route("/api/diagnostics", diagnostics),
        Route("/api/blocks", blocks),
        Route("/api/fingerprint", fingerprint),
        Route("/api/history", history),
        Route("/api/writeback", writeback_status),
        Route("/api/propose", propose, methods=["POST"]),
        Route("/api/propose-import", propose_import, methods=["POST"]),
        Route("/api/propose-undo", propose_undo, methods=["POST"]),
        Route("/api/apply", apply, methods=["POST"]),
        Route("/api/generate", generate, methods=["POST"]),
        Route("/api/writeback/confirm", confirm, methods=["POST"]),
        Route("/api/writeback/restore", restore, methods=["POST"]),
        Route("/api/events", events),
        Route("/", index),
    ]
    if (STATIC / "assets").is_dir():
        routes.append(Mount("/assets", app=StaticFiles(directory=STATIC / "assets")))
    return Guard(Starlette(routes=routes), port=port, token=token)


def _watched(service: PuffinService) -> dict[str, list[Path]]:
    root = service.root
    manifest = root / checkpoint.BACKUP_DIR / checkpoint.MANIFEST
    files: dict[str, list[Path]] = {
        "project": [service.path],
        "state": [root / STATE_PATH],
        "checkpoint": [manifest],
    }
    try:
        model = service.project().model
        schematic_paths = [
            root / b.kicad_schematic for b in model.boards.values() if b.kicad_schematic
        ]
    except ToolError:
        schematic_paths = []
    files["schematic"] = [f for s in schematic_paths for f in (schematic_files(s) or [s])]
    return files


def _stat(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    out = []
    for path in paths:
        try:
            st = path.stat()
            out.append((str(path), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(path), -1, -1))
    return tuple(out)


async def _watch(service: PuffinService, request: Request) -> Any:
    """Poll the project's files; report a change once it has been stable for one poll."""
    # Snapshot before saying hello: a change made right after the client connects must count.
    last = {kind: _stat(paths) for kind, paths in _watched(service).items()}
    yield "event: hello\ndata: {}\n\n"
    pending: dict[str, tuple[Any, ...]] = {}
    idle = 0.0
    while not await request.is_disconnected():
        await asyncio.sleep(POLL_SECONDS)
        idle += POLL_SECONDS
        current = {kind: _stat(paths) for kind, paths in _watched(service).items()}
        for kind, snapshot in current.items():
            if snapshot == last.get(kind):
                pending.pop(kind, None)
                continue
            if pending.get(kind) == snapshot:  # unchanged for one poll: settled
                last[kind] = snapshot
                pending.pop(kind, None)
                idle = 0.0
                yield f"event: change\ndata: {json.dumps({'type': kind})}\n\n"
            else:
                pending[kind] = snapshot
        if idle >= HEARTBEAT_SECONDS:
            idle = 0.0
            yield ": heartbeat\n\n"


def serve(project: Path, port: int | None = None, open_browser: bool = True) -> None:
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port or 0))  # loopback only, always
    bound = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{bound}/#token={token}"
    app = create_app(project, token, bound)
    print(f"Puffin UI: {url}\n(Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[sock])
