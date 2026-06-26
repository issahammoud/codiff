from codiff.parsers.code_parser import ParsedRepository, parse_repository
from codiff.parsers.language_parser import LanguageParser
from codiff.parsers.python_parser import PythonParser
from codiff.parsers.typescript_parser import TypeScriptParser, TypeScriptXParser

# Backward-compatible alias
CodeParser = PythonParser

__all__ = [
    "LanguageParser",
    "PythonParser",
    "TypeScriptParser",
    "TypeScriptXParser",
    "CodeParser",
    "parse_repository",
    "ParsedRepository",
]
