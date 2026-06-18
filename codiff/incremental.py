"""Incremental index updater: re-parses changed .py files and updates the DB."""

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import Text, cast, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from codiff.db import Class, FileState, Function, get_session_maker
from codiff.parsers import CodeParser, is_venv_dir
from codiff.resolvers import resolve_internal_calls
from codiff.utils.gitignore_utils import is_dir_ignored, load_gitignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_file(path: str) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_and_resolve(repo_path: str, abs_file_path: str) -> tuple:
    """Synchronous: parse one file and resolve its internal calls.

    Uses a lightweight filesystem walk to rebuild modules_dict and
    package_exports (no re-parsing of other files), then resolves calls
    for this file's functions only.

    Returns:
        (functions, classes, module_docstring, class_docstrings_dict)
    """
    from codiff.setup import build_modules_dict, build_package_exports

    parser = CodeParser()
    repo_path_obj = Path(repo_path)
    rel_path = os.path.relpath(abs_file_path, repo_path)
    gitignore = load_gitignore(repo_path)

    modules_dict = build_modules_dict(repo_path_obj, parser, gitignore)
    package_exports = build_package_exports(repo_path_obj, parser, gitignore)

    with open(abs_file_path, "r", encoding="utf-8", errors="ignore") as f:
        source = f.read()

    functions_new, classes_new, imports, module_docstring = parser.parse_code(
        source, rel_path, modules_dict
    )

    class_docstrings = {cls.name: cls.docstring for cls in classes_new if cls.docstring}

    if functions_new:
        functions_new = resolve_internal_calls(
            functions=functions_new,
            classes=classes_new,
            imports=imports,
            modules_dict=modules_dict,
            package_exports=package_exports,
            max_workers=2,
        )

    return functions_new, classes_new, module_docstring, class_docstrings


# ---------------------------------------------------------------------------
# Reverse-edge cleanup
# ---------------------------------------------------------------------------


async def _clean_reverse_edges(session: AsyncSession, repo_id: str, function_id: str) -> None:
    """Remove references to a deleted function_id from other functions' calls lists."""
    stmt = select(Function).where(
        Function.repository_id == repo_id,
        cast(Function.calls, Text).like(f'%"{function_id}"%'),
    )
    result = await session.execute(stmt)
    for func in result.scalars().all():
        if func.calls:
            func.calls = [c for c in func.calls if c != function_id]


# ---------------------------------------------------------------------------
# Per-file processors
# ---------------------------------------------------------------------------


