"""GraphSnapshot: a lightweight, in-memory view of a call graph.

Two loaders:
- load_from_db(db_path)   — reads the indexed base commit from SQLite
- build_from_path(repo_path) — parses the working tree in memory (no DB writes)

Both return a GraphSnapshot with the same structure so the differ can compare them.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


@dataclass
class NodeInfo:
    """Everything we need about a single function for diffing."""

    function_id: str
    name: str
    file_path: str
    class_name: Optional[str]
    parameters: list[dict]  # [{name, type, value}, ...]
    return_type: Optional[str]
    calls: list[str]  # resolved callee function_ids
    code: str  # used to detect implementation changes


@dataclass
class GraphSnapshot:
    """Nodes and edges of a resolved call graph at one point in time."""

    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    # Edges are (caller_id, callee_id) — only internal (both ends in nodes)
    edges: set[tuple[str, str]] = field(default_factory=set)


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
    finally:
        db.close()
        engine.dispose()

    _build_edges(snapshot)
    return snapshot


def build_from_ref(repo_path: str, ref: str) -> GraphSnapshot:
    """Parse a git ref in memory and return a GraphSnapshot (no DB written).

    Extracts the ref via git archive into a temp directory, runs the same
    parse pipeline as build_from_path, then discards the temp directory.
    """
    import io
    import subprocess
    import tarfile
    import tempfile

    proc = subprocess.run(
        ["git", "archive", ref, "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="codiff_head_") as tmpdir:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(tmpdir)
        return build_from_path(tmpdir)


def build_from_path(repo_path: str) -> GraphSnapshot:
    """Parse the working tree at *repo_path* in memory and return a GraphSnapshot.

    Nothing is written to disk. Uses the same CodeParser + CallResolver pipeline
    as the indexer, but discards results after building the snapshot.
    """
    from codiff.code_parsing import CodeParser, is_venv_dir, resolve_internal_calls
    from codiff.setup import build_modules_dict, build_package_exports
    from codiff.utils.gitignore_utils import is_dir_ignored, load_gitignore

    parser = CodeParser()
    repo = Path(repo_path)
    gitignore = load_gitignore(repo_path)

    modules_dict = build_modules_dict(repo, parser, gitignore)
    package_exports = build_package_exports(repo, parser, gitignore)

    functions_list: list = []
    classes_list: list = []
    imports_dict: dict = {}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in parser.exclude_dirs
            and not is_venv_dir(root, d)
            and not is_dir_ignored(gitignore, str(repo), root, d)
        )
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(repo))
            try:
                src = fpath.read_text(encoding="utf-8", errors="ignore")
                funcs, classes, imports, _ = parser.parse_code(src, rel, modules_dict)
                functions_list.extend(funcs)
                classes_list.extend(classes)
                imports_dict.update(imports)
            except Exception as exc:
                logger.warning("Parse error %s: %s", rel, exc)

    functions_list = resolve_internal_calls(
        functions=functions_list,
        classes=classes_list,
        imports=imports_dict,
        modules_dict=modules_dict,
        package_exports=package_exports,
        max_workers=4,
    )

    snapshot = GraphSnapshot()
    for chunk in functions_list:
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
