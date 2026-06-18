import os

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from codiff.db.models import Base

_async_engine = None
_async_session_maker = None


def get_db_path(repo_path: str) -> str:
    return os.path.join(os.path.abspath(repo_path), ".codiff.db")


def make_sync_engine(db_path: str):
    """Create a sync SQLAlchemy engine with WAL mode enabled."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def make_sync_session(db_path: str):
    """Create a sync engine + session, ensure tables exist, return (engine, Session)."""
    engine = make_sync_engine(db_path)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


async def init_async_db(db_path: str) -> None:
    """Create async engine, session maker, and tables. Must be called before get_session_maker()."""
    global _async_engine, _async_session_maker

    _async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _async_session_maker = async_sessionmaker(
        _async_engine, class_=AsyncSession, expire_on_commit=False
    )

    @event.listens_for(_async_engine.sync_engine, "connect")
    def _set_wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session_maker() -> async_sessionmaker:
    if _async_session_maker is None:
        raise RuntimeError("Async DB not initialized. Call init_async_db() first.")
    return _async_session_maker
