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

_DESCRIPTION = """\
Show the structural call-graph diff of the current codebase against a
base git reference.

WHEN TO CALL
Call this tool once when you are about to create a pull request — NOT after
every file edit.

HOW TO USE
Call with format="mermaid" and embed the returned string in the PR description.
GitHub renders Mermaid natively — no plugin needed.

  codiff_diff(base_ref="main", head_ref="HEAD", format="mermaid")

Include it in the PR body like this:

  ## Structural diff
  <paste the returned string here>

Pass include_deleted=True to also show removed functions in the diagram.
By default, test functions and deleted functions are excluded.
"""


@mcp.tool(description=_DESCRIPTION)
def codiff_diff(
    repo_path: str = ".",
    base_ref: str = "HEAD",
    head_ref: Optional[str] = None,
    include_tests: bool = False,
    include_deleted: bool = False,
    format: str = "terminal",
) -> str:
    """Compute and render the structural call-graph diff.

    Returns an empty string for format="terminal" (output goes to the TTY).
    Returns the diagram/data as a string for format="mermaid" or "json".
    """
    import os

    from codiff.db import get_db_path
    from codiff.diff.analysis import analyze
    from codiff.diff.differ import diff_snapshots
    from codiff.diff.indexer import ensure_indexed
    from codiff.diff.snapshot import build_from_path, build_from_ref, load_from_db
    from codiff.export import render_json, render_mermaid, render_terminal

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

    if not include_tests:
        from codiff.utils.files import is_test_file

        result.added = [fn for fn in result.added if not is_test_file(fn.file_path)]
        result.modified = [fn for fn in result.modified if not is_test_file(fn.file_path)]
        result.removed = [fn for fn in result.removed if not is_test_file(fn.file_path)]

    if not include_deleted:
        result.removed = []

    head_label = head_ref or "working tree"

    if format == "mermaid":
        return render_mermaid(result)
    if format == "json":
        return render_json(result, base_ref=base_ref, head_ref=head_label)

    # terminal — render to TTY, return empty string
    render_terminal(
        result,
        base_ref=base_ref,
        head_ref=head_label,
        include_tests=include_tests,
    )
    return ""


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
