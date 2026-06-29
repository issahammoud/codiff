"""Git and .gitignore utilities shared across the codebase."""

import io
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .gitignore exclusion
# ---------------------------------------------------------------------------


def load_gitignore(repo_path: str):
    """Parse the .gitignore at the repo root.

    Returns a ``pathspec.PathSpec`` instance, or ``None`` if:
    - pathspec is not installed, or
    - no .gitignore exists at repo_path.
    """
    try:
        import pathspec
    except ImportError:
        logger.debug("pathspec not installed; .gitignore exclusion disabled")
        return None

    gitignore_path = os.path.join(repo_path, ".gitignore")
    if not os.path.exists(gitignore_path):
        return None

    try:
        with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        logger.debug("Loaded .gitignore: %d pattern(s)", len(spec.patterns))
        return spec
    except Exception as exc:
        logger.warning("Failed to load .gitignore: %s", exc)
        return None


def is_dir_ignored(spec, repo_path: str, parent_abs: str, dirname: str) -> bool:
    """Return True if *dirname* (inside *parent_abs*) is matched by *spec*.

    A trailing slash is appended to the relative path so that
    directory-specific patterns like ``build/`` match correctly.
    """
    if spec is None:
        return False
    rel = os.path.relpath(os.path.join(parent_abs, dirname), repo_path)
    return spec.match_file(rel) or spec.match_file(rel + "/")


def is_file_ignored(spec, repo_path: str, abs_file_path: str) -> bool:
    """Return True if *abs_file_path* is matched by *spec*."""
    if spec is None:
        return False
    rel = os.path.relpath(abs_file_path, repo_path)
    return spec.match_file(rel)


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def resolve_sha(repo_path: str, ref: str) -> str:
    """Return the full 40-char commit SHA for a git ref."""
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def changed_files_between(repo_path: str, from_ref: str, to_ref: str) -> set[str]:
    """Return relative paths of files changed between two git refs."""
    result = subprocess.run(
        ["git", "diff", "--name-only", from_ref, to_ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    raw = result.stdout.strip()
    return set(raw.split("\n")) if raw else set()


def git_archive(repo_path, ref):
    proc = subprocess.run(
        ["git", "archive", ref, "--format=tar"],
        cwd=repo_path,
        capture_output=True,
        check=True,
    )
    return io.BytesIO(proc.stdout)
