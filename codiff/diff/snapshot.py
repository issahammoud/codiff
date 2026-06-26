"""GraphSnapshot: a lightweight, in-memory view of a call graph.

Two loaders:
- load_from_db(db_path)   — reads the indexed base commit from SQLite
- build_from_path(repo_path) — parses the working tree in memory (no DB writes)

Both return a GraphSnapshot with the same structure so the differ can compare them.
"""

import io
import logging
import subprocess
import tarfile
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from codiff.schema.diff import GraphSnapshot, NodeInfo

logger = logging.getLogger(__name__)


def _build_edges(snapshot: GraphSnapshot) -> None:
    """Populate snapshot.edges from node call lists (in-place)."""
    all_ids = set(snapshot.nodes)
    for node in snapshot.nodes.values():
        for callee_id in node.calls:
            if callee_id in all_ids:
                snapshot.edges.add((node.function_id, callee_id))


def load_from_db(db_path: str) -> GraphSnapshot:
    """Load the base snapshot from the indexed SQLite database."""
    from codiff.db import Function

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Session = sessionmaker(bind=engine)
    db = Session()
    snapshot = GraphSnapshot()

    try:
        from codiff.db import Class as DbClass

        for func in db.query(Function).all():
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
        for cls in db.query(DbClass).all():
            if cls.superclasses:
                snapshot.class_parents[cls.class_id] = list(cls.superclasses)
    finally:
        db.close()
        engine.dispose()

    _build_edges(snapshot)
    return snapshot


def build_from_ref(repo_path: str, ref: str) -> GraphSnapshot:
    """Parse a git ref in memory and return a GraphSnapshot (no DB written)."""
    proc = subprocess.run(
        ["git", "archive", ref, "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="codiff_head_") as tmpdir:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(tmpdir, filter="data")
        return build_from_path(tmpdir)


def build_from_path(repo_path: str) -> GraphSnapshot:
    """Parse the working tree at *repo_path* in memory and return a GraphSnapshot."""
    from codiff.parsers import parse_repository

    parsed = parse_repository(repo_path)

    snapshot = GraphSnapshot()
    for cls in parsed.classes:
        if cls.superclasses:
            snapshot.class_parents[cls.id] = list(cls.superclasses)
    for chunk in parsed.functions:
        snapshot.nodes[chunk.id] = NodeInfo(
            function_id=chunk.id,
            name=chunk.name,
            file_path=chunk.file_path,
            class_name=chunk.class_name,
            parameters=[p.to_dict() for p in chunk.parameters] if chunk.parameters else [],
            return_type=chunk.return_type,
            calls=chunk.calls or [],
            code=chunk.code or "",
        )

    _build_edges(snapshot)
    return snapshot
