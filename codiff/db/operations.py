"""All database read/write operations for codiff.

Every function that touches SQLite lives here. Callers (indexer, snapshot)
only deal with domain types (ParsedRepository, GraphSnapshot) — they never
open sessions or reference ORM models directly.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

from sqlalchemy import text
from sqlalchemy.orm import Session, load_only, sessionmaker

from codiff.db.engine import make_sync_engine
from codiff.db.models import Base, CallEdge, Class, CommitMeta, Function, Repository
from codiff.schema.diff import GraphSnapshot, NodeInfo

if TYPE_CHECKING:
    from codiff.languages.repository import ParsedRepository


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------


@contextmanager
def _session(db_path: str, create_tables: bool = False) -> Generator[Session, None, None]:
    """Open a session; commit on clean exit, rollback on error."""
    engine = make_sync_engine(db_path)
    if create_tables:
        Base.metadata.create_all(engine)
    db: Session = sessionmaker(bind=engine)()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def get_indexed_sha(db_path: str) -> str | None:
    """Return the commit SHA stored in commit_meta, or None if DB is empty/missing."""
    if not os.path.exists(db_path):
        return None
    try:
        with _session(db_path, create_tables=True) as db:
            meta = db.query(CommitMeta).order_by(CommitMeta.indexed_at.desc()).first()
            return meta.commit_sha if meta else None
    except Exception:
        return None


def load_snapshot(db_path: str) -> GraphSnapshot:
    """Load the indexed snapshot from the SQLite database, including resolved edges."""
    snapshot = GraphSnapshot()
    with _session(db_path) as db:
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
        for cls in db.query(Class).options(load_only(Class.class_id, Class.superclasses)).all():
            if cls.superclasses:
                snapshot.class_parents[cls.class_id] = list(cls.superclasses)

    all_ids = set(snapshot.nodes)
    for node in snapshot.nodes.values():
        for callee_id in node.calls:
            if callee_id in all_ids:
                snapshot.edges.add((node.function_id, callee_id))

    return snapshot


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def write_full_snapshot(db_path: str, parsed: ParsedRepository, sha: str) -> None:
    """Wipe the DB and write *parsed* as the canonical snapshot at *sha*."""
    with _session(db_path, create_tables=True) as db:
        db.query(CallEdge).delete()
        db.query(Function).delete()
        db.query(Class).delete()
        db.query(CommitMeta).delete()
        db.query(Repository).delete()
        db.flush()

        repo_id = str(uuid.uuid4())
        repo = Repository(id=repo_id, name="base", url="", is_parsed=False)
        db.add(repo)

        _insert_functions(db, repo_id, parsed)
        _insert_classes(db, repo_id, parsed)
        db.flush()
        _insert_call_edges(db, parsed.functions)

        repo.total_functions = len(parsed.functions)
        repo.total_classes = len(parsed.classes)
        repo.is_parsed = True
        db.add(CommitMeta(commit_sha=sha))


def write_incremental(
    db_path: str,
    fresh: ParsedRepository,
    files_to_update: set[str],
    old_func_ids: set[str],
    sha: str,
) -> None:
    """Replace rows for *files_to_update* with *fresh*, then update the SHA."""
    with _session(db_path) as db:
        if old_func_ids:
            db.query(CallEdge).filter(CallEdge.caller_id.in_(old_func_ids)).delete(
                synchronize_session=False
            )
        db.query(Function).filter(Function.file_path.in_(files_to_update)).delete(
            synchronize_session=False
        )
        db.query(Class).filter(Class.file_path.in_(files_to_update)).delete(
            synchronize_session=False
        )

        repo = db.query(Repository).first()
        assert repo is not None, "no repository row — DB may be corrupt"

        _insert_functions(db, repo.id, fresh)
        _insert_classes(db, repo.id, fresh)
        db.flush()
        _insert_call_edges(db, fresh.functions)

        db.query(CommitMeta).delete()
        db.add(CommitMeta(commit_sha=sha))


def update_sha(db_path: str, sha: str) -> None:
    """Update the stored commit SHA without touching any other data."""
    with _session(db_path) as db:
        db.query(CommitMeta).delete()
        db.add(CommitMeta(commit_sha=sha))


# ---------------------------------------------------------------------------
# Private insertion helpers
# ---------------------------------------------------------------------------


def _insert_functions(db: Session, repo_id: str, parsed: ParsedRepository) -> None:
    for fn in parsed.functions:
        mod_doc = parsed.module_docstrings.get(fn.file_path)
        cls_doc = parsed.class_docstrings.get(fn.class_name) if fn.class_name else None
        db.add(
            Function(
                repository_id=repo_id,
                function_id=fn.id,
                name=fn.name,
                file_path=fn.file_path,
                class_name=fn.class_name,
                nested=fn.nested,
                code=fn.code,
                docstring=fn.docstring,
                module_docstring=mod_doc,
                class_docstring=cls_doc,
                start_line=fn.start_line,
                end_line=fn.end_line,
                parameters=[p.to_dict() for p in fn.parameters] if fn.parameters else None,
                decorators=fn.decorators,
                return_type=fn.return_type,
                calls=fn.calls,
            )
        )


def _insert_classes(db: Session, repo_id: str, parsed: ParsedRepository) -> None:
    for cls in parsed.classes:
        db.add(
            Class(
                repository_id=repo_id,
                class_id=cls.id,
                name=cls.name,
                file_path=cls.file_path,
                code=cls.code,
                docstring=cls.docstring,
                start_line=cls.start_line,
                end_line=cls.end_line,
                decorators=cls.decorators,
                superclasses=cls.superclasses,
            )
        )


def _insert_call_edges(db: Session, functions) -> None:
    pairs: set[tuple[str, str]] = {
        (fn.id, callee_id) for fn in functions for callee_id in (fn.calls or [])
    }
    if pairs:
        db.execute(
            text(
                "INSERT OR IGNORE INTO call_edges (caller_id, callee_id)"
                " VALUES (:caller_id, :callee_id)"
            ),
            [{"caller_id": c, "callee_id": e} for c, e in pairs],
        )
