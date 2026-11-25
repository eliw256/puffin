"""Read-only views of the project graph shared by the CLI and the MCP server."""

from __future__ import annotations

from typing import Any

from puffin.core.models import ProjectModel, split_endpoint


def board_view(model: ProjectModel, board: str | None) -> dict[str, Any]:
    """The project as plain data, optionally limited to one board (`puffin show --json`)."""
    if board is None:
        return model.model_dump(exclude_none=True)
    refs = {ref for ref, item in model.components.items() if item.board == board}
    data = model.model_dump(exclude_none=True)
    data["boards"] = {board: data["boards"][board]}
    data["components"] = {k: v for k, v in data["components"].items() if k in refs}
    data["nets"] = {
        net: endpoints
        for net, endpoints in data["nets"].items()
        if any(split_endpoint(endpoint)[0] in refs for endpoint in endpoints)
    }
    data["assignments"] = {k: v for k, v in data["assignments"].items() if k in refs}
    data["overrides"] = [o for o in data["overrides"] if split_endpoint(o["pin"])[0] in refs]
    data["constraints"] = [c for c in data["constraints"] if c["net"] in data["nets"]]
    data["intent"] = {k: v for k, v in data["intent"].items() if k in refs}
    return data
