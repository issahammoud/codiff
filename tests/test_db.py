import sqlite3

import pytest
from sqlalchemy.orm import sessionmaker

from codiff.db import (
    Base,
    Class,
    CommitMeta,
    Function,
    Repository,
    get_db_path,
    make_sync_engine,
    make_sync_session,
)


class TestGetDbPath:
    def test_appends_codiff_db(self, tmp_path):
        result = get_db_path(str(tmp_path))
        assert result.endswith(".codiff.db")
        assert str(tmp_path) in result


class TestMakeSyncEngine:
    def test_creates_usable_engine(self, tmp_path):
        engine = make_sync_engine(str(tmp_path / "test.db"))
        Base.metadata.create_all(engine)
        engine.dispose()

    def test_wal_journal_mode(self, tmp_path):
        db_path = str(tmp_path / "wal.db")
        engine = make_sync_engine(db_path)
        Base.metadata.create_all(engine)
        engine.dispose()
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


class TestMakeSyncSession:
    def test_returns_engine_and_sessionmaker(self, tmp_path):
        engine, Session = make_sync_session(str(tmp_path / "s.db"))
        assert Session is not None
        engine.dispose()

    def test_tables_created(self, tmp_path):
        from sqlalchemy import inspect

        engine, _ = make_sync_session(str(tmp_path / "t.db"))
        tables = inspect(engine).get_table_names()
        assert "repositories" in tables
        assert "functions" in tables
        assert "classes" in tables
        engine.dispose()


@pytest.fixture
def db_session(tmp_path):
    engine = make_sync_engine(str(tmp_path / "test.db"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


class TestRepositoryModel:
    def test_create_and_query(self, db_session):
        repo = Repository(id="r1", name="myrepo", url="/path/to/repo")
        db_session.add(repo)
        db_session.commit()
        found = db_session.query(Repository).filter_by(name="myrepo").first()
        assert found is not None
        assert found.url == "/path/to/repo"
        assert found.is_parsed is False

    def test_defaults(self, db_session):
        repo = Repository(id="r2", name="r", url="/")
        db_session.add(repo)
        db_session.commit()
        assert repo.total_functions == 0
        assert repo.total_classes == 0


class TestFunctionModel:
    def test_create_and_query(self, db_session):
        db_session.add(Repository(id="r3", name="r", url="/"))
        db_session.commit()
        db_session.add(
            Function(
                repository_id="r3",
                function_id="mod.foo",
                name="foo",
                file_path="mod.py",
                code="def foo(): pass",
                start_line=1,
                end_line=1,
            )
        )
        db_session.commit()
        found = db_session.query(Function).filter_by(name="foo").first()
        assert found.function_id == "mod.foo"

    def test_cascade_delete(self, db_session):
        repo = Repository(id="r4", name="r", url="/")
        db_session.add(repo)
        db_session.commit()
        db_session.add(
            Function(
                repository_id="r4",
                function_id="mod.bar",
                name="bar",
                file_path="mod.py",
                code="pass",
                start_line=1,
                end_line=1,
            )
        )
        db_session.commit()
        db_session.delete(repo)
        db_session.commit()
        assert db_session.query(Function).filter_by(function_id="mod.bar").first() is None


class TestClassModel:
    def test_create_and_query(self, db_session):
        db_session.add(Repository(id="r5", name="r", url="/"))
        db_session.commit()
        db_session.add(
            Class(
                repository_id="r5",
                class_id="mod.MyClass",
                name="MyClass",
                file_path="mod.py",
                code="class MyClass: pass",
                start_line=1,
                end_line=1,
            )
        )
        db_session.commit()
        found = db_session.query(Class).filter_by(name="MyClass").first()
        assert found.class_id == "mod.MyClass"


class TestCommitMetaModel:
    def test_create_and_query(self, db_session):
        meta = CommitMeta(commit_sha="a" * 40)
        db_session.add(meta)
        db_session.commit()
        found = db_session.query(CommitMeta).first()
        assert found.commit_sha == "a" * 40
