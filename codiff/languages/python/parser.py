"""Python-specific parser implementation.

Implements LanguageParser for Python using tree-sitter-python. Contains all
Python-specific query definitions, AST node handling, call extraction, import
resolution, and docstring parsing.
"""

import re
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Query

from codiff.languages.parser import LanguageParser
from codiff.schema.parsing import ClassChunk, FunctionChunk, Parameter


class PythonParser(LanguageParser):
    # ------------------------------------------------------------------
    # LanguageParser interface
    # ------------------------------------------------------------------

    @property
    def language(self) -> Language:
        return Language(tspython.language())

    @property
    def extension(self) -> str:
        return ".py"

    # ------------------------------------------------------------------
    # Module resolution  (Python-specific overrides)
    # ------------------------------------------------------------------

    def file_to_module_id(self, rel_path: str) -> str:
        """'pkg/sub/mod.py' → 'pkg.sub.mod'"""
        return rel_path.replace("/", ".").replace(".py", "")

    def _is_package_init(self, filename: str) -> bool:
        return filename == "__init__.py"

    def build_package_exports(self, repo_path: Path, gitignore=None) -> dict[str, str]:
        """Scan __init__.py files for re-exports (from .sub import name)."""
        import os

        from codiff.utils.files import is_venv_dir
        from codiff.utils.git import is_dir_ignored

        package_exports: dict[str, str] = {}
        repo_str = str(repo_path)

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [
                d
                for d in dirs
                if d not in self.exclude_dirs
                and not is_venv_dir(root, d)
                and not is_dir_ignored(gitignore, repo_str, root, d)
            ]
            if "__init__.py" not in files:
                continue
            init_path = Path(root) / "__init__.py"
            rel_dir = str(init_path.parent.relative_to(repo_path))
            package_name = "" if rel_dir == "." else rel_dir.replace("/", ".")
            try:
                content = init_path.read_text(encoding="utf-8", errors="ignore")
                pattern = r"from\s+\.(\w+)\s+import\s+(?:\(([^)]+)\)|([^(\n]+))"
                for match in re.finditer(pattern, content, re.DOTALL):
                    submodule = match.group(1)
                    names_str = re.sub(r"#[^\n]*", "", match.group(2) or match.group(3))
                    for part in names_str.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        if " as " in part:
                            orig, alias = part.split(" as ")
                            orig, alias = orig.strip(), alias.strip()
                        else:
                            orig = alias = part
                        if not re.match(r"^[a-zA-Z_]\w*$", orig or ""):
                            continue
                        if not re.match(r"^[a-zA-Z_]\w*$", alias or ""):
                            continue
                        if package_name:
                            package_exports[f"{package_name}.{alias}"] = (
                                f"{package_name}.{submodule}.{orig}"
                            )
                        else:
                            package_exports[alias] = f"{submodule}.{orig}"
            except Exception:
                pass

        return package_exports

    def _extra_exclude_dirs(self) -> set[str]:
        return {
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "venv",
            "env",
            ".venv",
            ".eggs",
            ".tox",
        }

    def _init_queries(self) -> None:
        lang = self.language

        self.function_query = Query(
            lang,
            """
            (function_definition
                name: (identifier) @func_name
                parameters: (parameters
                    [
                    (identifier) @param.name
                    (typed_parameter
                        (identifier) @param.typed.name
                        type: (_) @param.typed.type)
                    (default_parameter
                        name: (identifier) @param.default.name
                        value: (_) @param.default.value)
                    (typed_default_parameter
                        (identifier) @param.typed_default.name
                        type: (_) @param.typed_default.type
                        value: (_) @param.typed_default.value)
                    ]*
                ) @params
                return_type: (_)? @return_type
                body: (block
                    (expression_statement
                        (string) @docstring)?
                    (_)*
                ) @body
            ) @function
        """,
        )

        self.class_query = Query(
            lang,
            """
            (class_definition
                name: (identifier) @class_name
                superclasses: (argument_list)? @superclasses
                body: (block
                    (expression_statement
                        (string) @docstring)?
                    (_)*
                ) @body
            ) @class
            """,
        )

        self.import_query = Query(
            lang,
            """
            [
                (import_statement
                    name: (dotted_name) @import.module
                )
                (import_statement
                    name: (aliased_import
                        name: (dotted_name) @import.aliased.name
                        alias: (identifier) @import.aliased.alias
                    )
                )
                (import_from_statement
                    module_name: (dotted_name) @import_from.module
                    name: (dotted_name) @import_from.name
                )
                (import_from_statement
                    module_name: (dotted_name) @import_from.module
                    name: (aliased_import
                        name: (dotted_name) @import_from.aliased.name
                        alias: (identifier) @import_from.aliased.alias
                    )
                )
                (import_from_statement
                    module_name: (relative_import) @import_from.relative_module
                    name: (dotted_name) @import_from.relative_name
                )
                (import_from_statement
                    module_name: (relative_import) @import_from.relative_module
                    name: (aliased_import
                        name: (dotted_name) @import_from.relative_aliased.name
                        alias: (identifier) @import_from.relative_aliased.alias
                    )
                )
            ]
            """,
        )

    # ------------------------------------------------------------------
    # Function extraction
    # ------------------------------------------------------------------

    def _extract_function(self, captures_dict: dict, relative_path: str) -> FunctionChunk | None:
        if "func_name" not in captures_dict or "function" not in captures_dict:
            return None

        func_node = self.get_node(captures_dict, "function")
        name_node = self.get_node(captures_dict, "func_name")
        body_node = self.get_node(captures_dict, "body")

        func_name = name_node.text.decode("utf-8")
        start_line = func_node.start_point[0] + 1
        end_line = func_node.end_point[0] + 1
        func_code = func_node.text.decode("utf-8")

        parameters = []
        params_node = self.get_node(captures_dict, "params")
        if params_node:
            parameters = self._extract_parameters_from_node(params_node)

        return_type = None
        return_type_node = self.get_node(captures_dict, "return_type")
        if return_type_node:
            return_type = return_type_node.text.decode("utf-8")

        docstring = None
        docstring_node = self.get_node(captures_dict, "docstring")
        if docstring_node:
            docstring = docstring_node.text.decode("utf-8")

        decorators, class_name, nested = self._get_function_context(func_node)

        _id = relative_path + "/" + class_name + "." if class_name else relative_path + "/"
        _id = _id + nested + "." if nested else _id
        _id = _id + func_name
        _id = _id.replace("/", ".").replace(".py", "")

        var_types, var_sources = self.extract_variable_assignments(body_node)
        param_names = {p.name for p in parameters if p.name}
        calls = self.extract_calls_from_function_body(body_node, var_types, param_names)

        return FunctionChunk(
            id=_id,
            name=func_name,
            code=func_code,
            docstring=docstring,
            start_line=start_line,
            end_line=end_line,
            parameters=parameters,
            decorators=decorators,
            file_path=relative_path,
            class_name=class_name,
            nested=nested,
            return_type=return_type,
            calls=calls,
            var_types=var_types if var_types else None,
            var_sources=var_sources if var_sources else None,
        )

    def _extract_parameters_from_node(self, params_node) -> list[Parameter]:
        parameters = []
        for param in params_node.named_children:
            name, type_, value = None, None, None
            if param.type == "identifier":
                name = param.text.decode("utf-8")
            elif param.type == "typed_parameter":
                name = param.named_children[0].text.decode("utf-8")
                type_ = param.named_children[1].text.decode("utf-8")
            elif param.type == "default_parameter":
                name = param.named_children[0].text.decode("utf-8")
                value = param.named_children[1].text.decode("utf-8")
            elif param.type == "typed_default_parameter":
                name = param.named_children[0].text.decode("utf-8")
                type_ = param.named_children[1].text.decode("utf-8")
                value = param.named_children[2].text.decode("utf-8")
            if name is not None:
                parameters.append(Parameter(name=name, type=type_, value=value))
        return parameters

    def _get_function_context(self, func_node) -> tuple[list[str], str | None, str | None]:
        decorators: list[str] = []
        nested = None
        class_name = None

        parent = func_node.parent
        if parent:
            func_index = list(parent.children).index(func_node)
            for i in range(func_index - 1, -1, -1):
                sibling = parent.children[i]
                if sibling.type == "decorator":
                    decorator_text = sibling.text.decode("utf-8").strip()
                    if decorator_text.startswith("@"):
                        decorator_text = decorator_text[1:]
                    decorators.insert(0, decorator_text)

        depth = 0
        current = func_node.parent
        while current and depth < 10:
            if current.type == "class_definition":
                for child in current.named_children:
                    if child.type == "identifier":
                        class_name = child.text.decode("utf-8")
                        break
            elif current.type == "function_definition" and nested is None:
                for child in current.named_children:
                    if child.type == "identifier":
                        nested = child.text.decode("utf-8")
                        break
            current = current.parent
            depth += 1

        return decorators, class_name, nested

    def _extract_decorator_call(self, decorator_text: str) -> str | None:
        builtin_decorators = {
            "staticmethod",
            "classmethod",
            "property",
            "abstractmethod",
            "dataclass",
            "contextmanager",
            "lru_cache",
            "cached_property",
        }
        func_name = (
            decorator_text.split("(")[0].strip()
            if "(" in decorator_text
            else decorator_text.strip()
        )
        if func_name.split(".")[-1] in builtin_decorators:
            return None
        return func_name

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def extract_calls_from_function_body(self, body_node, var_types=None, param_names=None) -> list:
        calls = []
        var_types = var_types or {}
        param_names = param_names or set()

        def walk(node):
            for child in node.children:
                if child.type == "function_definition":
                    continue
                walk(child)

            if node.type == "call":
                call_info = self.parse_call_node(node)
                if call_info:
                    calls.append(call_info)
                dunder_calls = self._extract_builtin_dunder_calls(node)
                calls.extend(dunder_calls)
                func_refs = self._extract_function_references_from_call(
                    node, var_types, param_names
                )
                calls.extend(func_refs)

        walk(body_node)
        method_refs = self._extract_self_method_references(body_node, calls)
        calls.extend(method_refs)
        return calls

    def _extract_self_method_references(self, body_node, existing_calls) -> list:
        refs = []
        existing_set = set(existing_calls)

        def walk(node):
            if node.type == "attribute":
                named = node.named_children
                if (
                    len(named) == 2
                    and named[0].type == "identifier"
                    and named[0].text == b"self"
                    and named[1].type == "identifier"
                ):
                    method_name = named[1].text.decode("utf-8")
                    ref_text = f"self.{method_name}"
                    if ref_text not in existing_set and self._is_method_reference_context(node):
                        refs.append(ref_text)
                        existing_set.add(ref_text)
                        return
            for child in node.children:
                if child.type == "function_definition":
                    continue
                walk(child)

        walk(body_node)
        return refs

    def _is_method_reference_context(self, attr_node) -> bool:
        parent = attr_node.parent
        if parent is None:
            return False
        if parent.type == "call" and parent.children and parent.children[0] == attr_node:
            return False
        if parent.type == "attribute":
            return False
        if parent.type == "comparison_operator":
            return False
        if parent.type == "assignment" and parent.children and parent.children[0] == attr_node:
            return False
        if (
            parent.type == "augmented_assignment"
            and parent.children
            and parent.children[0] == attr_node
        ):
            return False
        if parent.type == "subscript" and parent.children and parent.children[0] == attr_node:
            return False
        current = parent
        while current:
            if current.type == "delete_statement":
                return False
            current = current.parent
        if parent.type in ("interpolation", "format_expression"):
            return False
        return True

    BUILTIN_TO_DUNDER = {
        "len": "__len__",
        "str": "__str__",
        "repr": "__repr__",
        "int": "__int__",
        "float": "__float__",
        "bool": "__bool__",
        "bytes": "__bytes__",
        "hash": "__hash__",
        "iter": "__iter__",
        "next": "__next__",
        "reversed": "__reversed__",
        "abs": "__abs__",
        "round": "__round__",
        "complex": "__complex__",
        "format": "__format__",
    }

    def _extract_builtin_dunder_calls(self, call_node) -> list:
        dunder_calls: list[str] = []
        if not call_node.children:
            return dunder_calls
        func_node = call_node.children[0]
        if func_node.type != "identifier":
            return dunder_calls
        func_name = func_node.text.decode("utf-8")
        if func_name not in self.BUILTIN_TO_DUNDER:
            return dunder_calls
        dunder_method = self.BUILTIN_TO_DUNDER[func_name]
        for child in call_node.children:
            if child.type == "argument_list":
                first_arg = self._get_first_positional_arg(child)
                if first_arg:
                    dunder_calls.append(f"{first_arg}.{dunder_method}")
                break
        return dunder_calls

    def _get_first_positional_arg(self, arg_list_node) -> str | None:
        for child in arg_list_node.children:
            if child.type in ("(", ")", ","):
                continue
            if child.type == "keyword_argument":
                continue
            if child.type in ("identifier", "attribute"):
                return child.text.decode("utf-8")
            break
        return None

    def _extract_function_references_from_call(
        self, call_node, var_types=None, param_names=None
    ) -> list:
        func_refs = []
        var_types = var_types or {}
        param_names = param_names or set()
        for child in call_node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    ref = self._extract_func_ref_from_arg(arg, var_types, param_names)
                    if ref:
                        func_refs.append(ref)
        return func_refs

    def _extract_func_ref_from_arg(self, arg_node, var_types=None, param_names=None) -> str | None:
        var_types = var_types or {}
        param_names = param_names or set()

        if arg_node.type == "identifier":
            name = arg_node.text.decode("utf-8")
            if name in var_types or name in param_names:
                return None
            if not self._is_likely_function_reference(name):
                return None
            return name

        elif arg_node.type == "attribute":
            return arg_node.text.decode("utf-8")

        elif arg_node.type == "keyword_argument":
            callback_keywords = {
                "key",
                "target",
                "func",
                "function",
                "callback",
                "handler",
                "processor",
                "factory",
                "side_effect",
                "default",
                "default_factory",
            }
            key_name = None
            value_node = None
            for child in arg_node.children:
                if child.type == "identifier" and key_name is None:
                    key_name = child.text.decode("utf-8")
                elif child.type in ("identifier", "attribute"):
                    value_node = child
            if key_name in callback_keywords and value_node:
                value_text = value_node.text.decode("utf-8")
                if value_text in var_types or value_text in param_names:
                    return None
                return value_text

        return None

    def _is_likely_function_reference(self, name: str) -> bool:
        non_function_names = {
            "self",
            "cls",
            "args",
            "kwargs",
            "data",
            "items",
            "result",
            "value",
            "values",
            "key",
            "keys",
            "item",
            "x",
            "y",
            "z",
            "i",
            "j",
            "k",
            "n",
            "m",
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
            "None",
            "True",
            "False",
            "str",
            "int",
            "float",
            "list",
            "dict",
            "set",
            "tuple",
            "bytes",
            "bool",
            "type",
            "object",
            "path",
            "file",
            "name",
            "msg",
            "message",
            "text",
            "string",
            "config",
            "options",
            "settings",
            "params",
        }
        if name in non_function_names:
            return False
        if len(name) == 1 and name.islower():
            return False
        return True

    def parse_call_node(self, call_node) -> str | None:
        if not call_node.children:
            return None
        function_expr = call_node.children[0]

        if function_expr.type == "attribute":
            for child in function_expr.children:
                if child.type == "call":
                    inner_call_text = self.parse_call_node(child)
                    if inner_call_text:
                        parts = inner_call_text.split(".")
                        base = parts[0]
                        special_calls = {"super", "cls", "type"}
                        if base.startswith("super(") or base in special_calls:
                            return function_expr.text.decode("utf-8")
                        method_name = None
                        for sibling in function_expr.children:
                            if sibling.type == "identifier":
                                method_name = sibling.text.decode("utf-8")
                        if method_name:
                            return f"{base}.{method_name}"
                    return function_expr.text.decode("utf-8")
            return function_expr.text.decode("utf-8")

        return function_expr.text.decode("utf-8")

    # ------------------------------------------------------------------
    # Variable assignment extraction (for call resolution)
    # ------------------------------------------------------------------

    def extract_variable_assignments(self, body_node) -> tuple[dict, dict]:
        var_types = {}
        var_sources = {}

        def walk(node):
            if node.type == "assignment":
                var_name, class_name = self._extract_assignment_info(node)
                if var_name and class_name:
                    var_types[var_name] = [class_name]
                else:
                    var_name, class_names = self._extract_conditional_assignment(node)
                    if var_name and class_names:
                        var_types[var_name] = class_names
                    else:
                        var_name, source = self._extract_chained_call_assignment_info(node)
                        if var_name and source:
                            var_sources[var_name] = source
            elif node.type == "with_statement":
                ctx_vars = self._extract_context_manager_vars(node)
                for k, v in ctx_vars.items():
                    var_types[k] = [v]
            elif node.type in ("for_statement", "for_in_clause"):
                var_sources.update(self._extract_for_loop_vars(node))
            for child in node.children:
                if child.type == "function_definition":
                    continue
                walk(child)

        walk(body_node)
        return var_types, var_sources

    def _extract_assignment_info(self, assignment_node) -> tuple[str | None, str | None]:
        var_name = None
        class_name = None
        for child in assignment_node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")
            elif child.type == "call":
                if child.children:
                    func_expr = child.children[0]
                    if func_expr.type == "identifier":
                        class_name = func_expr.text.decode("utf-8")
                    elif func_expr.type == "attribute":
                        is_chained = any(c.type == "call" for c in func_expr.children)
                        if not is_chained:
                            class_name = func_expr.text.decode("utf-8")
        return var_name, class_name

    def _extract_conditional_assignment(self, assignment_node) -> tuple[str | None, list | None]:
        var_name = None
        class_names = []
        for child in assignment_node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")
            elif child.type == "conditional_expression" and var_name is not None:
                parts = child.children
                if len(parts) >= 5:
                    for expr in (parts[0], parts[-1]):
                        if expr.type in ("identifier", "attribute"):
                            class_names.append(expr.text.decode("utf-8"))
        if var_name and class_names:
            return var_name, [f"@ref:{c}" for c in class_names]
        return None, None

    def _extract_chained_call_assignment_info(
        self, assignment_node
    ) -> tuple[str | None, str | None]:
        var_name = None
        source = None
        for child in assignment_node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")
            elif child.type == "call" and var_name is not None:
                if not child.children:
                    continue
                func_expr = child.children[0]
                if func_expr.type != "attribute":
                    continue
                inner_call = None
                method_name = None
                for attr_child in func_expr.children:
                    if attr_child.type == "call":
                        inner_call = attr_child
                    elif attr_child.type == "identifier":
                        method_name = attr_child.text.decode("utf-8")
                if inner_call is None or method_name is None:
                    continue
                if inner_call.children:
                    inner_func = inner_call.children[0]
                    if inner_func.type == "identifier":
                        source = f"{inner_func.text.decode('utf-8')}.{method_name}"
        return var_name, source

    def _extract_for_loop_vars(self, node) -> dict:
        var_sources = {}
        loop_var = None
        iterable_node = None
        found_in = False
        for child in node.children:
            if child.type == "identifier" and loop_var is None and not found_in:
                loop_var = child.text.decode("utf-8")
            elif child.text == b"in":
                found_in = True
            elif found_in and iterable_node is None:
                if child.type in (":", "block", "comment"):
                    continue
                iterable_node = child
                break
        if loop_var and iterable_node:
            if iterable_node.type == "identifier":
                var_sources[loop_var] = f"@iter:{iterable_node.text.decode('utf-8')}"
            elif iterable_node.type == "call" and iterable_node.children:
                raw_call = iterable_node.children[0].text.decode("utf-8")
                var_sources[loop_var] = f"@iter_call:{raw_call}"
        return var_sources

    def _extract_context_manager_vars(self, with_node) -> dict:
        var_types = {}
        for child in with_node.children:
            if child.type == "with_clause":
                for item in child.children:
                    if item.type == "with_item":
                        var_name, class_name = self._extract_with_item_info(item)
                        if var_name and class_name:
                            var_types[var_name] = class_name
            elif child.type == "with_item":
                var_name, class_name = self._extract_with_item_info(child)
                if var_name and class_name:
                    var_types[var_name] = class_name
        return var_types

    def _extract_with_item_info(self, with_item_node) -> tuple[str | None, str | None]:
        var_name = None
        class_name = None
        for child in with_item_node.children:
            if child.type == "as_pattern":
                for as_child in child.children:
                    if as_child.type == "as_pattern_target":
                        for target_child in as_child.children:
                            if target_child.type == "identifier":
                                var_name = target_child.text.decode("utf-8")
                                break
                    elif as_child.type == "call" and as_child.children:
                        func_expr = as_child.children[0]
                        if func_expr.type in ("identifier", "attribute"):
                            class_name = func_expr.text.decode("utf-8")
                    elif as_child.type == "identifier" and var_name is None:
                        var_name = as_child.text.decode("utf-8")
            elif child.type == "call" and child.children:
                func_expr = child.children[0]
                if func_expr.type in ("identifier", "attribute"):
                    class_name = func_expr.text.decode("utf-8")
            elif child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")
        return var_name, class_name

    # ------------------------------------------------------------------
    # Class extraction
    # ------------------------------------------------------------------

    def _extract_class(self, captures_dict: dict, relative_path: str) -> ClassChunk | None:
        class_node = self.get_node(captures_dict, "class")
        name_node = self.get_node(captures_dict, "class_name")
        super_class_node = self.get_node(captures_dict, "superclasses")
        if class_node is None or name_node is None:
            return None

        class_name = name_node.text.decode("utf-8")
        start_line = class_node.start_point[0] + 1
        end_line = class_node.end_point[0] + 1
        class_code = class_node.text.decode("utf-8")

        super_classes = []
        if super_class_node:
            super_classes = [c.text.decode("utf-8") for c in super_class_node.named_children]

        decorators, _, _ = self._get_function_context(class_node)

        docstring = None
        docstring_node = self.get_node(captures_dict, "docstring")
        if docstring_node:
            docstring = docstring_node.text.decode("utf-8")

        _id = (relative_path + "/" + class_name).replace("/", ".").replace(".py", "")

        return ClassChunk(
            id=_id,
            name=class_name,
            code=class_code,
            docstring=docstring,
            start_line=start_line,
            end_line=end_line,
            decorators=decorators,
            superclasses=super_classes,
            file_path=relative_path,
        )

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_imports(self, captures_dict: dict, all_modules: dict, path: str | None) -> dict:
        k, v = None, None

        if "import.module" in captures_dict:
            k = captures_dict["import.module"][0].text.decode("utf-8")
            v = k
        elif "import_from.module" in captures_dict and "import_from.name" in captures_dict:
            k = captures_dict["import_from.name"][0].text.decode("utf-8")
            v = captures_dict["import_from.module"][0].text.decode("utf-8") + "." + k
        elif "import.aliased.name" in captures_dict and "import.aliased.alias" in captures_dict:
            k = captures_dict["import.aliased.alias"][0].text.decode("utf-8")
            v = captures_dict["import.aliased.name"][0].text.decode("utf-8")
        elif (
            "import_from.module" in captures_dict
            and "import_from.aliased.name" in captures_dict
            and "import_from.aliased.alias" in captures_dict
        ):
            k = captures_dict["import_from.aliased.alias"][0].text.decode("utf-8")
            v = (
                captures_dict["import_from.module"][0].text.decode("utf-8")
                + "."
                + captures_dict["import_from.aliased.name"][0].text.decode("utf-8")
            )
        elif (
            "import_from.relative_module" in captures_dict
            and "import_from.relative_name" in captures_dict
        ):
            k = captures_dict["import_from.relative_name"][0].text.decode("utf-8")
            relative_module = captures_dict["import_from.relative_module"][0].text.decode("utf-8")
            if path:
                absolute_module = self._resolve_relative_import(relative_module, path, all_modules)
                if absolute_module:
                    v = absolute_module + "." + k
        elif (
            "import_from.relative_module" in captures_dict
            and "import_from.relative_aliased.name" in captures_dict
            and "import_from.relative_aliased.alias" in captures_dict
        ):
            k = captures_dict["import_from.relative_aliased.alias"][0].text.decode("utf-8")
            relative_module = captures_dict["import_from.relative_module"][0].text.decode("utf-8")
            original_name = captures_dict["import_from.relative_aliased.name"][0].text.decode(
                "utf-8"
            )
            if path:
                absolute_module = self._resolve_relative_import(relative_module, path, all_modules)
                if absolute_module:
                    v = absolute_module + "." + original_name

        if k is not None and v is not None and k != v:
            if v in all_modules:
                return {k: all_modules[v]}
            module = ".".join(v.split(".")[:-1])
            name = v.split(".")[-1]
            if module in all_modules:
                v = f"{all_modules[module]}.{name}"
                return {k: v}

        return {}

    def _resolve_relative_import(
        self, relative_module_text: str, current_file_path: str, all_modules: dict
    ) -> str | None:
        dot_count = 0
        for char in relative_module_text:
            if char == ".":
                dot_count += 1
            else:
                break

        relative_module_name = relative_module_text[dot_count:]
        current_module = current_file_path.replace("/", ".").replace(".py", "")
        current_parts = current_module.split(".")

        if dot_count > len(current_parts):
            return None

        parent_parts = current_parts[:-dot_count] if dot_count > 0 else current_parts[:-1]

        if relative_module_name:
            absolute_module = (
                ".".join(parent_parts) + "." + relative_module_name
                if parent_parts
                else relative_module_name
            )
        else:
            absolute_module = ".".join(parent_parts)

        return absolute_module

    # ------------------------------------------------------------------
    # Module docstring
    # ------------------------------------------------------------------

    def _extract_module_docstring(self, source: str) -> str | None:
        tree = self.parser.parse(bytes(source, "utf8"))
        for child in tree.root_node.children:
            if child.type == "comment":
                continue
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string" and sub.text is not None:
                        raw = sub.text.decode("utf-8")
                        for quote in ['"""', "'''"]:
                            if raw.startswith(quote) and raw.endswith(quote):
                                return raw[3:-3].strip()
                        for quote in ['"', "'"]:
                            if raw.startswith(quote) and raw.endswith(quote):
                                return raw[1:-1].strip()
                        return raw
            return None
        return None

    # Keep old method name as alias for any direct callers
    parse_module_docstring = _extract_module_docstring
