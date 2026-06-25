import hashlib
import os
from pathlib import Path

# Directories whose presence anywhere in a path marks the file as test code
_TEST_DIRS = {"test", "tests", "testing", "spec", "specs", "__tests__", "e2e", "integration"}

# File name suffixes that mark a file as test code (checked on the bare filename)
_TEST_SUFFIXES = ("_test.py", "_tests.py", "_spec.py", "_specs.py")

# Exact filenames that are always test/fixture infrastructure
_TEST_NAMES = {"conftest.py", "setup_test.py"}


def is_test_file(file_path: str) -> bool:
    """Return True if *file_path* is a test/spec file by any common convention.

    Covers:
    - Any path segment matching a test directory name (tests/, spec/, e2e/, …)
    - Any path segment starting with ``test`` (test_foo.py, testutils.py, …)
    - Filenames ending with ``_test.py``, ``_spec.py``, etc.
    - Exact filenames like conftest.py
    """
    parts = Path(file_path).parts
    name = parts[-1] if parts else ""
    dirs = parts[:-1]
    return (
        any(d.lower() in _TEST_DIRS or d.lower().startswith("test") for d in dirs)
        or name.startswith("test")
        or any(name.endswith(s) for s in _TEST_SUFFIXES)
        or name in _TEST_NAMES
    )


def hash_file(path: str) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_venv_dir(root: str, d: str) -> bool:
    """Return True if *d* under *root* is a Python virtual environment or egg-info directory."""
    if d.endswith(".egg-info"):
        return True
    return os.path.exists(os.path.join(root, d, "pyvenv.cfg"))


def is_path_in_venv(abs_path: str, repo_path: str) -> bool:
    """Return True if *abs_path* lives inside a virtual environment under *repo_path*."""
    try:
        rel = os.path.relpath(abs_path, repo_path)
    except ValueError:
        return False
    current = repo_path
    for part in rel.split(os.sep)[:-1]:
        current = os.path.join(current, part)
        if os.path.exists(os.path.join(current, "pyvenv.cfg")):
            return True
    return False
