import uuid

import pytest

from codiff.db import Function, Repository, make_sync_session
from codiff.setup import setup_repository


@pytest.fixture
def mini_repo(tmp_path):
    (tmp_path / "app.py").write_text("def greet():\n    pass\n\ndef farewell():\n    greet()\n")
    return tmp_path


class TestSetupRepository:
    def test_returns_valid_uuid(self, mini_repo):
        repo_id = setup_repository(str(mini_repo))
        uuid.UUID(repo_id)  # raises ValueError if not a valid UUID

    def test_creates_db_file(self, mini_repo):
        setup_repository(str(mini_repo))
        assert (mini_repo / ".codiff.db").exists()

    def test_functions_stored(self, mini_repo):
        setup_repository(str(mini_repo))
        _, Session = make_sync_session(str(mini_repo / ".codiff.db"))
        with Session() as session:
            names = {fn.name for fn in session.query(Function).all()}
        assert "greet" in names
        assert "farewell" in names

    def test_repository_marked_parsed(self, mini_repo):
        setup_repository(str(mini_repo))
        _, Session = make_sync_session(str(mini_repo / ".codiff.db"))
        with Session() as session:
            repo = session.query(Repository).first()
        assert repo.is_parsed is True
        assert repo.total_functions >= 2

    def test_repo_name_is_dir_basename(self, mini_repo):
        setup_repository(str(mini_repo))
        _, Session = make_sync_session(str(mini_repo / ".codiff.db"))
        with Session() as session:
            repo = session.query(Repository).first()
        assert repo.name == mini_repo.name

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            setup_repository(str(tmp_path / "nonexistent"))
