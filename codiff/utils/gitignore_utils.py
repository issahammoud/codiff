"""Utilities for .gitignore-based path exclusion.

Loads the repo-root .gitignore at startup and exposes helpers used by every
filesystem walk (setup, incremental, watcher) to skip ignored paths.

Only the repo-root .gitignore is loaded; nested .gitignore files are not
supported (they're uncommon and add significant complexity).

pathspec (https://github.com/cpburnz/python-pathspec) implements the same
'gitwildmatch' pattern language that Git uses, so patterns like 'build/',
'*.egg-info', and '!important' all behave as expected.
"""

import logging
import os

logger = logging.getLogger(__name__)


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
    # Check both 'build' and 'build/' so patterns with and without trailing
    # slash both work.
    return spec.match_file(rel) or spec.match_file(rel + "/")


def is_file_ignored(spec, repo_path: str, abs_file_path: str) -> bool:
    """Return True if *abs_file_path* is matched by *spec*."""
    if spec is None:
        return False
    rel = os.path.relpath(abs_file_path, repo_path)
    return spec.match_file(rel)
