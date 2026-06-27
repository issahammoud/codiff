"""GraphSnapshot: a lightweight, in-memory view of a call graph.

Loaders:
- load_from_db(db_path)          — reads the indexed base commit from SQLite
- build_from_path(repo_path)     — parses the working tree in memory (no DB writes)
- build_from_ref(repo_path, ref) — parses a git ref in memory (no DB writes)
- build_incremental_head(...)    — builds HEAD snapshot by re-parsing only changed
                                   files, reusing the base snapshot for the rest

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


def build_incremental_head(
    repo_path: str,
    base: GraphSnapshot,
    base_ref: str,
    head_ref: str | None,
    max_workers: int = 4,
) -> GraphSnapshot:
    """Build the HEAD snapshot by re-parsing only files changed since base_ref.

    Unchanged files are taken directly from *base*, so parsing and resolution
    scale with diff size rather than repo size.  Works whether *base* came from
    the DB or was computed in memory.

    *head_ref* = None means the working tree is HEAD.
    """
    from codiff.languages import parse_repository

    t = time.perf_counter()
    changed_files = _git_changed_files(repo_path, base_ref, head_ref)
    logger.debug(
        "[timing] git diff --name-only: %.2fs  (%d changed files)",
        time.perf_counter() - t,
        len(changed_files),
    )

    if not changed_files:
        logger.info(
            "No changed files between %s and %s — HEAD equals base",
            base_ref,
            head_ref or "working tree",
        )
        return base

    logger.info(
        "Incremental HEAD: parsing %d changed file(s) out of ~%d base nodes",
        len(changed_files),
        len(base.nodes),
    )

    # Build stubs so the resolver can find calls INTO unchanged functions.
    t = time.perf_counter()
    node_stubs = [_NodeStub(n) for n in base.nodes.values()]
    class_stubs = [_ClassStub(cid, supers) for cid, supers in base.class_parents.items()]
    logger.debug(
        "[timing] build stubs: %.2fs  (%d func, %d class)",
        time.perf_counter() - t,
        len(node_stubs),
        len(class_stubs),
    )

    if head_ref is None:
        # Working tree: read changed files directly from disk.
        t = time.perf_counter()
        fresh_parsed = parse_repository(
            repo_path,
            files_to_parse=changed_files,
            extra_index_functions=node_stubs,
            extra_index_classes=class_stubs,
            max_workers=max_workers,
        )
        logger.debug(
            "[timing] parse+resolve changed files: %.2fs  (%d fresh functions)",
            time.perf_counter() - t,
            len(fresh_parsed.functions),
        )
    else:
        # Git ref: extract full repo to tmpdir (preserves module dict integrity),
        # then parse only the changed files.
        t = time.perf_counter()
        proc = subprocess.run(
            ["git", "archive", head_ref, "--format=tar"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        with tempfile.TemporaryDirectory(prefix="codiff_head_") as tmpdir:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
                tf.extractall(tmpdir, filter="data")
            logger.debug("[timing] git archive + extract: %.2fs", time.perf_counter() - t)
            t = time.perf_counter()
            fresh_parsed = parse_repository(
                tmpdir,
                files_to_parse=changed_files,
                extra_index_functions=node_stubs,
                extra_index_classes=class_stubs,
                max_workers=max_workers,
            )
            logger.debug(
                "[timing] parse+resolve changed files: %.2fs  (%d fresh functions)",
                time.perf_counter() - t,
                len(fresh_parsed.functions),
            )

    # Assemble HEAD snapshot: base nodes for unchanged files + fresh nodes for changed files.
    t = time.perf_counter()
    snapshot = GraphSnapshot()
    snapshot.class_parents = dict(base.class_parents)

    for node in base.nodes.values():
        if node.file_path not in changed_files:
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