async def process_changed_file(
    session: AsyncSession,
    repo_id: str,
    repo_path: str,
    abs_file_path: str,
) -> None:
    """Diff a changed .py file against the DB and apply minimal updates."""
    rel_path = os.path.relpath(abs_file_path, repo_path)

    # 1. Hash check — skip if file content is unchanged
    try:
        content_hash = _hash_file(abs_file_path)
    except OSError:
        logger.warning("Cannot read %s, skipping", rel_path)
        return

    stmt = select(FileState).where(
        FileState.repository_id == repo_id,
        FileState.file_path == rel_path,
    )
    result = await session.execute(stmt)
    file_state = result.scalar_one_or_none()

    if file_state and file_state.content_hash == content_hash:
        logger.debug("File %s unchanged (hash match), skipping", rel_path)
        return

    logger.info("Incremental update: %s", rel_path)

    # 2. Parse + resolve calls in a thread (synchronous, blocking)
    try:
        functions_new, classes_new, module_docstring, class_docstrings = await asyncio.to_thread(
            _parse_and_resolve, repo_path, abs_file_path
        )
    except Exception as e:
        logger.error("Parse error for %s: %s", rel_path, e)
        return

    # 3. Load existing DB records for this file
    func_stmt = select(Function).where(
        Function.repository_id == repo_id,
        Function.file_path == rel_path,
    )
    result = await session.execute(func_stmt)
    existing_funcs: dict[str, Function] = {f.function_id: f for f in result.scalars().all()}  # type: ignore[misc,attr-defined]

    cls_stmt = select(Class).where(
        Class.repository_id == repo_id,
        Class.file_path == rel_path,
    )
    result = await session.execute(cls_stmt)
    existing_classes: dict[str, Class] = {c.class_id: c for c in result.scalars().all()}  # type: ignore[misc,attr-defined]

    new_funcs = {chunk.id: chunk for chunk in functions_new}
    new_classes = {chunk.id: chunk for chunk in classes_new}

    # 4. Three-way diff on functions
    existing_ids = set(existing_funcs)
    new_ids = set(new_funcs)

    to_delete = existing_ids - new_ids
    to_add = new_ids - existing_ids
    to_check = existing_ids & new_ids

    # 4a. Delete removed functions and clean up dangling reverse call edges
    for fid in to_delete:
        await _clean_reverse_edges(session, repo_id, fid)
        await session.delete(existing_funcs[fid])

    # Added functions
    for fid in to_add:
        chunk = new_funcs[fid]
        class_doc = class_docstrings.get(chunk.class_name) if chunk.class_name else None
        db_func = Function(
            id=str(uuid.uuid4()),
            repository_id=repo_id,
            function_id=fid,
            name=chunk.name,
            file_path=chunk.file_path,
            class_name=chunk.class_name,
            nested=chunk.nested,
            code=chunk.code,
            docstring=chunk.docstring,
            module_docstring=module_docstring,
            class_docstring=class_doc,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            parameters=[p.to_dict() for p in chunk.parameters] if chunk.parameters else None,
            decorators=chunk.decorators,
            return_type=chunk.return_type,
            calls=chunk.calls,
        )
        session.add(db_func)

    # Modified functions
    modified_count = 0
    for fid in to_check:
        chunk = new_funcs[fid]
        db_func = existing_funcs[fid]

        # Always update resolved calls (resolution context may have changed)
        db_func.calls = chunk.calls

        if db_func.code == chunk.code:
            continue  # Body unchanged

        modified_count += 1
        class_doc = class_docstrings.get(chunk.class_name) if chunk.class_name else None
        db_func.code = chunk.code
        db_func.start_line = chunk.start_line
        db_func.end_line = chunk.end_line
        db_func.docstring = chunk.docstring
        db_func.module_docstring = module_docstring
        db_func.class_docstring = class_doc
        db_func.parameters = [p.to_dict() for p in chunk.parameters] if chunk.parameters else None  # type: ignore[assignment]
        db_func.decorators = chunk.decorators
        db_func.return_type = chunk.return_type

    # 5. Three-way diff on classes
    existing_cls_ids = set(existing_classes)
    new_cls_ids = set(new_classes)

    for cid in existing_cls_ids - new_cls_ids:
        await session.delete(existing_classes[cid])

    for cid in new_cls_ids - existing_cls_ids:
        chunk = new_classes[cid]
        session.add(
            Class(
                id=str(uuid.uuid4()),
                repository_id=repo_id,
                class_id=cid,
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

    for cid in existing_cls_ids & new_cls_ids:
        chunk = new_classes[cid]
        db_cls = existing_classes[cid]
        if db_cls.code != chunk.code:
            db_cls.code = chunk.code
            db_cls.docstring = chunk.docstring
            db_cls.start_line = chunk.start_line
            db_cls.end_line = chunk.end_line
            db_cls.decorators = chunk.decorators
            db_cls.superclasses = chunk.superclasses

    # 6. Update FileState
    if file_state:
        file_state.content_hash = content_hash
        file_state.last_indexed_at = datetime.utcnow()
    else:
        session.add(
            FileState(
                id=str(uuid.uuid4()),
                repository_id=repo_id,
                file_path=rel_path,
                content_hash=content_hash,
                last_indexed_at=datetime.utcnow(),
            )
        )

    await session.commit()
    logger.info(
        "Done: %s (+%d added, ~%d modified, -%d deleted functions)",
        rel_path,
        len(to_add),
        modified_count,
        len(to_delete),
    )


async def process_deleted_file(
    session: AsyncSession,
    repo_id: str,
    repo_path: str,
    abs_file_path: str,
) -> None:
    """Remove all DB records for a deleted .py file."""
    rel_path = os.path.relpath(abs_file_path, repo_path)

    # Clean reverse edges and delete functions
    stmt = select(Function).where(
        Function.repository_id == repo_id,
        Function.file_path == rel_path,
    )
    result = await session.execute(stmt)
    for func in result.scalars().all():
        await _clean_reverse_edges(session, repo_id, func.function_id)
        await session.delete(func)

    # Delete classes
    cls_stmt = select(Class).where(
        Class.repository_id == repo_id,
        Class.file_path == rel_path,
    )
    result = await session.execute(cls_stmt)
    for cls in result.scalars().all():
        await session.delete(cls)

    # Delete FileState record
    await session.execute(
        delete(FileState).where(
            FileState.repository_id == repo_id,
            FileState.file_path == rel_path,
        )
    )

    await session.commit()
    logger.info("Removed indexed records for deleted file: %s", rel_path)


# ---------------------------------------------------------------------------
# Batch entry point (used by watcher and startup catchup)
# ---------------------------------------------------------------------------


async def process_changes(
    repo_id: str,
    repo_path: str,
    changed: list[str],
    deleted: list[str],
) -> None:
    """Process a batch of changed and deleted .py file paths."""
    session_maker = get_session_maker()

    for abs_path in changed:
        async with session_maker() as session:
            try:
                await process_changed_file(session, repo_id, repo_path, abs_path)
            except Exception as e:
                logger.error("Error processing changed file %s: %s", abs_path, e, exc_info=True)

    for abs_path in deleted:
        async with session_maker() as session:
            try:
                await process_deleted_file(session, repo_id, repo_path, abs_path)
            except Exception as e:
                logger.error("Error processing deleted file %s: %s", abs_path, e, exc_info=True)


# ---------------------------------------------------------------------------
# Startup catchup
# ---------------------------------------------------------------------------


async def _initialize_file_states(
    repo_id: str, repo_path: str, parser: CodeParser, gitignore=None
) -> None:
    """First-time migration: record current hashes for all .py files without re-indexing."""
    session_maker = get_session_maker()
    count = 0
    async with session_maker() as session:
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [
                d
                for d in dirs
                if d not in parser.exclude_dirs
                and not is_venv_dir(root, d)
                and not is_dir_ignored(gitignore, repo_path, root, d)
            ]
            for file in files:
                if not file.endswith(".py"):
                    continue
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, repo_path)
                try:
                    h = _hash_file(abs_path)
                except OSError:
                    continue
                session.add(
                    FileState(
                        id=str(uuid.uuid4()),
                        repository_id=repo_id,
                        file_path=rel_path,
                        content_hash=h,
                        last_indexed_at=datetime.utcnow(),
                    )
                )
                count += 1
        await session.commit()
    logger.info("Initialized file state records for %d .py files", count)


async def startup_catchup(repo_id: str, repo_path: str) -> None:
    """Detect and re-index any .py files that changed while the server was off.

    Also removes index entries for paths that are now excluded by .gitignore:
    gitignored files are invisible to the walk, so they naturally fall into
    the 'deleted' set and are cleaned from the DB.

    On first run with watch support (no FileState records yet), records
    current hashes for all files without re-indexing, to avoid a full
    re-embed on migration.
    """
    session_maker = get_session_maker()
    parser = CodeParser()
    gitignore = load_gitignore(repo_path)

    async with session_maker() as session:
        stmt = select(FileState).where(FileState.repository_id == repo_id)
        result = await session.execute(stmt)
        known: dict[str, str] = {fs.file_path: fs.content_hash for fs in result.scalars().all()}

    # First run with new code: initialize hashes, skip re-indexing
    if not known:
        logger.info("First run with watch support: initializing file state records...")
        await _initialize_file_states(repo_id, repo_path, parser, gitignore)
        return

    # Detect files changed or added since last run.
    # Gitignored directories are excluded from the walk, so any previously-
    # indexed paths inside them will not appear in current_rel and will be
    # treated as deleted below.
    changed: list[str] = []
    current_rel: set[str] = set()

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d
            for d in dirs
            if d not in parser.exclude_dirs
            and not is_venv_dir(root, d)
            and not is_dir_ignored(gitignore, repo_path, root, d)
        ]
        for file in files:
            if not file.endswith(".py"):
                continue
            abs_path = os.path.join(root, file)
            rel_path = os.path.relpath(abs_path, repo_path)
            current_rel.add(rel_path)
            try:
                h = _hash_file(abs_path)
            except OSError:
                continue
            if rel_path not in known or known[rel_path] != h:
                changed.append(abs_path)

    # Files in the DB but absent from the walk: either deleted from disk or
    # newly covered by .gitignore — both cases require index cleanup.
    deleted = [os.path.join(repo_path, rel) for rel in known if rel not in current_rel]

    if changed or deleted:
        logger.info(
            "Startup catchup: %d changed, %d deleted/gitignored files to re-index",
            len(changed),
            len(deleted),
        )
        await process_changes(repo_id, repo_path, changed, deleted)
    else:
        logger.info("Startup catchup: index is up to date")
