"""MCP server — exposes codiff's structural diff as a tool for coding agents.

Run with:
    codiff-mcp          (after `pip install git+https://github.com/issahammoud/codiff.git`)
    python -m codiff.mcp_server

Configure in Claude Code (.mcp.json in the project root):
    {
      "mcpServers": {
        "codiff": { "command": "codiff-mcp" }
      }
    }
"""

import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP
from rich.console import Console

from codiff.utils.instructions import load as _load_instructions

# The MCP server communicates over stdio, so stdout is the JSON-RPC pipe.
# Open /dev/tty directly so Rich renders to the user's terminal with full
# colors and formatting — identical to running `codiff diff` from the CLI.
try:
    _tty = open("/dev/tty", "w")
    _console = Console(file=_tty, force_terminal=True)
except OSError:
    _console = Console(file=sys.stderr, force_terminal=True)

# Replace the module-level console in export/terminal.py before any render calls.
import codiff.export.terminal as _render_mod

_render_mod.console = _console

mcp = FastMCP("codiff")

_DESCRIPTION: str = _load_instructions()["mcp_description"]


@mcp.tool(description=_DESCRIPTION)
def codiff_diff(
    repo_path: str = ".",
    base_ref: str = "HEAD",
    head_ref: Optional[str] = None,
    include_tests: bool = False,
    include_deleted: bool = False,
    format: str = "mermaid",
) -> str:
    """Compute and render the structural call-graph diff.

    Returns an empty string for format="terminal" (output goes to the TTY).
    Returns the diagram/data as a string for format="mermaid" or "json".
    """
    from codiff.diff.engine import compute_diff
    from codiff.export import render_json, render_mermaid, render_terminal

    result = compute_diff(
        repo_path,
        base_ref=base_ref,
        head_ref=head_ref,
        include_tests=include_tests,
        include_deleted=include_deleted,
        max_workers=1,
    )
    head_label = head_ref or "working tree"

    if format == "mermaid":
        return render_mermaid(result)
    if format == "json":
        return render_json(result, base_ref=base_ref, head_ref=head_label)

    # terminal — render to TTY, return empty string
    render_terminal(result, base_ref=base_ref, head_ref=head_label, include_tests=include_tests)
    return ""


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
