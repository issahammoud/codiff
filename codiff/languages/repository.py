"""Language-agnostic entry point for parsing a repository.

parse_repository() is the single function callers use. It:
  1. Asks each registered parser to build its module-resolution dict
  2. Walks the repo and dispatches each file to the right parser by extension
  3. Resolves inter-file call references using each parser's own resolver
  4. Returns a ParsedRepository with all results

Adding a new language means creating a new LanguageParser subclass and
appending it to _PARSERS — no other file changes required.
"""

import logging
import os
from pathlib import Path
from typing import NamedTuple

from codiff.languages.parser import LanguageParser
from codiff.languages.python.parser import PythonParser
from codiff.languages.typescript.parser import TypeScriptParser, TypeScriptXParser
from codiff.schema.parsing import ClassChunk, FunctionChunk
from codiff.utils.files import is_venv_dir
from codiff.utils.gitignore_utils import is_dir_ignored, load_gitignore

logger = logging.getLogger(__name__)

# Registry: add new LanguageParser subclasses here to support more languages
_PARSERS: list[LanguageParser] = [PythonParser(), TypeScriptParser(), TypeScriptXParser()]


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

    Each registered parser handles its own file extension. Call resolution
    runs per-language so that imports from different languages do not bleed
    into each other's resolution context. modules_dict and package_exports
    are merged across languages (they are keyed by module path, which is
    unique per file).
    """
    repo = Path(repo_path)
    if gitignore is None:
        gitignore = load_gitignore(str(repo))

    # Build combined module context and package exports from all parsers.
    # Merging is safe because each parser only registers its own file extensions.
    modules_dict: dict[str, str] = {}
    package_exports: dict[str, str] = {}
    for parser in _PARSERS:
        modules_dict.update(parser.build_modules_dict(repo, gitignore))
        package_exports.update(parser.build_package_exports(repo, gitignore))

    ext_map: dict[str, LanguageParser] = {p.extension: p for p in _PARSERS}

    all_exclude_dirs: set[str] = set()
    for parser in _PARSERS:
        all_exclude_dirs.update(parser.exclude_dirs)

    # Bucket by resolver class, not by file extension.
    # .ts and .tsx share the same TypeScriptCallResolver so they must be grouped
    # together — otherwise a .tsx component that instantiates a .ts class won't
    # find the class in the resolver's all_class_names and loses the edge.
    # Imports are still kept per-language (not per-extension) to prevent Python
    # alias names from bleeding into the TypeScript resolution context.
    resolver_cls_map: dict[type, type] = {p.resolver_class: p.resolver_class for p in _PARSERS}
    ext_to_resolver: dict[str, type] = {p.extension: p.resolver_class for p in _PARSERS}
    resolver_funcs: dict[type, list[FunctionChunk]] = {rc: [] for rc in resolver_cls_map}
    resolver_classes: dict[type, list[ClassChunk]] = {rc: [] for rc in resolver_cls_map}
    resolver_imports: dict[type, dict[str, str]] = {rc: {} for rc in resolver_cls_map}

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
                resolver_cls = ext_to_resolver[ext]
                resolver_funcs[resolver_cls].extend(funcs)
                resolver_classes[resolver_cls].extend(classes)
                resolver_imports[resolver_cls].update(imports)
                for cls in classes:
                    if cls.docstring:
                        class_docstrings[cls.name] = cls.docstring
            except Exception as exc:
                logger.warning("Parse error %s: %s", rel, exc)

    # Resolve calls per resolver class (groups all extensions that share a resolver).
    all_resolved_functions: list[FunctionChunk] = []
    for resolver_cls, funcs in resolver_funcs.items():
        if not funcs:
            continue
        classes = resolver_classes[resolver_cls]
        imports = resolver_imports[resolver_cls]
        resolver = resolver_cls(funcs, classes, imports, modules_dict, package_exports)
        resolved = resolver.resolve_all_calls(max_workers=max_workers)
        all_resolved_functions.extend(resolved)

    all_classes = [cls for ext_classes in resolver_classes.values() for cls in ext_classes]

    return ParsedRepository(
        functions=all_resolved_functions,
        classes=all_classes,
        module_docstrings=module_docstrings,
        class_docstrings=class_docstrings,
        modules_dict=modules_dict,
        package_exports=package_exports,
    )
