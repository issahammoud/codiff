"""GraphSnapshot: a lightweight, in-memory view of a call graph.

Loaders:
- load_from_db(db_path)                     — reads the indexed base commit from SQLite
- build_from_path(repo_path)                — parses the working tree in memory (no DB writes)
- build_from_ref(repo_path, ref)            — parses a git ref in memory (no DB writes)
- build_snapshot_incremental(...)           — builds a snapshot by re-parsing only changed
                                              files relative to a DB anchor, reusing the DB
                                              snapshot for everything else

All loaders return a GraphSnapshot with the same structure.
"""

import io
import logging
import subprocess
import tarfile
import tempfile
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import load_only, sessionmaker

from codiff.languages.repository import ParsedRepository
from codiff.schema.diff import GraphSnapshot, NodeInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub types for the incremental resolver index
# ---------------------------------------------------------------------------


class _NodeStub:
    """Duck-typed FunctionChunk built from a NodeInfo, used only as a resolver
    index entry.  calls=[] prevents the resolver from re-resolving base calls."""

    __slots__ = (
        "id",
        "name",
        "file_path",
        "class_name",
        "calls",
        "return_type",
        "var_types",
        "var_sources",
        "decorators",
        "nested",
        "parameters",
    )

    def __init__(self, node: NodeInfo) -> None:
        self.id = node.function_id
        self.name = node.name
        self.file_path = node.file_path
        self.class_name = node.class_name
        self.calls: list = []
        self.return_type = node.return_type
        self.var_types: dict = {}
        self.var_sources: dict = {}
        self.decorators: list = []
        self.nested = None
        self.parameters = node.parameters


class _ClassStub:
    """Duck-typed ClassChunk built from a class_id + superclass list."""

    __slots__ = ("id", "name", "superclasses")

    def __init__(self, class_id: str, superclasses: list[str]) -> None:
        self.id = class_id
        self.name = class_id.split(".")[-1]
        self.superclasses = superclasses


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_edges(snapshot: GraphSnapshot) -> None:
    """Populate snapshot.edges from node call lists (in-place)."""
    all_ids = set(snapshot.nodes)
    for node in snapshot.nodes.values():
        for callee_id in node.calls:
            if callee_id in all_ids:
                snapshot.edges.add((node.function_id, callee_id))


def _git_changed_files(repo_path: str, base_ref: str, head_ref: str | None) -> set[str]:
    """Return relative paths of files changed between base_ref and HEAD (or head_ref)."""
    cmd = ["git", "diff", "--name-only", base_ref]
    if head_ref is not None:
        cmd.append(head_ref)
    result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, check=True)
    raw = result.stdout.strip()
    return set(raw.split("\n")) if raw else set()


def _chunk_to_node_info(chunk) -> NodeInfo:
    return NodeInfo(
        function_id=chunk.id,
        name=chunk.name,
        file_path=chunk.file_path,
        class_name=chunk.class_name,
        parameters=[p.to_dict() for p in chunk.parameters] if chunk.parameters else [],
        return_type=chunk.return_type,
        calls=chunk.calls or [],
        code=chunk.code or "",
    )


def _parse_and_expand_stale(
    parse_root: str,
    files_to_parse: set[str],
    old_ids_in_changed: set[str],
    db_snapshot: GraphSnapshot,
    node_stubs: list,
    class_stubs: list,
    max_workers: int,
) -> tuple[ParsedRepository, set[str]]:
    """Parse files_to_parse; if function IDs were deleted, expand to stale callers and re-parse.

    Returns (fresh_parsed, final_files_to_parse).
    """
    from codiff.languages import parse_repository

    fresh = parse_repository(
        parse_root,
        files_to_parse=files_to_parse,
        extra_index_functions=node_stubs,
        extra_index_classes=class_stubs,
        max_workers=max_workers,
    )

    deleted_ids = old_ids_in_changed - {fn.id for fn in fresh.functions}
    if not deleted_ids:
        return fresh, files_to_parse

    id_to_file = {nid: node.file_path for nid, node in db_snapshot.nodes.items()}
    stale_files = {
        id_to_file[caller_id]
        for caller_id, callee_id in db_snapshot.edges
        if callee_id in deleted_ids
        and caller_id in id_to_file
        and id_to_file[caller_id] not in files_to_parse
    }
    if not stale_files:
        return fresh, files_to_parse

    logger.info(
        "Expanding to %d stale caller file(s) after %d deletion(s)",
        len(stale_files),
        len(deleted_ids),
    )
    expanded = files_to_parse | stale_files
    fresh = parse_repository(
        parse_root,
        files_to_parse=expanded,
        extra_index_functions=node_stubs,
        extra_index_classes=class_stubs,
        max_workers=max_workers,
    )
    return fresh, expanded


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_from_db(db_path: str) -> GraphSnapshot:
    """Load the base snapshot from the indexed SQLite database."""
    from codiff.db import Class as DbClass
    from codiff.db import Function

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Session = sessionmaker(bind=engine)
    db = Session()
    snapshot = GraphSnapshot()

    try:
        for func in (
            db.query(Function)
            .options(
                load_only(
                    Function.function_id,
                    Function.name,
                    Function.file_path,
                    Function.class_name,
                    Function.code,
                    Function.parameters,
                    Function.return_type,
                    Function.calls,
                )
            )
            .all()
        ):
            snapshot.nodes[func.function_id] = NodeInfo(
                function_id=func.function_id,
                name=func.name,
                file_path=func.file_path,
                class_name=func.class_name,
                parameters=list(func.parameters or []),  # type: ignore[arg-type]
                return_type=func.return_type,
                calls=func.calls or [],
                code=func.code or "",
            )
        for cls in (
            db.query(DbClass).options(load_only(DbClass.class_id, DbClass.superclasses)).all()
        ):
            if cls.superclasses:
                snapshot.class_parents[cls.class_id] = list(cls.superclasses)
    finally:
        db.close()
        engine.dispose()

    _build_edges(snapshot)
    return snapshot


