import hashlib
import os


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
