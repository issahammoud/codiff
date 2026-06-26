import subprocess

import pytest


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repository with one Python file and one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def hello():\n    pass\n\ndef world():\n    hello()\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo
