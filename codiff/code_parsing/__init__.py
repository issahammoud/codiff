from codiff.code_parsing.call_resolver import CallResolver, resolve_internal_calls
from codiff.code_parsing.data_classes import ClassChunk, FunctionChunk, Parameter
from codiff.code_parsing.language_parser import LanguageParser
from codiff.code_parsing.python_parser import PythonParser, is_venv_dir

# Backward-compatible alias
CodeParser = PythonParser

__all__ = [
    "LanguageParser",
    "PythonParser",
    "CodeParser",
    "is_venv_dir",
    "FunctionChunk",
    "ClassChunk",
    "Parameter",
    "CallResolver",
    "resolve_internal_calls",
]
