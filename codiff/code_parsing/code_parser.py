# Backward-compatibility shim — import from python_parser directly for new code.
from codiff.code_parsing.python_parser import PythonParser as CodeParser
from codiff.code_parsing.python_parser import is_venv_dir

__all__ = ["CodeParser", "is_venv_dir"]
