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
