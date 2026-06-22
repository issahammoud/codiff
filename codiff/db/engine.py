import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from codiff.db.models import Base


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
