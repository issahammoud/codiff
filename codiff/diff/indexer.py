"""Orchestrate git operations and DB indexing.

The only responsibilities here are:
- resolving git refs to SHAs
- extracting git archives to tmp directories
- deciding whether a full or incremental index is needed
- invoking parsers and delegating all DB writes to codiff.db.operations
"""

import logging
import os
import tarfile
import tempfile

from codiff.db import get_db_path
from codiff.db.operations import (
    get_indexed_sha,
    load_snapshot,
    update_sha,
    write_full_snapshot,
    write_incremental,
)
from codiff.utils.git import changed_files_between, git_archive, resolve_sha

logger = logging.getLogger(__name__)

# Re-exports for backward compatibility with callers/tests that imported from here.
current_indexed_sha = get_indexed_sha


def ensure_indexed(repo_path: str, ref: str = "HEAD", max_workers: int = 4) -> str:
    """Ensure .codiff.db is indexed at *ref*.

    - Empty DB: full parse and index.
    - SHA changed: incremental update (re-parse changed files + stale callers).
    - Same SHA: no-op.

    Returns the resolved commit SHA.
    """
    repo_path = os.path.abspath(repo_path)
    db_path = get_db_path(repo_path)
    sha = resolve_sha(repo_path, ref)
    current = get_indexed_sha(db_path)
    if current == sha:
        logger.info("DB already at %s — skipping re-index", sha[:8])
        return sha

    if current is None:
        logger.info("DB empty — full index of %s at %s", repo_path, sha[:8])
        _full_index(repo_path, db_path, ref, sha, max_workers=max_workers)
    else:
        logger.info("DB at %s — incremental update to %s", current[:8], sha[:8])
        _incremental_update_db(repo_path, db_path, current, sha, max_workers=max_workers)

    return sha


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _full_index(repo_path: str, db_path: str, ref: str, sha: str, max_workers: int = 4) -> None:
    """Extract the git ref to a tmpdir, parse it, write to DB."""
    from codiff.languages import parse_repository

    tar_archive = git_archive(repo_path, ref)
    with tempfile.TemporaryDirectory(prefix="codiff_base_") as tmpdir:
        with tarfile.open(fileobj=tar_archive) as tf:
            tf.extractall(tmpdir, filter="data")
        parsed = parse_repository(tmpdir, max_workers=max_workers)

    write_full_snapshot(db_path, parsed, sha)
    logger.info(
        "Indexed %d functions, %d classes at %s",
        len(parsed.functions),
        len(parsed.classes),
        sha[:8],
    )


def _incremental_update_db(
    repo_path: str, db_path: str, db_sha: str, new_sha: str, max_workers: int = 4
) -> None:
    """Update the DB from db_sha to HEAD incrementally.

    Re-parses files changed between db_sha and HEAD. If function IDs were deleted
    or renamed, also re-parses files whose call references became stale.
    """
    from codiff.diff.snapshot import _ClassStub, _NodeStub, _parse_and_expand_stale

    changed_files = changed_files_between(repo_path, db_sha, "HEAD")
    if not changed_files:
        update_sha(db_path, new_sha)
        return

    logger.info(
        "Incremental DB update: %d changed file(s) (%s → %s)",
        len(changed_files),
        db_sha[:8],
        new_sha[:8],
    )

    db_snapshot = load_snapshot(db_path)
    node_stubs = [_NodeStub(n) for n in db_snapshot.nodes.values()]
    class_stubs = [_ClassStub(cid, supers) for cid, supers in db_snapshot.class_parents.items()]
    old_ids_in_changed: set[str] = {
        nid for nid, node in db_snapshot.nodes.items() if node.file_path in changed_files
    }

    tar_archive = git_archive(repo_path, "HEAD")
    with tempfile.TemporaryDirectory(prefix="codiff_inc_") as tmpdir:
        with tarfile.open(fileobj=tar_archive) as tf:
            tf.extractall(tmpdir, filter="data")
        fresh, files_to_update = _parse_and_expand_stale(
            tmpdir,
            set(changed_files),
            old_ids_in_changed,
            db_snapshot,
            node_stubs,
            class_stubs,
            max_workers,
        )

    old_func_ids: set[str] = {
        nid for nid, node in db_snapshot.nodes.items() if node.file_path in files_to_update
    }
    write_incremental(db_path, fresh, files_to_update, old_func_ids, new_sha)
    logger.info(
        "Incremental update done: %d functions across %d file(s), sha %s",
        len(fresh.functions),
        len(files_to_update),
        new_sha[:8],
    )
