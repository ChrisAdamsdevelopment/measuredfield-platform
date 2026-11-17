"""Legacy never-linked local-only stdio MCP pilot.

This old unpaired endpoint must NEVER serve files on a computer that has an
account link. Use the separate, owner-approved paired MCP server instead.
Every individual tool is checked, even if the stdio process was started before
the owner linked or disconnected their computer. No public listener exists.
"""
from __future__ import annotations

try:
    from mcp.server.mcpserver import MCPServer as FastMCP  # Official MCP SDK v2
except ImportError:  # Existing owner-local installations on MCP SDK v1
    from mcp.server.fastmcp import FastMCP

from app.account_link.runtime_guard import AccountRuntimeGuard
from .store import LocalConnect, _home

mcp = FastMCP(
    "MeasuredField Connected Workspace — Never-Linked Private Pilot",
    instructions=(
        "Legacy owner-local pilot only, disabled after any account connection. "
        "Only user-granted project folders are available. Edits are proposals "
        "requiring separate owner approval. There is no shell, direct write, "
        "deletion, pairing or grant tool."
    ),
)


def _guard() -> AccountRuntimeGuard:
    guard = AccountRuntimeGuard(_home())
    guard.require_never_linked_local_pilot()
    return guard


def _legacy_call(method: str, *args, **kwargs):
    """Guard *before and after* every model-callable local file operation."""
    guard = _guard()
    local = LocalConnect(guard.home)
    result = getattr(local, method)(*args, **kwargs)
    # If account linking began during the operation, never release the result.
    guard.require_never_linked_local_pilot()
    return result


@mcp.tool()
def list_granted_projects() -> list[dict]:
    """List locally granted projects ONLY on a never-linked computer."""
    return _legacy_call("grants", include_roots=False)


@mcp.tool()
def browse_project(grant_id: str, relative_path: str = "") -> dict:
    """Browse a selected folder in the never-linked local pilot."""
    return _legacy_call("browse", grant_id, relative_path)


@mcp.tool()
def read_project_file(grant_id: str, relative_path: str) -> dict:
    """Read a bounded UTF-8 file only for the never-linked local pilot."""
    return _legacy_call("read", grant_id, relative_path)


@mcp.tool()
def propose_project_edit(grant_id: str, relative_path: str, new_content: str) -> dict:
    """Stage a review-only proposal; never apply it automatically."""
    return _legacy_call("propose", grant_id, relative_path, new_content)


@mcp.tool()
def list_pending_edits() -> list[dict]:
    """Show pending changes without disclosing file contents."""
    return _legacy_call("pending")


@mcp.tool()
def recent_connection_activity(limit: int = 20) -> list[dict]:
    """Show privacy-minimal local activity only in never-linked pilot."""
    return _legacy_call("audit", limit)


def main() -> None:
    # Prevent even initializing the stdio protocol on any linked computer.
    _guard()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
