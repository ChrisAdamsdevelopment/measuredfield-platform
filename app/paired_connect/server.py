"""Paired-only stdio MCP server. Start via owner-local launch.py, never directly.

No public listener. OAuth identity and production-grade multi-tenant pairing are
not implemented here. Intended for trusted private synthetic pilot only.
"""
from __future__ import annotations
import os

try:
    from mcp.server.mcpserver import MCPServer as FastMCP  # Official MCP SDK v2
except ImportError:  # Existing owner-local installations on MCP SDK v1
    from mcp.server.fastmcp import FastMCP

from .registry import BoundSession

mcp = FastMCP(
    "MeasuredField Connected Workspace - Paired Private Pilot",
    instructions=(
        "This local pilot has only the project permissions approved by the owner. "
        "Do not ask for or expose tokens, credentials or files outside the selected "
        "projects. Proposed edits require separate on-device owner approval."
    ),
)


def _session() -> BoundSession:
    # Secret is process-local, not model-callable and never sent as an MCP result.
    pair_id = os.getenv("MEASUREDFIELD_PAIR_ID", "")
    secret = os.getenv("MEASUREDFIELD_PAIR_SECRET", "")
    return BoundSession(pair_id, secret)


@mcp.tool()
def list_granted_projects() -> list[dict]:
    """List only projects approved for this paired private session."""
    return _session().projects()


@mcp.tool()
def browse_project(grant_id: str, relative_path: str = "") -> dict:
    """Browse one folder within this paired session's permitted projects."""
    return _session().browse(grant_id, relative_path)


@mcp.tool()
def read_project_file(grant_id: str, relative_path: str) -> dict:
    """Read a bounded non-secret UTF-8 text file within the paired project scope."""
    return _session().read(grant_id, relative_path)


@mcp.tool()
def propose_project_edit(grant_id: str, relative_path: str, new_content: str) -> dict:
    """Stage a change only when both pairing and folder allow proposals; never apply."""
    return _session().propose(grant_id, relative_path, new_content)


@mcp.tool()
def list_pending_edits() -> list[dict]:
    """List outstanding edit proposals in this pairing's approved projects only."""
    return _session().pending()


@mcp.tool()
def recent_connection_activity(limit: int = 20) -> list[dict]:
    """Show metadata-only activity for the projects this pairing can access."""
    return _session().activity(limit)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
