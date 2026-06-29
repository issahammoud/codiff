import subprocess

from sqlalchemy.orm import sessionmaker

from codiff.db import Base, CallEdge, CommitMeta, make_sync_engine
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

    def test_duplicate_calls_in_function_do_not_raise(self, tmp_path):
        """A function calling the same callee twice must not cause a UNIQUE violation."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def helper(): pass\n\ndef caller():\n    helper()\n    helper()\n"
        )
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        ensure_indexed(str(repo), "HEAD")  # must not raise IntegrityError

    def test_call_edges_populated_after_index(self, git_repo):
        ensure_indexed(str(git_repo), "HEAD")
        db_path = str(git_repo / ".codiff.db")
        engine = make_sync_engine(db_path)
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            edges = session.query(CallEdge).all()
        engine.dispose()
        # app.py has world() → hello(), so at least one edge must exist
        assert any(e.callee_id.endswith(".hello") for e in edges)
