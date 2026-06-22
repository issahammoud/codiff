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

from codiff.db import Base, Class, CommitMeta, Function, Repository, get_db_path, make_sync_engine
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


def ensure_indexed(repo_path: str, ref: str = "HEAD") -> str:
    """Ensure .codiff.db is indexed at *ref*. Re-indexes only when the SHA changed.

    Returns the resolved commit SHA.
    """
    repo_path = os.path.abspath(repo_path)
    db = get_db_path(repo_path)
    sha = resolve_sha(repo_path, ref)

    current = current_indexed_sha(db)
    if current == sha:
        logger.info("DB already at %s — skipping re-index", sha[:8])
        return sha

    logger.info(
        "Indexing %s at %s (was %s)",
        repo_path,
        sha[:8],
        current[:8] if current else "none",
    )
    _full_index(repo_path, db, ref, sha)
    return sha


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _full_index(repo_path: str, db_path: str, ref: str, sha: str) -> None:
    """Extract the git ref to a tmpdir, parse it, write to DB."""
    proc = subprocess.run(
        ["git", "archive", ref, "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="codiff_base_") as tmpdir:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(tmpdir)
        _write_snapshot(tmpdir, db_path, sha)


def _write_snapshot(source_path: str, db_path: str, sha: str) -> None:
    """Parse *source_path* via parse_repository() and write results to *db_path*."""
    from codiff.parsers import parse_repository

    engine = make_sync_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        db.query(Function).delete()
        db.query(Class).delete()
        db.query(CommitMeta).delete()
        db.query(Repository).delete()
        db.commit()

        repo_id = str(uuid.uuid4())
        repo = Repository(id=repo_id, name="base", url=source_path, is_parsed=False)
        db.add(repo)
        db.commit()

        parsed = parse_repository(source_path)

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
