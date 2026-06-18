from codiff.parsers.language_parser import LanguageParser
from codiff.parsers.python_parser import PythonParser, is_venv_dir

# Backward-compatible alias
CodeParser = PythonParser

__all__ = [
    "LanguageParser",
    "PythonParser",
    "CodeParser",
    "is_venv_dir",
]
