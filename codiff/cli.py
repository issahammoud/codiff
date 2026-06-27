"""CLI entry point for codiff."""

import argparse
import logging
import os
import sys
import time

_DEFAULT_WORKERS = max(1, (os.cpu_count() or 2) // 2)


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
        "--head",
        default=None,
        metavar="GIT_REF",
        help="Head git ref to compare to (default: working tree)",
    )
    diff_parser.add_argument(
        "--include-tests",
        action="store_true",
        default=False,
        help="Include test functions in the diff output (hidden by default)",
    )
    diff_parser.add_argument(
        "--include-deleted",
        action="store_true",
        default=False,
        help="Include deleted functions in the diff output (hidden by default)",
    )
    diff_parser.add_argument(
        "--format",
        choices=["terminal", "mermaid", "json"],
        default="terminal",
        metavar="FORMAT",
        help="Output format: terminal (default), mermaid, or json",
    )
    diff_parser.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="Path to the repository root (default: current directory)",
    )
    diff_parser.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_WORKERS,
        metavar="N",
        help=f"Number of parallel workers for parsing and resolution (default: {_DEFAULT_WORKERS}, half of cpu count)",
    )
    diff_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Show timing breakdown for each processing step (sets log level to DEBUG)",
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

    level = logging.DEBUG if (args.verbose >= 2 or getattr(args, "debug", False)) else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "index":
        from codiff.setup import setup_repository

        setup_repository(args.repo_path)

    elif args.command == "diff":
        _run_diff(
            repo_path=args.repo,
            base_ref=args.base,
            head_ref=args.head,
            include_tests=args.include_tests,
            include_deleted=args.include_deleted,
            fmt=args.format,
            max_workers=args.workers,
        )

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


def _run_diff(
    repo_path: str,
    base_ref: str,
    head_ref: str | None = None,
    include_tests: bool = False,
    include_deleted: bool = False,
    fmt: str = "terminal",
    max_workers: int = _DEFAULT_WORKERS,
) -> None:
    from codiff.db import get_db_path
    from codiff.diff.analysis import analyze
    from codiff.diff.differ import diff_snapshots
    from codiff.diff.indexer import ensure_indexed
    from codiff.diff.snapshot import build_from_ref, build_incremental_head, load_from_db
    from codiff.export import render_json, render_mermaid, render_terminal

    log = logging.getLogger(__name__)
    repo_path = os.path.abspath(repo_path)

    if head_ref is not None:
        # Both sides are git refs — parse base in full, HEAD incrementally.
        log.info("Diffing %s → %s", base_ref, head_ref)
        t = time.perf_counter()
        base = build_from_ref(repo_path, base_ref, max_workers=max_workers)
        log.debug(
            "[timing] parse base (%s): %.2fs  (%d nodes)",
            base_ref,
            time.perf_counter() - t,
            len(base.nodes),
        )
    else:
        # Head is the working tree — use the cached SQLite index for the base.
        log.info("Ensuring base index for %s at %s", repo_path, base_ref)
        t = time.perf_counter()
        ensure_indexed(repo_path, base_ref, max_workers=max_workers)
        log.debug("[timing] ensure_indexed: %.2fs", time.perf_counter() - t)

        db = get_db_path(repo_path)
        t = time.perf_counter()
        base = load_from_db(db)
        log.debug(
            "[timing] load_from_db: %.2fs  (%d nodes)", time.perf_counter() - t, len(base.nodes)
        )

    t = time.perf_counter()
    head = build_incremental_head(repo_path, base, base_ref, head_ref, max_workers=max_workers)
    log.debug(
        "[timing] build_incremental_head: %.2fs  (%d nodes)",
        time.perf_counter() - t,
        len(head.nodes),
    )

    t = time.perf_counter()
    graph_diff = diff_snapshots(base, head)
    log.debug(
        "[timing] diff_snapshots: %.2fs  (+%d -%d ~%d)",
        time.perf_counter() - t,
        len(graph_diff.added_nodes),
        len(graph_diff.removed_nodes),
        len(graph_diff.modified_nodes),
    )

    t = time.perf_counter()
    result = analyze(graph_diff, base, head)
    log.debug("[timing] analyze: %.2fs", time.perf_counter() - t)

    if not include_tests:
        from codiff.utils.files import is_test_file

        result.added = [fn for fn in result.added if not is_test_file(fn.file_path)]
        result.modified = [fn for fn in result.modified if not is_test_file(fn.file_path)]
        result.removed = [fn for fn in result.removed if not is_test_file(fn.file_path)]

    if not include_deleted:
        result.removed = []

    head_label = head_ref or "working tree"
    if fmt == "json":
        print(render_json(result, base_ref=base_ref, head_ref=head_label))
    elif fmt == "mermaid":
        print(render_mermaid(result))
    else:
        render_terminal(result, base_ref=base_ref, head_ref=head_label, include_tests=include_tests)


if __name__ == "__main__":
    main()
