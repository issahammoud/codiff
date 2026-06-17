"""CLI entry point for codiff."""

import argparse
import logging
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="codiff",
        description="Structural diff of a Python codebase between two states",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Verbosity: -v for INFO, -vv for DEBUG",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # index subcommand — parse a repo and write call graph to DB
    index_parser = subparsers.add_parser(
        "index",
        help="Parse a repository and write the call graph to the database",
    )
    index_parser.add_argument(
        "repo_path",
        help="Path to the repository root directory",
    )

    # diff subcommand
    diff_parser = subparsers.add_parser(
        "diff",
        help="Show the structural delta between a base commit and the working tree",
    )
    diff_parser.add_argument(
        "--base",
        default="HEAD",
        metavar="GIT_REF",
        help="Base git ref to compare against (default: HEAD)",
    )
    diff_parser.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="Path to the repository root (default: current directory)",
    )

    # init subcommand — configure a coding agent to use codiff
    init_parser = subparsers.add_parser(
        "init",
        help="Configure a coding agent to use codiff in the current project",
    )
    init_parser.add_argument(
        "--agent",
        required=True,
        choices=["claude"],
        metavar="AGENT",
        help="Coding agent to configure (supported: claude)",
    )
    init_parser.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="Path to the repository root (default: current directory)",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose >= 2 else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "index":
        from codiff.setup import setup_repository

        setup_repository(args.repo_path)

    elif args.command == "diff":
        _run_diff(repo_path=args.repo, base_ref=args.base)

    elif args.command == "init":
        _run_init(repo_path=args.repo, agent=args.agent)


def _run_init(repo_path: str, agent: str) -> None:
    repo_path = os.path.abspath(repo_path)
    print(f"\nConfiguring codiff for {agent} in {repo_path}\n")
    if agent == "claude":
        _init_claude(repo_path)


def _init_claude(repo_path: str) -> None:
    """Write MCP server config + CLAUDE.md instructions for Claude Code."""
    import json
    from pathlib import Path

    repo = Path(repo_path)

    # ── .mcp.json ─────────────────────────────────────────────────────────────
    mcp_json_path = repo / ".mcp.json"

    mcp_config: dict = {}
    if mcp_json_path.exists():
        try:
            mcp_config = json.loads(mcp_json_path.read_text())
        except json.JSONDecodeError:
            mcp_config = {}

    mcp_servers: dict = mcp_config.setdefault("mcpServers", {})
    if "codiff" in mcp_servers:
        print("  ~ .mcp.json               codiff MCP already registered, skipped")
    else:
        mcp_servers["codiff"] = {"command": "codiff-mcp"}
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
        print("  + .mcp.json               registered codiff-mcp server")

    print("\n  Restart Claude Code to load the new MCP server.\n")


def _run_diff(repo_path: str, base_ref: str) -> None:
    from codiff.diff.analysis import analyze
    from codiff.diff.differ import diff_snapshots
    from codiff.diff.indexer import db_path_for, ensure_indexed
    from codiff.diff.render import render
    from codiff.diff.snapshot import build_from_path, load_from_db

    repo_path = os.path.abspath(repo_path)

    logging.getLogger(__name__).info("Ensuring base index for %s at %s", repo_path, base_ref)
    ensure_indexed(repo_path, base_ref)

    db = db_path_for(repo_path)
    base = load_from_db(db)
    head = build_from_path(repo_path)

    graph_diff = diff_snapshots(base, head)
    result = analyze(graph_diff, base, head)
    render(result, base_ref=base_ref)


if __name__ == "__main__":
    main()