def build_from_ref(repo_path: str, ref: str, max_workers: int = 4) -> GraphSnapshot:
    """Parse a git ref in memory and return a GraphSnapshot (no DB written)."""
    proc = subprocess.run(
        ["git", "archive", ref, "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="codiff_base_") as tmpdir:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(tmpdir, filter="data")
        return build_from_path(tmpdir, max_workers=max_workers)


def build_from_path(repo_path: str, max_workers: int = 4) -> GraphSnapshot:
    """Parse the working tree at *repo_path* in memory and return a GraphSnapshot."""
    from codiff.languages import parse_repository

    parsed = parse_repository(repo_path, max_workers=max_workers)

    snapshot = GraphSnapshot()
    for cls in parsed.classes:
        if cls.superclasses:
            snapshot.class_parents[cls.id] = list(cls.superclasses)
    for chunk in parsed.functions:
        snapshot.nodes[chunk.id] = _chunk_to_node_info(chunk)

    _build_edges(snapshot)
    return snapshot


def build_snapshot_incremental(
    repo_path: str,
    db_snapshot: GraphSnapshot,
    db_sha: str,
    target_ref: str | None,
    max_workers: int = 4,
) -> GraphSnapshot:
    """Build a snapshot by re-parsing only files changed between db_sha and target_ref.

    Unchanged files are taken from db_snapshot. target_ref=None means the working tree.

    When function IDs disappear in the changed files (rename, deletion), callers of
    those IDs in unchanged files are included in the re-parse set so their call
    references stay correct.
    """
    t = time.perf_counter()
    changed_files = _git_changed_files(repo_path, db_sha, target_ref)
    logger.debug(
        "[timing] git diff --name-only: %.2fs  (%d changed files)",
        time.perf_counter() - t,
        len(changed_files),
    )

    if not changed_files:
        logger.info(
            "No changed files between %s and %s — snapshot equals DB",
            db_sha[:8],
            target_ref or "working tree",
        )
        return db_snapshot

    logger.info(
        "Incremental snapshot: %d changed file(s) out of ~%d base nodes",
        len(changed_files),
        len(db_snapshot.nodes),
    )

    t = time.perf_counter()
    node_stubs = [_NodeStub(n) for n in db_snapshot.nodes.values()]
    class_stubs = [_ClassStub(cid, supers) for cid, supers in db_snapshot.class_parents.items()]
    logger.debug(
        "[timing] build stubs: %.2fs  (%d func, %d class)",
        time.perf_counter() - t,
        len(node_stubs),
        len(class_stubs),
    )

    old_ids_in_changed: set[str] = {
        nid for nid, node in db_snapshot.nodes.items() if node.file_path in changed_files
    }

    if target_ref is None:
        t = time.perf_counter()
        fresh_parsed, files_parsed = _parse_and_expand_stale(
            repo_path,
            set(changed_files),
            old_ids_in_changed,
            db_snapshot,
            node_stubs,
            class_stubs,
            max_workers,
        )
        logger.debug(
            "[timing] parse+resolve: %.2fs  (%d fresh functions)",
            time.perf_counter() - t,
            len(fresh_parsed.functions),
        )
    else:
        t = time.perf_counter()
        proc = subprocess.run(
            ["git", "archive", target_ref, "--format=tar"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        with tempfile.TemporaryDirectory(prefix="codiff_snap_") as tmpdir:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
                tf.extractall(tmpdir, filter="data")
            logger.debug("[timing] git archive + extract: %.2fs", time.perf_counter() - t)
            t = time.perf_counter()
            fresh_parsed, files_parsed = _parse_and_expand_stale(
                tmpdir,
                set(changed_files),
                old_ids_in_changed,
                db_snapshot,
                node_stubs,
                class_stubs,
                max_workers,
            )
            logger.debug(
                "[timing] parse+resolve: %.2fs  (%d fresh functions)",
                time.perf_counter() - t,
                len(fresh_parsed.functions),
            )

    t = time.perf_counter()
    snapshot = GraphSnapshot()
    snapshot.class_parents = dict(db_snapshot.class_parents)

    for node in db_snapshot.nodes.values():
        if node.file_path not in files_parsed:
            snapshot.nodes[node.function_id] = node

    for chunk in fresh_parsed.functions:
        snapshot.nodes[chunk.id] = _chunk_to_node_info(chunk)

    for cls in fresh_parsed.classes:
        if cls.superclasses:
            snapshot.class_parents[cls.id] = list(cls.superclasses)

    _build_edges(snapshot)
    logger.debug(
        "[timing] assemble snapshot + build edges: %.2fs  (%d total nodes)",
        time.perf_counter() - t,
        len(snapshot.nodes),
    )
    return snapshot
