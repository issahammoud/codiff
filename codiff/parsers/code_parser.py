"""Language-agnostic entry point for parsing a repository.

parse_repository() is the single function callers use. It:
  1. Asks each registered parser to build its module-resolution dict
  2. Walks the repo and dispatches each file to the right parser by extension
  3. Resolves inter-file call references
  4. Returns a ParsedRepository with all results

Adding a new language means creating a new LanguageParser subclass and
appending it to _PARSERS — no other file changes required.
"""

import logging
import os
from pathlib import Path
from typing import NamedTuple

from codiff.parsers.language_parser import LanguageParser
from codiff.parsers.python_parser import PythonParser
from codiff.resolvers import resolve_internal_calls
from codiff.schema.parsing import ClassChunk, FunctionChunk
from codiff.utils.files import is_venv_dir
from codiff.utils.gitignore_utils import is_dir_ignored, load_gitignore

logger = logging.getLogger(__name__)

# Registry: add new LanguageParser subclasses here to support more languages
_PARSERS: list[LanguageParser] = [PythonParser()]


class ParsedRepository(NamedTuple):
    """All parsed data for a repository — returned by parse_repository()."""

    functions: list[FunctionChunk]
    classes: list[ClassChunk]
    module_docstrings: dict[str, str]  # rel_path → docstring
    class_docstrings: dict[str, str]  # class_name → docstring
    modules_dict: dict[str, str]  # module alias → full module path
    package_exports: dict[str, str]  # re-exported name → real path


def parse_repository(
    repo_path: str | Path,
    gitignore=None,
    max_workers: int = 4,
) -> ParsedRepository:
    """Walk *repo_path*, parse every source file, resolve internal calls.

    Delegates all language-specific work (module naming, package exports,
    AST parsing) to the registered LanguageParser instances. Callers never
    need to know which language they are working with.
    """
    repo = Path(repo_path)
    if gitignore is None:
        gitignore = load_gitignore(str(repo))

    # Build combined module context from all registered parsers
    modules_dict: dict[str, str] = {}
    package_exports: dict[str, str] = {}
    for parser in _PARSERS:
        modules_dict.update(parser.build_modules_dict(repo, gitignore))
        package_exports.update(parser.build_package_exports(repo, gitignore))

    # Extension → parser dispatch map
    ext_map: dict[str, LanguageParser] = {p.extension: p for p in _PARSERS}

    # Shared exclude_dirs across all parsers
    all_exclude_dirs: set[str] = set()
    for parser in _PARSERS:
        all_exclude_dirs.update(parser.exclude_dirs)

    functions_list: list[FunctionChunk] = []
    classes_list: list[ClassChunk] = []
    imports_dict: dict[str, str] = {}
    module_docstrings: dict[str, str] = {}
    class_docstrings: dict[str, str] = {}

    for root, dirs, files in os.walk(str(repo)):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in all_exclude_dirs
            and not is_venv_dir(root, d)
            and not is_dir_ignored(gitignore, str(repo), root, d)
        )
        for fname in sorted(files):
            ext = Path(fname).suffix
            file_parser = ext_map.get(ext)
            if file_parser is None:
                continue
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(repo))
            try:
                src = fpath.read_text(encoding="utf-8", errors="ignore")
                funcs, classes, imports, mod_doc = file_parser.parse_code(src, rel, modules_dict)
                if mod_doc:
                    module_docstrings[rel] = mod_doc
                functions_list.extend(funcs)
                classes_list.extend(classes)
                imports_dict.update(imports)
                for cls in classes:
                    if cls.docstring:
                        class_docstrings[cls.name] = cls.docstring
            except Exception as exc:
                logger.warning("Parse error %s: %s", rel, exc)

    functions_list = resolve_internal_calls(
        functions=functions_list,
        classes=classes_list,
        imports=imports_dict,
        modules_dict=modules_dict,
        package_exports=package_exports,
        max_workers=max_workers,
    )

    return ParsedRepository(
        functions=functions_list,
        classes=classes_list,
        module_docstrings=module_docstrings,
        class_docstrings=class_docstrings,
        modules_dict=modules_dict,
        package_exports=package_exports,
    )
