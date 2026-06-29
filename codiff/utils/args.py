"""CLI argument parser for codiff."""

import argparse
import os

DEFAULT_WORKERS: int = max(1, (os.cpu_count() or 2) // 2)

SUPPORTED_AGENTS: list[str] = ["claude", "codex", "gemini", "vibe"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codiff",
        description="Structural diff of a codebase between two states",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Verbosity: -v for INFO, -vv for DEBUG",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # index
    index_parser = subparsers.add_parser(
        "index",
        help="Parse a repository and write the call graph to the database",
    )
    index_parser.add_argument(
        "repo_path",
        help="Path to the repository root directory",
    )

    # diff
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
        default=DEFAULT_WORKERS,
        metavar="N",
        help=(
            f"Number of parallel workers for parsing and resolution"
            f" (default: {DEFAULT_WORKERS}, half of cpu count)"
        ),
    )
    diff_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Show timing breakdown for each processing step (sets log level to DEBUG)",
    )

    # init
    init_parser = subparsers.add_parser(
        "init",
        help="Configure a coding agent to use codiff in the current project",
    )
    init_parser.add_argument(
        "--agent",
        required=True,
        choices=SUPPORTED_AGENTS,
        metavar="AGENT",
        help=f"Coding agent to configure (supported: {', '.join(SUPPORTED_AGENTS)})",
    )
    init_parser.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="Path to the repository root (default: current directory)",
    )

    return parser
