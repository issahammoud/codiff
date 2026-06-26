"""Setup pipeline: parse a repository and write the call graph to the database."""

import logging
import os
import uuid

from sqlalchemy.orm import sessionmaker

from codiff.db import Base, Class, Function, Repository, get_db_path, make_sync_engine
from codiff.schema.parsing import ClassChunk, FunctionChunk

logger = logging.getLogger(__name__)


def setup_repository(repo_path: str) -> str:
    """Parse a repository and write the call graph to {repo_path}/.codiff.db.

    Returns the repository UUID.
    """
    from codiff.languages import parse_repository

    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        raise FileNotFoundError(f"Repository path not found: {repo_path}")

    repo_name = os.path.basename(repo_path)
    db_path = get_db_path(repo_path)

    logger.info("Setting up repository: %s", repo_name)
    logger.info("Database: %s", db_path)

    engine = make_sync_engine(db_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        repo_id = str(uuid.uuid4())
        repository = Repository(id=repo_id, name=repo_name, url=repo_path)
        db.add(repository)
        db.commit()

        logger.info("Parsing code...")
        parsed = parse_repository(repo_path)
        logger.info("Parsed %d functions, %d classes", len(parsed.functions), len(parsed.classes))

        logger.info("Storing functions...")
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

        logger.info("Storing classes...")
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

        repository.total_functions = len(parsed.functions)
        repository.total_classes = len(parsed.classes)
        repository.is_parsed = True
        db.commit()

        logger.info("Setup complete! Repository ID: %s", repo_id)
        return repo_id

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
