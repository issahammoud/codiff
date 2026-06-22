from codiff.parsers.code_parser import ParsedRepository, parse_repository
from codiff.parsers.language_parser import LanguageParser
from codiff.parsers.python_parser import PythonParser

# Backward-compatible alias
CodeParser = PythonParser

__all__ = [
    "LanguageParser",
    "PythonParser",
    "CodeParser",
    "parse_repository",
    "ParsedRepository",
]
