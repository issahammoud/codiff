import os
import shutil


def detect_width() -> int:
    """Detect terminal width via COLUMNS env var or shutil fallback."""
    val = os.environ.get("COLUMNS")
    if val and val.isdigit():
        return int(val)
    return shutil.get_terminal_size(fallback=(80, 24)).columns
