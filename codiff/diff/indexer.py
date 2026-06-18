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
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker

from codiff.code_parsing import CodeParser, resolve_internal_calls
from codiff.db import Base, Class, CommitMeta, Function, Repository
from codiff.setup import build_modules_dict, build_package_exports

logger = logging.getLogger(__name__)

DB_FILENAME = ".codiff.db"


def db_path_for(repo_path: str) -> str:
    return os.path.join(os.path.abspath(repo_path), DB_FILENAME)


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
        engine = _make_engine(db_path)
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
    db = db_path_for(repo_path)
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


def _make_engine(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @sa_event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


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
    """Parse *source_path* and write all functions/classes to *db_path*."""
    engine = _make_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Wipe previous snapshot
        db.query(Function).delete()
        db.query(Class).delete()
        db.query(CommitMeta).delete()
        db.query(Repository).delete()
        db.commit()

        repo_id = str(uuid.uuid4())
        repo = Repository(id=repo_id, name="base", url=source_path, is_parsed=False)
        db.add(repo)
        db.commit()

        parser = CodeParser()
        source = Path(source_path)
        modules_dict = build_modules_dict(source, parser)
        package_exports = build_package_exports(source, parser)

        functions_list: list = []
        classes_list: list = []
        imports_dict: dict = {}
        module_docstrings: dict = {}
        class_docstrings: dict = {}

        for root, dirs, files in os.walk(source_path):
            dirs[:] = sorted(d for d in dirs if d not in parser.exclude_dirs)
            for fname in sorted(files):
                if not fname.endswith(".py"):
                    continue
                fpath = Path(root) / fname
                rel = str(fpath.relative_to(source))
                try:
                    src = fpath.read_text(encoding="utf-8", errors="ignore")
                    funcs, classes, imports, mod_doc = parser.parse_code(src, rel, modules_dict)
                    if mod_doc:
                        module_docstrings[rel] = mod_doc
                    functions_list.extend(funcs)
                    classes_list.extend(classes)
                    imports_dict.update(imports)
                    for cls in classes:
                        if cls.docstring:
                            class_docstrings[cls.name] = cls.docstring
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

        for chunk in functions_list:
            mod_doc = module_docstrings.get(chunk.file_path)
            cls_doc = class_docstrings.get(chunk.class_name) if chunk.class_name else None
            db.add(
                Function(
                    repository_id=repo_id,
                    function_id=chunk.id,
                    name=chunk.name,
                    file_path=chunk.file_path,
                    class_name=chunk.class_name,
                    nested=chunk.nested,
                    code=chunk.code,
                    docstring=chunk.docstring,
                    module_docstring=mod_doc,
                    class_docstring=cls_doc,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    parameters=[p.to_dict() for p in chunk.parameters]
                    if chunk.parameters
                    else None,
                    decorators=chunk.decorators,
                    return_type=chunk.return_type,
                    calls=chunk.calls,
                )
            )

        for chunk in classes_list:
            db.add(
                Class(
                    repository_id=repo_id,
                    class_id=chunk.id,
                    name=chunk.name,
                    file_path=chunk.file_path,
                    code=chunk.code,
                    docstring=chunk.docstring,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    decorators=chunk.decorators,
                    superclasses=chunk.superclasses,
                )
            )

        repo.total_functions = len(functions_list)
        repo.total_classes = len(classes_list)
        repo.is_parsed = True
        db.add(CommitMeta(commit_sha=sha))
        db.commit()

        logger.info(
            "Indexed %d functions, %d classes at %s",
            len(functions_list),
            len(classes_list),
            sha[:8],
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()
