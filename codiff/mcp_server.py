"""MCP server — exposes codiff's structural diff as a tool for coding agents.

Run with:
    codiff-mcp          (after `pip install codiff[mcp]`)
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

# The MCP server communicates over stdio, so stdout is the JSON-RPC pipe.
# Open /dev/tty directly so Rich renders to the user's terminal with full
# colors and formatting — identical to running `codiff diff` from the CLI.
try:
    _tty = open("/dev/tty", "w")
    _console = Console(file=_tty, force_terminal=True)
except OSError:
    _console = Console(file=sys.stderr, force_terminal=True)

# Replace the module-level console in render.py before any render calls.
import codiff.diff.render as _render_mod

_render_mod.console = _console

mcp = FastMCP("codiff")

_DESCRIPTION = """\
Show the structural call-graph diff of the current Python codebase against a
base git reference.

WHEN TO CALL
Call this tool at the END of every response in which you created, edited, or
deleted any Python file. Do not wait for a commit — show the structural diff
immediately so the user can see what changed architecturally after each of
your responses. The output is rendered directly in the user's terminal with
full colors and formatting.

OUTPUT FORMAT
The output is split into two top-level groups: Source and Tests.
Within each group there are Added, Modified, and Removed tables.
Columns: File | [Class] | Function | context column.

ADDED TABLE — "← Caller / → Callee" column:
  "entry point"  nothing calls this function; it is new public surface (API,
                 CLI command, test entry, etc.)
  "← caller"    an existing function that now calls this new function —
                 this is where the new code hooks into the existing codebase
  "→ callee"    a function this new function calls

  WHITE names = functions that already existed before this diff
  GRAY  names = functions also added in this same diff

MODIFIED TABLE — "Changes" column:
  "body changed"           implementation changed, same signature and calls
  "+ callee"               this function now calls callee
  "- callee"               this function no longer calls callee
  "was (...) → now (...)"  signature changed

REMOVED TABLE — "Was Called By" lists what used to call the removed function.

CHAIN COLORS
Functions that belong to the same connected call chain share a color across
the entire output (both the Function column and every Caller/Callee
reference). All cyan names are one chain; all magenta names are another.
This lets you visually trace a feature end-to-end at a glance.
Gray = pre-existing code (context). White = new but isolated.

ORDERING
Within each (file, class) block, entry points appear first, then their
callees in depth-first order so each call chain reads top-to-bottom.

By default, test functions are excluded. Pass include_tests=True to show them.
"""


@mcp.tool(description=_DESCRIPTION)
def codiff_diff(
    repo_path: str = ".",
    base_ref: str = "HEAD",
    head_ref: Optional[str] = None,
    include_tests: bool = False,
) -> str:
    """Render the structural call-graph diff directly to the user's terminal."""
    import os

    from codiff.db import get_db_path
    from codiff.diff.analysis import analyze
    from codiff.diff.differ import diff_snapshots
    from codiff.diff.indexer import ensure_indexed
    from codiff.diff.render import render
    from codiff.diff.snapshot import build_from_path, build_from_ref, load_from_db

    repo_path = os.path.abspath(repo_path)
    if head_ref is not None:
        base = build_from_ref(repo_path, base_ref)
        head = build_from_ref(repo_path, head_ref)
    else:
        ensure_indexed(repo_path, base_ref)
        db = get_db_path(repo_path)
        base = load_from_db(db)
        head = build_from_path(repo_path)
    graph_diff = diff_snapshots(base, head)
    result = analyze(graph_diff, base, head)
    render(
        result, base_ref=base_ref, head_ref=head_ref or "working tree", include_tests=include_tests
    )
    return ""


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
