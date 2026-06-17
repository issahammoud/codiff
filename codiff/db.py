"""SQLite database layer — ORM models shared by sync (indexer) and async (watcher) paths."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Helper: store UUIDs as text in SQLite
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    community_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_parsed: Mapped[bool] = mapped_column(default=False)
    total_functions: Mapped[int] = mapped_column(Integer, default=0)
    total_classes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    functions: Mapped[list["Function"]] = relationship(
        "Function",
        back_populates="repository",
        cascade="all, delete-orphan",
    )
    classes: Mapped[list["Class"]] = relationship(
        "Class",
        back_populates="repository",
        cascade="all, delete-orphan",
    )


class Function(Base):
    __tablename__ = "functions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id"), nullable=False
    )

    function_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    class_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    nested: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    module_docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    class_docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    parameters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    decorators: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    return_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    calls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="functions")


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id"), nullable=False
    )

    class_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)

    code: Mapped[str] = mapped_column(Text, nullable=False)
    docstring: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    decorators: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    superclasses: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    repository: Mapped["Repository"] = relationship("Repository", back_populates="classes")


class FileState(Base):
    """Tracks per-file content hashes for incremental re-indexing."""

    __tablename__ = "file_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hex
    last_indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CommitMeta(Base):
    """Records which git commit SHA the database was last indexed from."""

    __tablename__ = "commit_meta"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------

_engine = None
_session_maker = None


def get_db_path(repo_path: str) -> str:
    """Return the SQLite database path for a given repo path."""
    import os

    return os.path.join(repo_path, ".codiff.db")


async def init_db(db_path: str):
    """Create engine, session maker, and tables."""
    global _engine, _session_maker
    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    # Enable WAL mode for better concurrency
    @event.listens_for(_engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session_maker() -> async_sessionmaker:
    if _session_maker is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_maker


def get_sync_db_path(repo_path: str) -> str:
    """Return the SQLite database path (for sync usage in setup)."""
    import os

    return os.path.join(repo_path, ".codiff.db")
