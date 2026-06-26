"""TypeScript and TSX parser implementations.

Implements LanguageParser for TypeScript (.ts) and TSX (.tsx) using
tree-sitter-typescript. Captures function_declaration, method_definition,
module-level arrow functions (const X = () => {}), factory-call consts
(const X = create(...)), object-property arrow functions (Zustand/Redux
patterns), and JSX component usage as function calls.
"""

import json
import os
import re
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Query

from codiff.languages.language_parser import LanguageParser
from codiff.schema.parsing import ClassChunk, FunctionChunk, Parameter


class TypeScriptParser(LanguageParser):
    # ------------------------------------------------------------------
    # LanguageParser interface
    # ------------------------------------------------------------------

    @property
    def language(self) -> Language:
        return Language(tsts.language_typescript())

    @property
    def extension(self) -> str:
        return ".ts"

    @property
    def resolver_class(self):
        from codiff.languages.typescript.resolver import TypeScriptCallResolver

        return TypeScriptCallResolver

    def __init__(self) -> None:
        super().__init__()
        # List of (alias_prefix, target_prefix) where target_prefix is a
        # slash-separated path relative to the repo root (may be empty for root).
        # Populated by build_modules_dict from tsconfig.json; defaults handle
        # the common @/ and ~/ conventions when no tsconfig is present so that
        # tests calling parse_code() directly still work.
        self._path_aliases: list[tuple[str, str]] = [("@/", ""), ("~/", "")]

    # ------------------------------------------------------------------
    # Module resolution
    # ------------------------------------------------------------------

    def file_to_module_id(self, rel_path: str) -> str:
        """'src/api/user.ts' → 'src.api.user'"""
        base, _ = os.path.splitext(rel_path)
        return base.replace("/", ".")

    def _is_package_init(self, filename: str) -> bool:
        return filename in ("index.ts", "index.tsx")

    def build_modules_dict(self, repo_path: Path, gitignore=None) -> dict[str, str]:
        self._path_aliases = self._load_path_aliases(repo_path)
        return super().build_modules_dict(repo_path, gitignore)

    def build_package_exports(self, repo_path: Path, gitignore=None) -> dict[str, str]:
        return {}

    def _extra_exclude_dirs(self) -> set[str]:
        return {".next", ".nuxt", ".cache", ".turbo", "coverage", ".jest-cache"}

    # ------------------------------------------------------------------
    # tsconfig.json path-alias loading
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_tsconfig(text: str) -> dict:
        """Parse JSONC: tsconfig allows // line comments, /* */ block comments,
        and trailing commas — none of which are valid JSON."""
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r",(\s*[}\]])", r"\1", text)
        return json.loads(text)

    def _load_path_aliases(self, repo_path: Path) -> list[tuple[str, str]]:
        """Scan all tsconfig*.json files under repo_path and return
        (alias_prefix, target_prefix) pairs where target_prefix is a
        slash-separated path relative to the repo root (empty = root).

        Falls back to common @/ and ~/ conventions pointing at the repo root
        when no tsconfig paths are found, which covers the majority of projects
        that use default Vite/Next/Nuxt configs without explicit paths.
        """
        aliases: list[tuple[str, str]] = []
        repo_abs = repo_path.resolve()

        for root, dirs, files in os.walk(str(repo_path)):
            dirs[:] = sorted(d for d in dirs if d not in self.exclude_dirs)
            for fname in sorted(files):
                if not (fname.startswith("tsconfig") and fname.endswith(".json")):
                    continue
                tsconfig_file = Path(root) / fname
                try:
                    raw = tsconfig_file.read_text(encoding="utf-8")
                    data = self._parse_tsconfig(raw)
                    compiler_opts = data.get("compilerOptions", {})
                    paths = compiler_opts.get("paths", {})
                    if not paths:
                        continue
                    base_url = compiler_opts.get("baseUrl", ".")
                    tsconfig_dir = tsconfig_file.parent.resolve()
                    base_abs = (tsconfig_dir / base_url).resolve()
                    try:
                        base_rel = base_abs.relative_to(repo_abs)
                    except ValueError:
                        continue  # base_url resolves outside the repo

                    for pattern, targets in paths.items():
                        if not targets:
                            continue
                        target = targets[0]
                        if pattern.endswith("/*") and target.endswith("/*"):
                            alias_prefix = pattern[:-1]  # "@/*" → "@/"
                            target_sub = target[:-1]  # "src/*" → "src/"
                            target_dir = os.path.normpath(str(base_rel / target_sub)).replace(
                                "\\", "/"
                            )
                            if target_dir == ".":
                                target_dir = ""
                            elif not target_dir.endswith("/"):
                                target_dir += "/"
                            aliases.append((alias_prefix, target_dir))
                        elif "*" not in pattern:
                            # Exact alias: "#lib": ["./src/lib"]
                            target_dir = os.path.normpath(str(base_rel / target)).replace("\\", "/")
                            if target_dir == ".":
                                target_dir = ""
                            aliases.append((pattern, target_dir))
                except Exception:
                    continue

        if not aliases:
            # No tsconfig paths found — fall back to common root conventions.
            # @/ is used by Vite, Vue CLI, Next.js, Create React App.
            # ~/ is used by Nuxt and some Webpack setups.
            aliases = [("@/", ""), ("~/", "")]

        return aliases

    # ------------------------------------------------------------------
    # Query initialisation
    # ------------------------------------------------------------------

    def _init_queries(self) -> None:
        lang = self.language

        self.function_query = Query(
            lang,
            """
            [
              (function_declaration
                name: (identifier) @func_name
                parameters: (formal_parameters) @params
                return_type: (type_annotation)? @return_type
                body: (statement_block) @body
              ) @function
              (method_definition
                name: (property_identifier) @func_name
                parameters: (formal_parameters) @params
                return_type: (type_annotation)? @return_type
                body: (statement_block) @body
              ) @function
              (lexical_declaration
                (variable_declarator
                  name: (identifier) @func_name
                  value: (arrow_function
                    parameters: (formal_parameters)? @params
                    body: (statement_block) @body
                  )
                )
              ) @function
              (lexical_declaration
                (variable_declarator
                  name: (identifier) @func_name
                  value: (call_expression
                    function: (identifier)
                  )
                )
              ) @function
              (pair
                key: (property_identifier) @func_name
                value: (arrow_function
                  parameters: (formal_parameters)? @params
                  body: (statement_block) @body
                )
              ) @function
            ]
            """,
        )

        self.class_query = Query(
            lang,
            """
            (class_declaration
                name: (type_identifier) @class_name
                body: (class_body) @body
            ) @class
            """,
        )

        # import_query is not used (parse_imports is overridden), but the
        # LanguageParser base declares it, so set it to any valid query.
        self.import_query = self.function_query

    # ------------------------------------------------------------------
    # Function extraction
    # ------------------------------------------------------------------

    def _extract_function(self, captures_dict: dict, relative_path: str) -> FunctionChunk | None:
        if "func_name" not in captures_dict or "function" not in captures_dict:
            return None

        func_node = self.get_node(captures_dict, "function")
        name_node = self.get_node(captures_dict, "func_name")
        body_node = self.get_node(captures_dict, "body")

        if func_node is None or name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = func_node.start_point[0] + 1
        end_line = func_node.end_point[0] + 1
        func_code = func_node.text.decode("utf-8")

        params_node = self.get_node(captures_dict, "params")
        parameters = self._extract_ts_parameters(params_node) if params_node else []

        return_type = None
        rt_node = self.get_node(captures_dict, "return_type")
        if rt_node:
            # Strip leading ': ' from the type_annotation text
            rt_text = rt_node.text.decode("utf-8")
            return_type = rt_text.lstrip(":").strip()

        class_name = self._get_class_context(func_node)
        nested = self._get_nested_context(func_node)

        path_base = os.path.splitext(relative_path)[0]  # strip .ts / .tsx
        _id = path_base + "/" + class_name + "." if class_name else path_base + "/"
        _id = _id + nested + "." if nested else _id
        _id = _id + func_name
        _id = _id.replace("/", ".")

        var_types, _ = self._extract_ts_variable_assignments(body_node) if body_node else ({}, {})
        calls = self._extract_ts_calls(body_node) if body_node else []

        return FunctionChunk(
            id=_id,
            name=func_name,
            code=func_code,
            docstring=None,
            start_line=start_line,
            end_line=end_line,
            parameters=parameters,
            decorators=[],
            file_path=relative_path,
            class_name=class_name,
            nested=nested,
            return_type=return_type,
            calls=calls,
            var_types=var_types if var_types else None,
            var_sources=None,
        )

    def _get_class_context(self, func_node) -> str | None:
        current = func_node.parent
        depth = 0
        while current and depth < 10:
            if current.type == "class_declaration":
                for child in current.named_children:
                    if child.type == "type_identifier":
                        return child.text.decode("utf-8")
            depth += 1
            current = current.parent
        return None

    def _get_nested_context(self, func_node) -> str | None:
        current = func_node.parent
        depth = 0
        while current and depth < 10:
            if current.type in ("function_declaration", "method_definition"):
                for child in current.named_children:
                    if child.type in ("identifier", "property_identifier"):
                        return child.text.decode("utf-8")
            depth += 1
            current = current.parent
        return None

    def _extract_ts_parameters(self, params_node) -> list[Parameter]:
        parameters = []
        for child in params_node.named_children:
            if child.type in ("required_parameter", "optional_parameter", "rest_parameter"):
                name, type_ = self._extract_ts_param_info(child)
                if name:
                    parameters.append(Parameter(name=name, type=type_, value=None))
        return parameters

    def _extract_ts_param_info(self, param_node) -> tuple[str | None, str | None]:
        name = None
        type_ = None
        for child in param_node.named_children:
            if child.type == "accessibility_modifier":
                continue
            if child.type in ("identifier", "object_pattern", "array_pattern") and name is None:
                if child.type == "identifier":
                    name = child.text.decode("utf-8")
                else:
                    name = child.text.decode("utf-8")
            elif child.type == "type_annotation" and type_ is None:
                type_ = child.text.decode("utf-8").lstrip(":").strip()
        return name, type_

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_ts_calls(self, body_node) -> list[str]:
        calls: list[str] = []

        def walk(node):
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "method_definition",
                    "arrow_function",
                    "function",
                ):
                    continue
                walk(child)

            if node.type == "call_expression":
                call_str = self._parse_ts_call(node)
                if call_str:
                    calls.append(call_str)

            elif node.type == "new_expression":
                ctor = self._parse_ts_new(node)
                if ctor:
                    calls.append(ctor)

            elif node.type in ("jsx_self_closing_element", "jsx_opening_element"):
                name = self._parse_jsx_element_name(node)
                # Emit unconditionally — HTML intrinsics (div, span…) won't be
                # in any import dict so the resolver drops them silently.
                if name:
                    calls.append(name)

        walk(body_node)
        return calls

    def _parse_jsx_element_name(self, node) -> str | None:
        """Return the component name from a jsx_self_closing_element or jsx_opening_element."""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8")
            if child.type == "member_expression":
                return child.text.decode("utf-8")
        return None

    def _parse_ts_call(self, call_node) -> str | None:
        """Extract the call target string from a call_expression node."""
        func_child = None
        for child in call_node.children:
            if child.type != "arguments":
                func_child = child
                break

        if func_child is None:
            return None

        if func_child.type == "identifier":
            return func_child.text.decode("utf-8")

        if func_child.type == "super":
            return "super"

        if func_child.type == "member_expression":
            obj = None
            method = None
            for child in func_child.named_children:
                if obj is None:
                    obj = child.text.decode("utf-8")
                else:
                    method = child.text.decode("utf-8")
            if obj and method:
                return f"{obj}.{method}"
            return func_child.text.decode("utf-8")

        if func_child.type == "call_expression":
            # Chained call: e.g. getService().method()
            inner = self._parse_ts_call(func_child)
            return inner

        return None

    def _parse_ts_new(self, new_node) -> str | None:
        """Extract the constructor name from a new_expression node."""
        for child in new_node.children:
            if child.type in ("new", "arguments"):
                continue
            if child.type in ("identifier", "member_expression"):
                return child.text.decode("utf-8")
        return None

    # ------------------------------------------------------------------
    # Variable assignment extraction (new_expression only)
    # ------------------------------------------------------------------

    def _extract_ts_variable_assignments(self, body_node) -> tuple[dict, dict]:
        var_types: dict = {}

        def walk(node):
            if node.type in (
                "function_declaration",
                "method_definition",
                "arrow_function",
                "function",
            ):
                return
            if node.type in ("lexical_declaration", "variable_declaration"):
                for child in node.named_children:
                    if child.type == "variable_declarator":
                        self._handle_ts_declarator(child, var_types)
            for child in node.children:
                walk(child)

        walk(body_node)
        return var_types, {}

    def _handle_ts_declarator(self, decl_node, var_types: dict) -> None:
        var_name = None
        for child in decl_node.named_children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")
            elif child.type == "new_expression" and var_name is not None:
                ctor = self._parse_ts_new(child)
                if ctor:
                    var_types[var_name] = [ctor]

    # ------------------------------------------------------------------
    # Class extraction
    # ------------------------------------------------------------------

    def _extract_class(self, captures_dict: dict, relative_path: str) -> ClassChunk | None:
        class_node = self.get_node(captures_dict, "class")
        name_node = self.get_node(captures_dict, "class_name")
        if class_node is None or name_node is None:
            return None

        class_name = name_node.text.decode("utf-8")
        start_line = class_node.start_point[0] + 1
        end_line = class_node.end_point[0] + 1
        class_code = class_node.text.decode("utf-8")

        # Extract superclasses from class_heritage → extends_clause
        superclasses: list[str] = []
        for child in class_node.children:
            if child.type == "class_heritage":
                for sub in child.children:
                    if sub.type == "extends_clause":
                        for val in sub.children:
                            if val.type in ("identifier", "type_identifier"):
                                superclasses.append(val.text.decode("utf-8"))

        path_base = os.path.splitext(relative_path)[0]
        _id = (path_base + "/" + class_name).replace("/", ".")

        return ClassChunk(
            id=_id,
            name=class_name,
            code=class_code,
            docstring=None,
            start_line=start_line,
            end_line=end_line,
            decorators=[],
            superclasses=superclasses,
            file_path=relative_path,
        )

    # ------------------------------------------------------------------
    # Import extraction (override parse_imports; _extract_imports unused)
    # ------------------------------------------------------------------

    def _extract_imports(self, captures_dict: dict, all_modules: dict, path=None) -> dict:
        return {}  # parse_imports is overridden; this is never called

    def parse_imports(
        self, source_code: str, all_modules: dict, relative_path: "str | None" = None
    ) -> dict:
        tree = self.parser.parse(bytes(source_code, "utf8"))
        imports: dict = {}
        for child in tree.root_node.children:
            if child.type == "import_statement":
                self._process_import_statement(child, all_modules, relative_path, imports)
        return imports

    def _process_import_statement(
        self, stmt_node, all_modules: dict, current_file: "str | None", imports: dict
    ) -> None:
        # Skip type-only imports (import type { ... })
        for child in stmt_node.children:
            if child.text == b"type":
                return

        source_node = None
        import_clause_node = None
        for child in stmt_node.named_children:
            if child.type == "import_clause":
                import_clause_node = child
            elif child.type == "string":
                source_node = child

        if source_node is None or import_clause_node is None:
            return

        source_str = source_node.text.decode("utf-8")
        module_path = self._resolve_ts_import_source(source_str, current_file, all_modules)
        if module_path is None:
            return

        for child in import_clause_node.children:
            if child.type == "identifier":
                # Default import: import Foo from '...'
                name = child.text.decode("utf-8")
                imports[name] = f"{module_path}.{name}"
            elif child.type == "named_imports":
                for specifier in child.named_children:
                    if specifier.type == "import_specifier":
                        names = [n for n in specifier.named_children if n.type == "identifier"]
                        if len(names) >= 1:
                            orig = names[0].text.decode("utf-8")
                            alias = names[-1].text.decode("utf-8")  # same as orig if no alias
                            imports[alias] = f"{module_path}.{orig}"
            elif child.type == "namespace_import":
                # import * as NS from '...'
                for sub in child.named_children:
                    if sub.type == "identifier":
                        name = sub.text.decode("utf-8")
                        imports[name] = module_path

    def _resolve_ts_import_source(
        self, source_str: str, current_file: "str | None", all_modules: dict
    ) -> "str | None":
        """Resolve a TS import source string to a module path from all_modules."""
        source = source_str.strip("'\"")

        # Check path aliases loaded from tsconfig.json (e.g. @/, ~/, #lib, …).
        for alias_prefix, target_prefix in self._path_aliases:
            if source.startswith(alias_prefix):
                remainder = source[len(alias_prefix) :]
                raw = target_prefix + remainder
                normalized = os.path.normpath(raw).replace("\\", "/")
                mod_key = os.path.splitext(normalized)[0].replace("/", ".")
                if mod_key in all_modules:
                    return all_modules[mod_key]
                # Stem fallback within this alias
                stem = Path(remainder).stem
                for key, val in all_modules.items():
                    if key.endswith("." + stem) or key == stem:
                        return val
                return None  # Matched alias but module not in codebase

        if not source.startswith("."):
            return None  # External npm package — not in codebase

        if current_file is None:
            return None

        current_dir = Path(current_file).parent
        # Resolve relative to current file; normpath handles ./ and ../ correctly.
        # Strip any extension (imports rarely include .ts but handle it defensively).
        resolved = os.path.normpath(str(current_dir / source)).replace("\\", "/")
        mod_key = os.path.splitext(resolved)[0].replace("/", ".")
        if mod_key in all_modules:
            return all_modules[mod_key]

        # Last resort: suffix match on stem (handles unusual path shapes)
        source_stem = Path(source).stem
        for key, val in all_modules.items():
            if key.endswith("." + source_stem) or key == source_stem:
                return val

        return None

    # ------------------------------------------------------------------
    # Module docstring (TypeScript has no string-literal docstrings)
    # ------------------------------------------------------------------

    def _extract_module_docstring(self, source: str) -> "str | None":
        return None


class TypeScriptXParser(TypeScriptParser):
    """Parser for .tsx files (React TypeScript)."""

    @property
    def language(self) -> Language:
        return Language(tsts.language_tsx())

    @property
    def extension(self) -> str:
        return ".tsx"
