from sqlalchemy.orm import sessionmaker

from codiff.db import Base, CommitMeta, make_sync_engine
from codiff.diff.indexer import current_indexed_sha, ensure_indexed, resolve_sha


class TestResolveSha:
    def test_returns_40_char_sha(self, git_repo):
        sha = resolve_sha(str(git_repo), "HEAD")
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_head_resolves_consistently(self, git_repo):
        assert resolve_sha(str(git_repo), "HEAD") == resolve_sha(str(git_repo), "HEAD")


class TestCurrentIndexedSha:
    def test_returns_none_for_missing_db(self, tmp_path):
        assert current_indexed_sha(str(tmp_path / "nonexistent.db")) is None

    def test_returns_none_for_empty_db(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        engine = make_sync_engine(db_path)
        Base.metadata.create_all(engine)
        engine.dispose()
        assert current_indexed_sha(db_path) is None

    def test_returns_sha_after_indexing(self, git_repo):
        sha = ensure_indexed(str(git_repo), "HEAD")
        db_path = str(git_repo / ".codiff.db")
        assert current_indexed_sha(db_path) == sha


class TestEnsureIndexed:
    def test_returns_full_sha(self, git_repo):
        sha = ensure_indexed(str(git_repo), "HEAD")
        assert len(sha) == 40

    def test_creates_db_file(self, git_repo):
        ensure_indexed(str(git_repo), "HEAD")
        assert (git_repo / ".codiff.db").exists()

    def test_stores_commit_sha_in_db(self, git_repo):
        sha = ensure_indexed(str(git_repo), "HEAD")
        db_path = str(git_repo / ".codiff.db")
        engine = make_sync_engine(db_path)
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            meta = session.query(CommitMeta).first()
        engine.dispose()
        assert meta is not None
        assert meta.commit_sha == sha

    def test_skips_reindex_when_sha_unchanged(self, git_repo):
        sha1 = ensure_indexed(str(git_repo), "HEAD")
        sha2 = ensure_indexed(str(git_repo), "HEAD")
        assert sha1 == sha2
