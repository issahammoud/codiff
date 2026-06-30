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
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import NamedTuple

from codiff.languages.parser import LanguageParser
from codiff.languages.python.parser import PythonParser
from codiff.languages.typescript.parser import TypeScriptParser, TypeScriptXParser
from codiff.schema.parsing import ClassChunk, FunctionChunk
from codiff.utils.files import is_venv_dir
from codiff.utils.git import is_dir_ignored, load_gitignore

logger = logging.getLogger(__name__)

# Registry: add new LanguageParser subclasses here to support more languages
_PARSERS: list[LanguageParser] = [PythonParser(), TypeScriptParser(), TypeScriptXParser()]

# Worker-process globals — set once per process by _init_mp_worker().
# Using module-level globals avoids pickling modules_dict (130k entries) on
# every task; the initializer sends it once when the worker process starts.
_mp_ext_map: dict | None = None
_mp_modules_dict: dict | None = None


def _init_mp_worker(modules_dict: dict) -> None:
    """Run once in each worker process: create per-process parsers and stash modules_dict."""
    global _mp_ext_map, _mp_modules_dict
    _mp_modules_dict = modules_dict
    _mp_ext_map = {
        p.extension: p for p in [PythonParser(), TypeScriptParser(), TypeScriptXParser()]
    }


def _parse_file_mp(args: tuple) -> tuple | None:
    """Parse one file in a worker process.

    Returns (ext, rel, funcs, classes, imports, mod_doc) or None on error.
    Reads modules_dict from the process-local global set by _init_mp_worker.
    """
    fpath, rel, ext = args
    file_parser = _mp_ext_map.get(ext)  # type: ignore[union-attr]
    if file_parser is None:
        return None
    try:
        src = Path(fpath).read_text(encoding="utf-8", errors="ignore")
        funcs, classes, imports, mod_doc = file_parser.parse_code(src, rel, _mp_modules_dict)
        return (ext, rel, funcs, classes, imports, mod_doc)
    except Exception as exc:
        logging.getLogger(__name__).warning("Parse error %s: %s", rel, exc)
        return None


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
    files_to_parse: set[str] | None = None,
    extra_index_functions=None,
    extra_index_classes=None,
) -> ParsedRepository:
    """Walk *repo_path*, parse every source file, resolve internal calls.

    Each registered parser handles its own file extension. Call resolution
    runs per-language so that imports from different languages do not bleed
    into each other's resolution context. modules_dict and package_exports
    are merged across languages (they are keyed by module path, which is
    unique per file).

    If *files_to_parse* is given, only those relative paths are parsed for
    content (the module/export dicts are still built from the full walk).
    *extra_index_functions* and *extra_index_classes* are duck-typed objects
    that augment the resolver's lookup index without being re-resolved; only
    the freshly parsed functions are resolved and returned.
    """
    repo = Path(repo_path)
    if gitignore is None:
        gitignore = load_gitignore(str(repo))

    # Build combined module context and package exports from all parsers.
    # Merging is safe because each parser only registers its own file extensions.
    t0 = time.perf_counter()
    modules_dict: dict[str, str] = {}
    package_exports: dict[str, str] = {}
    for parser in _PARSERS:
        modules_dict.update(parser.build_modules_dict(repo, gitignore))
        package_exports.update(parser.build_package_exports(repo, gitignore))
    logger.debug(
        "[timing] build module dict: %.2fs  (%d entries)",
        time.perf_counter() - t0,
        len(modules_dict),
    )

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

    # Map each resolver class to its set of file extensions (for index filtering).
    resolver_cls_to_exts: dict[type, set[str]] = {}
    for p in _PARSERS:
        resolver_cls_to_exts.setdefault(p.resolver_class, set()).add(p.extension)

    module_docstrings: dict[str, str] = {}
    class_docstrings: dict[str, str] = {}
    supported_exts = set(ext_map)

    # Phase 1: walk the directory tree to collect files to parse (sequential, fast).
    t0 = time.perf_counter()
    pending: list[tuple] = []
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
            if ext not in supported_exts:
                continue
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(repo))
            if files_to_parse is not None and rel not in files_to_parse:
                continue
            pending.append((fpath, rel, ext))
    logger.debug(
        "[timing] file walk (collect): %.2fs  (%d files)", time.perf_counter() - t0, len(pending)
    )

    # Phase 2: parse files. When max_workers <= 1, run inline in the calling
    # process — no subprocess, no inherited file descriptors (required for MCP
    # stdio servers where child processes inherit the JSON-RPC pipes and corrupt
    # the channel). With multiple workers, use ProcessPoolExecutor as before.
    t0 = time.perf_counter()
    if max_workers <= 1:
        _init_mp_worker(modules_dict)
        file_results = [_parse_file_mp(args) for args in pending]
    else:
        chunksize = max(1, len(pending) // (max_workers * 8))
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_mp_worker,
            initargs=(modules_dict,),
        ) as pool:
            file_results = list(pool.map(_parse_file_mp, pending, chunksize=chunksize))

    for result in file_results:
        if result is None:
            continue
        ext, rel, funcs, classes, imports, mod_doc = result
        resolver_cls = ext_to_resolver[ext]
        if mod_doc:
            module_docstrings[rel] = mod_doc
        resolver_funcs[resolver_cls].extend(funcs)
        resolver_classes[resolver_cls].extend(classes)
        resolver_imports[resolver_cls].update(imports)
        for cls in classes:
            if cls.docstring:
                class_docstrings[cls.name] = cls.docstring

    n_fresh = sum(len(f) for f in resolver_funcs.values())
    logger.debug(
        "[timing] parse files (%s): %.2fs  (%d functions, %d classes)",
        f"{max_workers} workers" if max_workers > 1 else "sequential",
        time.perf_counter() - t0,
        n_fresh,
        sum(len(c) for c in resolver_classes.values()),
    )

    # Resolve calls per resolver class (groups all extensions that share a resolver).
    all_resolved_functions: list[FunctionChunk] = []
    for resolver_cls, funcs in resolver_funcs.items():
        if not funcs and not extra_index_functions:
            continue
        classes = resolver_classes[resolver_cls]
        imports = resolver_imports[resolver_cls]

        t0 = time.perf_counter()
        if extra_index_functions is not None:
            # Incremental mode: augment the resolver's index with stubs from the
            # base snapshot so that fresh functions can resolve calls to unchanged
            # functions. Stubs have calls=[] so they are never re-resolved.
            my_exts = resolver_cls_to_exts.get(resolver_cls, set())
            index_funcs = [f for f in extra_index_functions if Path(f.file_path).suffix in my_exts]
            # Class stubs have no file_path so pass all of them to every resolver;
            # cross-language contamination is harmless in practice.
            index_cls = list(extra_index_classes or [])
            resolver = resolver_cls(
                index_funcs + funcs, index_cls + classes, imports, modules_dict, package_exports
            )
            resolved = resolver.resolve_all_calls(max_workers=max_workers, resolve_subset=funcs)
        else:
            resolver = resolver_cls(funcs, classes, imports, modules_dict, package_exports)
            resolved = resolver.resolve_all_calls(max_workers=max_workers)

        logger.debug(
            "[timing] resolve %s: %.2fs  (%d functions)",
            resolver_cls.__name__,
            time.perf_counter() - t0,
            len(resolved),
        )
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
