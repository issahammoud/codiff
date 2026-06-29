"""Abstract base class for language-specific parsers.

Defines the interface and shared infrastructure every language implementation
must satisfy. Concrete subclasses supply:
  - language / extension properties
  - _init_queries()
  - _extract_function / _extract_class / _extract_imports / _extract_module_docstring

The parse_code() template method orchestrates the four steps in the right order;
subclasses never override it.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path

from tree_sitter import Language, Parser, Query, QueryCursor

from codiff.schema.parsing import ClassChunk, FunctionChunk


class LanguageParser(ABC):
    # Subclasses must assign these in _init_queries()
    function_query: Query
    class_query: Query
    import_query: Query

    def __init__(self) -> None:
        self.parser = Parser(self.language)
        self.exclude_dirs: set[str] = {
            ".git",
            ".vscode",
            ".idea",
            "build",
            "dist",
            "_build",
            "htmlcov",
            ".coverage",
            "node_modules",
        }
        self.exclude_dirs.update(self._extra_exclude_dirs())
        self._init_queries()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def language(self) -> Language:
        """Tree-sitter Language object for this language."""

    @property
    @abstractmethod
    def extension(self) -> str:
        """File extension, e.g. '.py'."""

    def _extra_exclude_dirs(self) -> set[str]:
        """Language-specific directories to exclude. Override as needed."""
        return set()

    @abstractmethod
    def file_to_module_id(self, rel_path: str) -> str:
        """Convert a relative file path to its canonical module identifier.

        Examples
        --------
        Python  : 'pkg/sub/mod.py'  → 'pkg.sub.mod'
        TS/JS   : 'src/utils/fn.ts' → 'src/utils/fn'
        """

    # ------------------------------------------------------------------
    # Module resolution helpers  (language-agnostic orchestration,
    # language-specific details delegated to subclass methods)
    # ------------------------------------------------------------------

    def build_modules_dict(self, repo_path: Path, gitignore=None) -> dict[str, str]:
        """Map every importable sub-path to its canonical module identifier.

        Calls *self.file_to_module_id()* for each file — fully generic,
        no language-specific logic here.
        """
        from codiff.utils.files import is_venv_dir
        from codiff.utils.git import is_dir_ignored

        modules_dict: dict[str, str] = {}
        init_modules: dict[str, str] = {}
        repo_str = str(repo_path)

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [
                d
                for d in dirs
                if d not in self.exclude_dirs
                and not is_venv_dir(root, d)
                and not is_dir_ignored(gitignore, repo_str, root, d)
            ]
            for file in files:
                if not file.endswith(self.extension):
                    continue
                rel = str((Path(root) / file).relative_to(repo_path))
                module_name = self.file_to_module_id(rel)
                is_init = self._is_package_init(file)
                parts = module_name.split(".")
                for i in range(len(parts)):
                    for j in range(i + 1, len(parts) + 1):
                        sub = ".".join(parts[i:j])
                        if is_init:
                            init_modules[sub] = module_name
                        elif sub not in init_modules:
                            modules_dict[sub] = module_name

        modules_dict.update(init_modules)
        return modules_dict

    @property
    def resolver_class(self):
        """Resolver class to use for this language's functions.

        Override in language-specific parsers to select the appropriate
        BaseCallResolver subclass. Default returns PythonCallResolver so
        that existing parsers keep working without changes.
        """
        from codiff.languages import PythonCallResolver

        return PythonCallResolver

    def build_package_exports(self, repo_path: Path, gitignore=None) -> dict[str, str]:
        """Return a map of re-exported names to their real locations.

        Default implementation returns {} (no re-export mechanism).
        Override in language parsers that have package re-export conventions
        (e.g. Python's __init__.py, TypeScript's index.ts barrel files).
        """
        return {}

    def _is_package_init(self, filename: str) -> bool:
        """True when *filename* is a package initialiser (e.g. __init__.py).

        Override if the language uses a different convention.
        """
        return False

    @abstractmethod
    def _init_queries(self) -> None:
        """Populate self.function_query, self.class_query, self.import_query."""

    @abstractmethod
    def _extract_function(self, captures: dict, path: str) -> "FunctionChunk | None":
        """Build a FunctionChunk from query captures, or None to skip."""

    @abstractmethod
    def _extract_class(self, captures: dict, path: str) -> "ClassChunk | None":
        """Build a ClassChunk from query captures, or None to skip."""

    @abstractmethod
    def _extract_imports(self, captures: dict, all_modules: dict, path: "str | None") -> dict:
        """Return {local_name: resolved_path} from query captures."""

    @abstractmethod
    def _extract_module_docstring(self, source: str) -> "str | None":
        """Extract the module-level docstring from source, or None."""

    # ------------------------------------------------------------------
    # Shared infrastructure
    # ------------------------------------------------------------------

    def get_node(self, captures_dict: dict, capture_name: str):
        """Return the first captured node for *capture_name*, or None."""
        if capture_name not in captures_dict:
            return None
        capture = captures_dict[capture_name]
        if isinstance(capture, list):
            return capture[0] if capture else None
        return capture

    def parse_functions(self, source_code: str, relative_path: str) -> list:
        return self._collect(
            self.function_query, source_code, self._extract_function, relative_path
        )

    def parse_classes(self, source_code: str, relative_path: str) -> list:
        return self._collect(self.class_query, source_code, self._extract_class, relative_path)

    def parse_imports(
        self, source_code: str, all_modules: dict, relative_path: "str | None" = None
    ) -> dict:
        tree = self.parser.parse(bytes(source_code, "utf8"))
        cursor = QueryCursor(self.import_query)
        imports: dict = {}
        for _, captures in cursor.matches(tree.root_node):
            result = self._extract_imports(captures, all_modules, relative_path)
            if result:
                imports.update(result)
        return imports

    def parse_code(self, source_code: str, relative_path: str, all_modules: dict) -> tuple:
        """Parse source and return (functions, classes, imports, module_docstring)."""
        classes = self.parse_classes(source_code, relative_path)
        functions = self.parse_functions(source_code, relative_path)
        imports = self.parse_imports(source_code, all_modules, relative_path)
        docstring = self._extract_module_docstring(source_code)
        return functions, classes, imports, docstring

    def _collect(self, query, source_code: str, extractor, *args) -> list:
        """Run *query* on *source_code*, call *extractor* per match, collect non-None."""
        tree = self.parser.parse(bytes(source_code, "utf8"))
        cursor = QueryCursor(query)
        results = []
        for _, captures in cursor.matches(tree.root_node):
            item = extractor(captures, *args)
            if item is not None:
                results.append(item)
        return results
