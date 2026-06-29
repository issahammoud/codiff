"""Core diff computation — shared between CLI and MCP server."""

import logging
import os
import time

from codiff.utils.args import DEFAULT_WORKERS

log = logging.getLogger(__name__)


def compute_diff(
    repo_path: str,
    base_ref: str = "HEAD",
    head_ref: str | None = None,
    include_tests: bool = False,
    include_deleted: bool = False,
    max_workers: int = DEFAULT_WORKERS,
):
    from codiff.db import get_db_path
    from codiff.diff.analysis import analyze
    from codiff.diff.differ import diff_snapshots
    from codiff.diff.indexer import ensure_indexed
    from codiff.diff.snapshot import build_snapshot_incremental, load_from_db

    repo_path = os.path.abspath(repo_path)
    log.info("Diffing %s → %s", base_ref, head_ref or "working tree")

    t = time.perf_counter()
    db_sha = ensure_indexed(repo_path, "HEAD", max_workers=max_workers)
    log.debug("[timing] ensure_indexed: %.2fs", time.perf_counter() - t)

    db = get_db_path(repo_path)
    t = time.perf_counter()
    db_snapshot = load_from_db(db)
    log.debug(
        "[timing] load_from_db: %.2fs  (%d nodes)", time.perf_counter() - t, len(db_snapshot.nodes)
    )

    t = time.perf_counter()
    base = build_snapshot_incremental(
        repo_path, db_snapshot, db_sha, base_ref, max_workers=max_workers
    )
    log.debug(
        "[timing] build base snapshot: %.2fs  (%d nodes)", time.perf_counter() - t, len(base.nodes)
    )

    t = time.perf_counter()
    head = build_snapshot_incremental(
        repo_path, db_snapshot, db_sha, head_ref, max_workers=max_workers
    )
    log.debug(
        "[timing] build head snapshot: %.2fs  (%d nodes)", time.perf_counter() - t, len(head.nodes)
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

    return result
