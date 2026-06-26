from codiff.languages.parser import LanguageParser
from codiff.languages.python.parser import PythonParser
from codiff.languages.python.resolver import (
    CallResolver,
    PythonCallResolver,
    resolve_internal_calls,
)
from codiff.languages.repository import ParsedRepository, parse_repository
from codiff.languages.resolver import BaseCallResolver
from codiff.languages.typescript.parser import TypeScriptParser, TypeScriptXParser
from codiff.languages.typescript.resolver import TypeScriptCallResolver

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
    "CallResolver",
    "PythonCallResolver",
    "TypeScriptCallResolver",
    "resolve_internal_calls",
    "BaseCallResolver",
]
