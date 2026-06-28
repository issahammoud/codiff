"""Index a git ref into the codiff database.

Parses the codebase at a given git ref (via git archive), resolves call edges,
and writes everything to .codiff.db at the repo root. The indexed commit SHA is
recorded in commit_meta so subsequent calls can skip re-indexing when the ref
hasn't changed.

No embeddings, no search vectors — call graph only.
"""

import io
import logging
import os
import subprocess
import tarfile
import tempfile
import uuid

from sqlalchemy.orm import sessionmaker

from codiff.db import (
    Base,
    CallEdge,
    Class,
    CommitMeta,
    Function,
    Repository,
    get_db_path,
    make_sync_engine,
)
from codiff.schema.parsing import ClassChunk, FunctionChunk

logger = logging.getLogger(__name__)


def resolve_sha(repo_path: str, ref: str) -> str:
    """Return the full 40-char commit SHA for a git ref."""
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def current_indexed_sha(db_path: str) -> str | None:
    """Return the SHA stored in commit_meta, or None if DB is empty or missing."""
    if not os.path.exists(db_path):
        return None
    try:
        engine = make_sync_engine(db_path)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            meta = db.query(CommitMeta).order_by(CommitMeta.indexed_at.desc()).first()
            return meta.commit_sha if meta else None
    except Exception:
        return None
    finally:
        engine.dispose()


def ensure_indexed(repo_path: str, ref: str = "HEAD", max_workers: int = 4) -> str:
    """Ensure .codiff.db is indexed at *ref*.

    - Empty DB: full parse and index.
    - SHA changed: incremental update (re-parse changed files + stale callers).
    - Same SHA: no-op.

    Returns the resolved commit SHA.
    """
    repo_path = os.path.abspath(repo_path)
    db = get_db_path(repo_path)
    sha = resolve_sha(repo_path, ref)

    current = current_indexed_sha(db)
    if current == sha:
        logger.info("DB already at %s — skipping re-index", sha[:8])
        return sha

    if current is None:
        logger.info("DB empty — full index of %s at %s", repo_path, sha[:8])
        _full_index(repo_path, db, ref, sha, max_workers=max_workers)
    else:
        logger.info("DB at %s — incremental update to %s", current[:8], sha[:8])
        _incremental_update_db(repo_path, db, current, sha, max_workers=max_workers)

    return sha


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _insert_call_edges(db, functions) -> None:
    """Bulk-insert call edges for *functions* using INSERT OR IGNORE.

    Deduplicates globally across all functions before inserting, so duplicate
    callee IDs within a single function and duplicate function objects with the
    same ID both handled without raising a UNIQUE constraint error.
    """
    from sqlalchemy import text

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


def _changed_files_between(repo_path: str, from_ref: str, to_ref: str) -> set[str]:
    """Return relative paths of files changed between two git refs."""
    result = subprocess.run(
        ["git", "diff", "--name-only", from_ref, to_ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    raw = result.stdout.strip()
    return set(raw.split("\n")) if raw else set()


def _full_index(repo_path: str, db_path: str, ref: str, sha: str, max_workers: int = 4) -> None:
    """Extract the git ref to a tmpdir, parse it, write to DB."""
    proc = subprocess.run(
        ["git", "archive", ref, "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="codiff_base_") as tmpdir:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(tmpdir, filter="data")
        _write_snapshot(tmpdir, db_path, sha, max_workers=max_workers)


def _incremental_update_db(
    repo_path: str, db_path: str, db_sha: str, new_sha: str, max_workers: int = 4
) -> None:
    """Update the DB from db_sha to HEAD incrementally.

    Re-parses files changed between db_sha and HEAD. If function IDs were deleted
    or renamed, also re-parses files whose call references became stale.
    """
    from codiff.diff.snapshot import _ClassStub, _NodeStub, _parse_and_expand_stale, load_from_db
    from codiff.languages.repository import ParsedRepository

    changed_files = _changed_files_between(repo_path, db_sha, "HEAD")
    if not changed_files:
        engine = make_sync_engine(db_path)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            db.query(CommitMeta).delete()
            db.add(CommitMeta(commit_sha=new_sha))
            db.commit()
        finally:
            db.close()
            engine.dispose()
        return

    logger.info(
        "Incremental DB update: %d changed file(s) (%s → %s)",
        len(changed_files),
        db_sha[:8],
        new_sha[:8],
    )

    db_snapshot = load_from_db(db_path)
    node_stubs = [_NodeStub(n) for n in db_snapshot.nodes.values()]
    class_stubs = [_ClassStub(cid, supers) for cid, supers in db_snapshot.class_parents.items()]

    old_ids_in_changed: set[str] = {
        nid for nid, node in db_snapshot.nodes.items() if node.file_path in changed_files
    }

    proc = subprocess.run(
        ["git", "archive", "HEAD", "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )

    with tempfile.TemporaryDirectory(prefix="codiff_inc_") as tmpdir:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
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

    fresh_repo: ParsedRepository = fresh
    old_func_ids: set[str] = {
        nid for nid, node in db_snapshot.nodes.items() if node.file_path in files_to_update
    }

    engine = make_sync_engine(db_path)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
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
        assert repo is not None, "no repository row found — DB may be corrupt"
        repo_id = repo.id

        fn: "FunctionChunk"
        for fn in fresh_repo.functions:
            mod_doc = fresh_repo.module_docstrings.get(fn.file_path)
            cls_doc = fresh_repo.class_docstrings.get(fn.class_name) if fn.class_name else None
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

        cls: "ClassChunk"
        for cls in fresh_repo.classes:
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

        db.flush()
        _insert_call_edges(db, fresh_repo.functions)
        db.query(CommitMeta).delete()
        db.add(CommitMeta(commit_sha=new_sha))
        db.commit()

        logger.info(
            "Incremental update done: %d functions across %d file(s), sha %s",
            len(fresh_repo.functions),
            len(files_to_update),
            new_sha[:8],
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()


def _write_snapshot(source_path: str, db_path: str, sha: str, max_workers: int = 4) -> None:
    """Parse *source_path* via parse_repository() and write all results to *db_path*."""
    from codiff.languages import parse_repository

    engine = make_sync_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        db.query(CallEdge).delete()
        db.query(Function).delete()
        db.query(Class).delete()
        db.query(CommitMeta).delete()
        db.query(Repository).delete()
        db.commit()

        repo_id = str(uuid.uuid4())
        repo = Repository(id=repo_id, name="base", url=source_path, is_parsed=False)
        db.add(repo)
        db.commit()

        parsed = parse_repository(source_path, max_workers=max_workers)

        fn: "FunctionChunk"
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

        cls: "ClassChunk"
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

        repo.total_functions = len(parsed.functions)
        repo.total_classes = len(parsed.classes)
        repo.is_parsed = True
        db.flush()
        _insert_call_edges(db, parsed.functions)
        db.add(CommitMeta(commit_sha=sha))
        db.commit()

        logger.info(
            "Indexed %d functions, %d classes at %s",
            len(parsed.functions),
            len(parsed.classes),
            sha[:8],
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()
