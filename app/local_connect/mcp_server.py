"""Private stdio MCP server. No model-callable grants, approvals or shell access.

The owner must configure the grants separately with the local CLI.
No network listener or public endpoint is created here.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .store import LocalConnect

mcp = FastMCP(
    "MeasuredField Connected Workspace",
    instructions=(
        "Only user-granted project folders are accessible. Never request credential "
        "files. File edits are proposals that a human must approve separately on "
        "their computer. There is no shell, direct write, deletion, pairing or grant tool."
    ),
)


@mcp.tool()
def list_granted_projects() -> list[dict]:
    """List explicitly connected project names, IDs and read/read-write permissions."""
    return LocalConnect().grants(include_roots=False)


@mcp.tool()
def browse_project(grant_id: str, relative_path: str = "") -> dict:
    """Browse one folder inside a user-approved project (max 100 entries)."""
    return LocalConnect().browse(grant_id, relative_path)


@mcp.tool()
def read_project_file(grant_id: str, relative_path: str) -> dict:
    """Read UTF-8 text up to 256 KiB inside the selected project's permitted scope."""
    return LocalConnect().read(grant_id, relative_path)


@mcp.tool()
def propose_project_edit(grant_id: str, relative_path: str, new_content: str) -> dict:
    """Stage a whole-text-file edit; NEVER changes files until local owner approval."""
    return LocalConnect().propose(grant_id, relative_path, new_content)


@mcp.tool()
def list_pending_edits() -> list[dict]:
    """Show staged changes awaiting local user approval, without exposing their content."""
    return LocalConnect().pending()


@mcp.tool()
def recent_connection_activity(limit: int = 20) -> list[dict]:
    """Show privacy-minimal metadata about recent computer-connection actions."""
    return LocalConnect().audit(limit)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
