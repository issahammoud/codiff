"""Background file watcher: detects .py changes and triggers incremental re-indexing."""

import asyncio
import logging
import os

from watchfiles import Change, awatch

from codiff.incremental import process_changes
from codiff.utils.files import is_path_in_venv
from codiff.utils.gitignore_utils import is_file_ignored, load_gitignore

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.5


async def watch_repo(repo_path: str, repo_id: str, indexing_lock: asyncio.Lock) -> None:
    """Watch *repo_path* for .py file changes and trigger incremental re-indexing.

    Runs indefinitely as a background asyncio task.  A debounce window of
    DEBOUNCE_SECONDS is applied: the re-index fires only after the filesystem
    has been quiet for that long, so rapid successive edits to the same file
    (common during active agent sessions) are coalesced into a single job.

    The *indexing_lock* is acquired for the duration of each re-index batch,
    blocking search tools until the index is consistent.
    """
    # path -> 'changed' | 'deleted'  (accumulated between debounce resets)
    pending: dict[str, str] = {}
    flush_task: asyncio.Task | None = None
    gitignore_spec = load_gitignore(repo_path)

    async def flush() -> None:
        """Sleep for the debounce window, then process all pending changes."""
        await asyncio.sleep(DEBOUNCE_SECONDS)

        snapshot = dict(pending)
        pending.clear()

        changed = [p for p, t in snapshot.items() if t == "changed"]
        deleted = [p for p, t in snapshot.items() if t == "deleted"]

        if not changed and not deleted:
            return

        logger.info(
            "Watcher firing: %d changed, %d deleted .py files",
            len(changed),
            len(deleted),
        )
        async with indexing_lock:
            await process_changes(repo_id, repo_path, changed, deleted)

    logger.info("Watching %s for .py changes (debounce=%.1fs)", repo_path, DEBOUNCE_SECONDS)

    try:
        async for changes in awatch(repo_path, force_polling=True, poll_delay_ms=1000):
            had_relevant_change = False
            for change_type, path in changes:
                # Reload gitignore spec immediately if .gitignore itself changed.
                if os.path.basename(path) == ".gitignore" and os.path.dirname(path) == repo_path:
                    gitignore_spec = load_gitignore(repo_path)
                    logger.info(
                        "Reloaded .gitignore (%d pattern(s))",
                        len(gitignore_spec.patterns) if gitignore_spec else 0,
                    )

                if (
                    path.endswith(".py")
                    and not is_path_in_venv(path, repo_path)
                    and not is_file_ignored(gitignore_spec, repo_path, path)
                ):
                    pending[path] = "deleted" if change_type == Change.deleted else "changed"
                    had_relevant_change = True

            if not had_relevant_change:
                continue

            # Cancel the current debounce timer and start a fresh one
            if flush_task and not flush_task.done():
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

            flush_task = asyncio.create_task(flush())

    except asyncio.CancelledError:
        logger.info("Watcher stopped")
        if flush_task and not flush_task.done():
            flush_task.cancel()
        raise
    except Exception:
        logger.error("Watcher crashed", exc_info=True)
