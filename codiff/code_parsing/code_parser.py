import os

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query, QueryCursor

from codiff.code_parsing.data_classes import ClassChunk, FunctionChunk, Parameter


def is_venv_dir(root: str, d: str) -> bool:
    """Return True if directory d should be excluded based on dynamic heuristics.

    Covers virtual environments (pyvenv.cfg) and suffix-based patterns that
    cannot be expressed as exact names (e.g. <pkg>.egg-info).
    """
    if d.endswith(".egg-info"):
        return True
    return os.path.exists(os.path.join(root, d, "pyvenv.cfg"))


class CodeParser:
    def __init__(self):
        self.extension = ".py"
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)
        self.exclude_dirs = {
            # version control
            ".git",
            # Python cache / tooling
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            # virtual environments
            "venv",
            "env",
            ".venv",
            # editors
            ".vscode",
            ".idea",
            # build outputs and distribution
            "build",
            "dist",
            "_build",
            ".eggs",
            # test / coverage outputs
            ".tox",
            "htmlcov",
            ".coverage",
            # node (full-stack repos)
            "node_modules",
        }
        self._init_queries()

    def _init_queries(self):
        self.function_query = Query(
            self.language,
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
            self.language,
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
            self.language,
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

    def get_node(self, captures_dict, capture_name):
        if capture_name not in captures_dict:
            return None
        capture = captures_dict[capture_name]
        if isinstance(capture, list):
            return capture[0] if capture else None
        return capture

    def parse_functions(self, source_code, relative_path):
        functions = []
        tree = self.parser.parse(bytes(source_code, "utf8"))
        cursor = QueryCursor(self.function_query)
        matches = cursor.matches(tree.root_node)

        for _, captures_dict in matches:
            func_chunk = self._extract_function_from_captures(captures_dict, relative_path)
            if func_chunk:
                functions.append(func_chunk)
        return functions

    def _extract_function_from_captures(self, captures_dict, relative_path):
        if "func_name" not in captures_dict or "function" not in captures_dict:
            return None

        func_node = self.get_node(captures_dict, "function")
        name_node = self.get_node(captures_dict, "func_name")
        body_node = self.get_node(captures_dict, "body")

        func_name = name_node.text.decode("utf-8")
        start_line = func_node.start_point[0] + 1
        end_line = func_node.end_point[0] + 1
        func_code = func_node.text.decode("utf-8")
        # Extract parameters using simple text parsing (more reliable)
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
            # func_code = func_code.replace(docstring, "")

        decorators, class_name, nested = self._get_function_context(func_node)

        _id = relative_path + "/" + class_name + "." if class_name else relative_path + "/"
        _id = _id + nested + "." if nested else _id
        _id = _id + func_name
        _id = _id.replace("/", ".").replace(".py", "")

        # Extract var_types FIRST so we can use it when extracting calls
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

    def _extract_parameters_from_node(self, params_node):
        parameters = []

        for param in params_node.named_children:
            name, type, value = None, None, None

            if param.type == "identifier":
                name = param.text.decode("utf-8")

            elif param.type == "typed_parameter":
                name = param.named_children[0].text.decode("utf-8")
                type = param.named_children[1].text.decode("utf-8")

            elif param.type == "default_parameter":
                name = param.named_children[0].text.decode("utf-8")
                value = param.named_children[1].text.decode("utf-8")

            elif param.type == "typed_default_parameter":
                name = param.named_children[0].text.decode("utf-8")
                type = param.named_children[1].text.decode("utf-8")
                value = param.named_children[2].text.decode("utf-8")

            param_info = Parameter(name=name, type=type, value=value)
            parameters.append(param_info)

        return parameters

    def _get_function_context(self, func_node):
        max_depth = 10
        decorators = []
        nested = None
        class_name = None

        parent = func_node.parent
        if parent:
            func_index = list(parent.children).index(func_node)

            # Look backwards for decorators
            for i in range(func_index - 1, -1, -1):
                sibling = parent.children[i]
                if sibling.type == "decorator":
                    decorator_text = sibling.text.decode("utf-8").strip()
                    if decorator_text.startswith("@"):
                        decorator_text = decorator_text[1:]
                    decorators.insert(0, decorator_text)

        depth = 0
        current = func_node.parent
        while current and depth < max_depth:
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
        """
        Extract the function name from a decorator string.

        Handles:
        - @decorator -> "decorator"
        - @decorator() -> "decorator"
        - @decorator(args) -> "decorator"
        - @module.decorator -> "module.decorator"
        - @module.decorator() -> "module.decorator"

        Args:
            decorator_text: Decorator string without @ (e.g., "my_decorator(arg)")

        Returns:
            Function name to call, or None for built-in decorators
        """
        # Skip built-in decorators
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

        # Remove parentheses and arguments if present
        # e.g., "decorator(arg1, arg2)" -> "decorator"
        if "(" in decorator_text:
            func_name = decorator_text.split("(")[0].strip()
        else:
            func_name = decorator_text.strip()

        # Skip built-in decorators
        base_name = func_name.split(".")[-1]
        if base_name in builtin_decorators:
            return None

        return func_name

    def extract_calls_from_function_body(self, body_node, var_types=None, param_names=None):
        calls = []
        var_types = var_types or {}
        param_names = param_names or set()

        def walk_node_for_calls(node):
            # Walk children first (post-order) to match Python's evaluation order:
            # arguments are evaluated before the enclosing call.
            for child in node.children:
                # Skip nested function definitions — their calls belong to them, not us
                if child.type == "function_definition":
                    continue
                walk_node_for_calls(child)

            if node.type == "call":
                call_info = self.parse_call_node(node)
                if call_info:
                    calls.append(call_info)

                    # Check for PyTorch-style module calls (self.module() -> forward())
                    forward_call = self._detect_module_forward_call(call_info, var_types)
                    if forward_call:
                        calls.append(forward_call)

                # Check for builtin calls that invoke dunder methods
                # e.g., len(obj) -> obj.__len__()
                dunder_calls = self._extract_builtin_dunder_calls(node)
                calls.extend(dunder_calls)

                # Also extract function references passed as arguments
                func_refs = self._extract_function_references_from_call(
                    node, var_types, param_names
                )
                calls.extend(func_refs)

        walk_node_for_calls(body_node)

        # Second pass: detect self.method references not already captured as calls
        method_refs = self._extract_self_method_references(body_node, calls)
        calls.extend(method_refs)

        return calls

    def _extract_self_method_references(self, body_node, existing_calls):
        """
        Extract self.method references that are NOT direct calls.

        Detects patterns where self.method is used as a function reference
        (stored in a variable, placed in a tuple/list, etc.) rather than
        called directly with ().

        Args:
            body_node: The function body AST node
            existing_calls: List of already-detected call strings

        Returns:
            List of method reference strings (e.g., ['self.load_image'])
        """
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
                        return  # Don't recurse into children

            for child in node.children:
                # Skip nested function definitions
                if child.type == "function_definition":
                    continue
                walk(child)

        walk(body_node)
        return refs

    def _is_method_reference_context(self, attr_node):
        """
        Check if a self.xxx attribute node is in a context that indicates
        a method reference rather than a data access or assignment target.

        Uses a blacklist approach: returns False for known non-reference contexts.
        """
        parent = attr_node.parent
        if parent is None:
            return False

        # Already a direct call: self.method()
        if parent.type == "call" and parent.children and parent.children[0] == attr_node:
            return False

        # Part of a deeper attribute chain: self.items.append
        if parent.type == "attribute":
            return False

        # Comparison: self.cache == "disk", self.x in items, self.x is None
        if parent.type == "comparison_operator":
            return False

        # LHS of assignment: self.x = ...
        if parent.type == "assignment" and parent.children and parent.children[0] == attr_node:
            return False

        # LHS of augmented assignment: self.x += 1
        if (
            parent.type == "augmented_assignment"
            and parent.children
            and parent.children[0] == attr_node
        ):
            return False

        # Being subscripted: self.data[i]
        if parent.type == "subscript" and parent.children and parent.children[0] == attr_node:
            return False

        # Inside delete statement: del self.x
        current = parent
        while current:
            if current.type == "delete_statement":
                return False
            current = current.parent

        # Inside string interpolation: f"{self.name}"
        if parent.type in ("interpolation", "format_expression"):
            return False

        return True

    # Mapping of builtin functions to their corresponding dunder methods
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
        """
        Extract dunder method calls from builtin function calls.

        When Python calls builtins like len(obj), it actually invokes obj.__len__().
        This method detects such patterns and adds the implicit dunder call.

        Handles:
        - len(obj) -> obj.__len__
        - str(obj) -> obj.__str__
        - iter(obj) -> obj.__iter__
        - etc.

        Args:
            call_node: The call AST node

        Returns:
            List of dunder method call strings
        """
        dunder_calls: list[str] = []

        if not call_node.children:
            return dunder_calls

        # Get the function being called
        func_node = call_node.children[0]
        if func_node.type != "identifier":
            return dunder_calls

        func_name = func_node.text.decode("utf-8")

        # Check if it's a known builtin
        if func_name not in self.BUILTIN_TO_DUNDER:
            return dunder_calls

        dunder_method = self.BUILTIN_TO_DUNDER[func_name]

        # Find the argument list
        for child in call_node.children:
            if child.type == "argument_list":
                # Get the first positional argument
                first_arg = self._get_first_positional_arg(child)
                if first_arg:
                    # Add the dunder call: arg.__dunder__
                    dunder_calls.append(f"{first_arg}.{dunder_method}")
                break

        return dunder_calls

    def _get_first_positional_arg(self, arg_list_node) -> str | None:
        """
        Get the first positional argument from an argument list.

        Args:
            arg_list_node: The argument_list AST node

        Returns:
            The first positional argument as a string, or None
        """
        for child in arg_list_node.children:
            # Skip parentheses and commas
            if child.type in ("(", ")", ","):
                continue

            # Skip keyword arguments
            if child.type == "keyword_argument":
                continue

            # Handle identifiers: len(obj) -> "obj"
            if child.type == "identifier":
                return child.text.decode("utf-8")

            # Handle attribute access: len(self.items) -> "self.items"
            if child.type == "attribute":
                return child.text.decode("utf-8")

            # Handle subscript: len(data[key]) -> skip (too complex)
            # Handle call: len(get_items()) -> skip (too complex)

            # For simple cases, return the text
            if child.type in ("identifier", "attribute"):
                return child.text.decode("utf-8")

            # Found first arg but it's complex - skip
            break

        return None

    def _detect_module_forward_call(self, call_info: str, var_types: dict) -> str | None:
        """
        Detect if a call might invoke a module's forward method.

        In PyTorch, calling module(x) internally calls module.forward(x).
        This method detects such patterns and adds the implicit forward call.

        Patterns detected:
        - self.xxx() where xxx is likely a module -> self.xxx.forward
        - var() where var = SomeClass() -> var.forward

        Args:
            call_info: The parsed call string (e.g., "self.encoder")
            var_types: Dict mapping variable names to class names

        Returns:
            The forward call string if applicable, None otherwise
        """
        if not call_info:
            return None

        parts = call_info.split(".")

        # Pattern 1: self.xxx() -> might invoke self.xxx.forward
        if len(parts) == 2 and parts[0] == "self":
            attr_name = parts[1]

            # Skip patterns that look like method calls (not module attributes)
            method_prefixes = (
                "get_",
                "set_",
                "is_",
                "has_",
                "can_",
                "should_",
                "validate_",
                "process_",
                "handle_",
                "create_",
                "build_",
                "init_",
                "load_",
                "save_",
                "update_",
                "compute_",
                "calculate_",
                "run_",
                "do_",
                "make_",
                "add_",
                "remove_",
                "delete_",
                "check_",
                "parse_",
                "to_",
                "from_",
                "as_",
                "with_",
                "_",  # Private methods
            )
            if any(attr_name.startswith(p) for p in method_prefixes):
                return None

            # Skip common method name patterns
            method_names = {
                "forward",
                "backward",
                "train",
                "eval",
                "parameters",
                "state_dict",
                "load_state_dict",
                "zero_grad",
                "step",
                "call",
                "apply",
                "register",
                "reset",
                "clear",
                "close",
            }
            if attr_name in method_names:
                return None

            return f"{call_info}.forward"

        # Pattern 2: var() where var is a known class instance (not a class reference)
        if len(parts) == 1 and call_info in var_types:
            class_refs = var_types[call_info]
            # Skip class references (conditional assignments like x = A if cond else B)
            # — calling a class reference is a constructor call, not a module invocation
            if isinstance(class_refs, list) and any(c.startswith("@ref:") for c in class_refs):
                return None
            return f"{call_info}.forward"

        return None

    def _extract_function_references_from_call(self, call_node, var_types=None, param_names=None):
        """
        Extract function references passed as arguments to higher-order functions.

        Handles patterns like:
        - map(func, items) -> extracts 'func'
        - filter(is_valid, items) -> extracts 'is_valid'
        - sorted(items, key=get_key) -> extracts 'get_key'
        - Thread(target=worker) -> extracts 'worker'
        - partial(add, 5) -> extracts 'add'

        Args:
            call_node: The call AST node
            var_types: Dict of known variable types (to exclude objects from being
                      treated as function references)
            param_names: Set of function parameter names (to exclude from being
                        treated as function references)

        Returns list of function reference strings.
        """
        func_refs = []
        var_types = var_types or {}
        param_names = param_names or set()

        # Find the argument_list child
        for child in call_node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    ref = self._extract_func_ref_from_arg(arg, var_types, param_names)
                    if ref:
                        func_refs.append(ref)

        return func_refs

    def _extract_func_ref_from_arg(self, arg_node, var_types=None, param_names=None):
        """
        Extract a function reference from an argument node.

        Handles:
        - Positional: func  (identifier)
        - Positional: obj.method  (attribute)
        - Keyword: key=func, target=func

        Args:
            arg_node: The argument AST node
            var_types: Dict of known variable types (to exclude objects)
            param_names: Set of function parameter names (to exclude)

        Returns the function reference string or None.
        """
        var_types = var_types or {}
        param_names = param_names or set()

        if arg_node.type == "identifier":
            # Direct function reference: map(func, items)
            name = arg_node.text.decode("utf-8")
            # If it's a known object variable, it's not a function reference
            if name in var_types:
                return None
            # If it's a function parameter, it's not a function reference
            if name in param_names:
                return None
            # Filter out common non-function arguments
            if not self._is_likely_function_reference(name):
                return None
            return name

        elif arg_node.type == "attribute":
            # Method reference: map(obj.method, items)
            return arg_node.text.decode("utf-8")

        elif arg_node.type == "keyword_argument":
            # Keyword argument: sorted(items, key=func) or Thread(target=worker)
            key_name = None
            value_node = None

            for child in arg_node.children:
                if child.type == "identifier" and key_name is None:
                    key_name = child.text.decode("utf-8")
                elif child.type in ("identifier", "attribute"):
                    value_node = child

            # Only extract if it's a known callback-style keyword
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

            if key_name in callback_keywords and value_node:
                value_text = value_node.text.decode("utf-8")
                # If it's a known object variable, it's not a function reference
                if value_text in var_types:
                    return None
                # If it's a function parameter, it's not a function reference
                if value_text in param_names:
                    return None
                return value_text

        return None

    def _is_likely_function_reference(self, name: str) -> bool:
        """
        Heuristic to determine if an identifier is likely a function reference
        vs. a regular variable/value.

        We can't know for sure without type information, but we can filter
        obvious non-functions.
        """
        # Filter out common non-function names
        non_function_names = {
            # Common variable names
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
            # Common literals/builtins used as args
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
            # Common arg names
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
            "kwargs",
            "args",
        }

        if name in non_function_names:
            return False

        # Single letter lowercase is usually a variable
        if len(name) == 1 and name.islower():
            return False

        return True

    def extract_variable_assignments(self, body_node):
        """
        Extract variable assignments where RHS is a class instantiation,
        plus var_sources for chained-call assignments and for-loop variables.

        Finds patterns like:
        - obj = ClassName()
        - obj = module.ClassName()
        - with ClassName() as obj  (context manager)
        - x = ClassName().method()  (chained call -> var_sources)
        - for x in y  (for loop -> var_sources)

        Returns: (var_types, var_sources) tuple
        - var_types: dict mapping variable names to class names
        - var_sources: dict mapping variable names to source expressions
        """
        var_types = {}
        var_sources = {}

        def walk_node_for_assignments(node):
            if node.type == "assignment":
                var_name, class_name = self._extract_assignment_info(node)
                if var_name and class_name:
                    # Last assignment wins (handles reassignment)
                    var_types[var_name] = [class_name]
                else:
                    # Try conditional assignment: x = A if cond else B
                    var_name, class_names = self._extract_conditional_assignment(node)
                    if var_name and class_names:
                        var_types[var_name] = class_names
                    else:
                        # Try chained-call assignment: x = ClassName().method()
                        var_name, source = self._extract_chained_call_assignment_info(node)
                        if var_name and source:
                            var_sources[var_name] = source

            elif node.type == "with_statement":
                # Extract context manager variable types
                # e.g., with ClassName() as var: ...
                ctx_vars = self._extract_context_manager_vars(node)
                for k, v in ctx_vars.items():
                    var_types[k] = [v]

            elif node.type in ("for_statement", "for_in_clause"):
                loop_vars = self._extract_for_loop_vars(node)
                var_sources.update(loop_vars)

            for child in node.children:
                # Skip nested function definitions
                if child.type == "function_definition":
                    continue
                walk_node_for_assignments(child)

        walk_node_for_assignments(body_node)
        return var_types, var_sources

    def _extract_chained_call_assignment_info(self, assignment_node):
        """
        Extract variable name and source expression from a chained-call assignment.

        Detects: x = ClassName().method() or x = ClassName(...).method()
        AST structure:
          assignment
            identifier (x)
            =
            call (outer)
              attribute
                call (inner: ClassName())
                  identifier (ClassName)
                  argument_list
                identifier (method)
              argument_list

        Returns: (var_name, "ClassName.method") or (None, None)
        """
        var_name = None
        source = None

        for child in assignment_node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")

            elif child.type == "call" and var_name is not None:
                # Check if this is a chained call: something.method()
                if not child.children:
                    continue
                func_expr = child.children[0]
                if func_expr.type != "attribute":
                    continue

                # func_expr is attribute node with children: [object, ".", identifier]
                inner_call = None
                method_name = None
                for attr_child in func_expr.children:
                    if attr_child.type == "call":
                        inner_call = attr_child
                    elif attr_child.type == "identifier":
                        method_name = attr_child.text.decode("utf-8")

                if inner_call is None or method_name is None:
                    continue

                # Get the class name from the inner call
                if inner_call.children:
                    inner_func = inner_call.children[0]
                    if inner_func.type == "identifier":
                        class_name = inner_func.text.decode("utf-8")
                        source = f"{class_name}.{method_name}"

        return var_name, source

    def _extract_for_loop_vars(self, node):
        """
        Extract for-loop variable types from for_statement and for_in_clause nodes.

        Detects:
        - for x in y: ...          -> {"x": "@iter:y"}
        - for x in func(): ...     -> {"x": "@iter_call:func"}
        - for x in obj.method(): . -> {"x": "@iter_call:obj.method"}
        - [... for x in y]         -> {"x": "@iter:y"}

        Returns: dict mapping loop variable names to source expressions
        """
        var_sources = {}

        # for_statement: for <var> in <iterable>: <body>
        # for_in_clause: for <var> in <iterable>  (comprehension)
        # Children layout: "for", identifier, "in", expression, ...

        loop_var = None
        iterable_node = None
        found_in = False

        for child in node.children:
            if child.type == "identifier" and loop_var is None and not found_in:
                loop_var = child.text.decode("utf-8")
            elif child.text == b"in":
                found_in = True
            elif found_in and iterable_node is None:
                # Skip colon and body
                if child.type in (":", "block", "comment"):
                    continue
                iterable_node = child
                break

        if loop_var and iterable_node:
            if iterable_node.type == "identifier":
                var_sources[loop_var] = f"@iter:{iterable_node.text.decode('utf-8')}"
            elif iterable_node.type == "call":
                # Extract the call expression text (e.g., "func" or "obj.method")
                if iterable_node.children:
                    call_func = iterable_node.children[0]
                    raw_call = call_func.text.decode("utf-8")
                    var_sources[loop_var] = f"@iter_call:{raw_call}"

        return var_sources

    def _extract_context_manager_vars(self, with_node):
        """
        Extract variable types from context manager statements.

        Handles:
        - with ClassName() as var: ...
        - with module.ClassName() as var: ...
        - with A() as a, B() as b: ...

        Returns: dict mapping variable names to class names
        """
        var_types = {}

        for child in with_node.children:
            if child.type == "with_clause":
                # Process each with_item in the clause
                for item in child.children:
                    if item.type == "with_item":
                        var_name, class_name = self._extract_with_item_info(item)
                        if var_name and class_name:
                            var_types[var_name] = class_name

            elif child.type == "with_item":
                # Older tree-sitter format
                var_name, class_name = self._extract_with_item_info(child)
                if var_name and class_name:
                    var_types[var_name] = class_name

        return var_types

    def _extract_with_item_info(self, with_item_node):
        """
        Extract variable name and class name from a with_item node.

        Handles: with ClassName() as var
        The AST structure is:
          with_item
            as_pattern
              call (ClassName())
              as_pattern_target
                identifier (var)

        Returns: (var_name, class_name) or (None, None)
        """
        var_name = None
        class_name = None

        for child in with_item_node.children:
            if child.type == "as_pattern":
                # as_pattern contains both the call and the variable
                for as_child in child.children:
                    if as_child.type == "as_pattern_target":
                        # Get variable name from as_pattern_target
                        for target_child in as_child.children:
                            if target_child.type == "identifier":
                                var_name = target_child.text.decode("utf-8")
                                break

                    elif as_child.type == "call":
                        # Get class name from the call
                        if as_child.children:
                            func_expr = as_child.children[0]
                            if func_expr.type == "identifier":
                                class_name = func_expr.text.decode("utf-8")
                            elif func_expr.type == "attribute":
                                class_name = func_expr.text.decode("utf-8")

                    elif as_child.type == "identifier":
                        # Fallback: direct identifier
                        if var_name is None:
                            var_name = as_child.text.decode("utf-8")

            elif child.type == "call":
                # Direct call without as_pattern wrapper
                if child.children:
                    func_expr = child.children[0]
                    if func_expr.type == "identifier":
                        class_name = func_expr.text.decode("utf-8")
                    elif func_expr.type == "attribute":
                        class_name = func_expr.text.decode("utf-8")

            elif child.type == "identifier" and var_name is None:
                # Direct identifier after "as" keyword
                var_name = child.text.decode("utf-8")

        return var_name, class_name

    def _extract_assignment_info(self, assignment_node):
        """
        Extract variable name and class name from an assignment node.

        Handles:
        - obj = ClassName()
        - obj = module.ClassName()

        Returns: (var_name, class_name) or (None, None) if not a class instantiation
        """
        var_name = None
        class_name = None

        for child in assignment_node.children:
            # Left side: variable name (identifier)
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")

            # Right side: call expression
            elif child.type == "call":
                # Get the function being called
                if child.children:
                    func_expr = child.children[0]

                    if func_expr.type == "identifier":
                        # Direct class: obj = ClassName()
                        class_name = func_expr.text.decode("utf-8")

                    elif func_expr.type == "attribute":
                        # Check if this is a chained call (attribute's object is a call)
                        # e.g., ClassName().method() — skip, handled by chained call extractor
                        is_chained = any(c.type == "call" for c in func_expr.children)
                        if not is_chained:
                            # Module attribute: obj = module.ClassName()
                            # Take the full text for resolution later (e.g., "module.ClassName")
                            class_name = func_expr.text.decode("utf-8")

        return var_name, class_name

    def _extract_conditional_assignment(self, assignment_node):
        """
        Extract variable name and class names from a conditional assignment.

        Handles: var = ClassA if condition else ClassB
        AST structure:
          assignment
            identifier (var)
            =
            conditional_expression
              identifier (ClassA)       <- true branch
              if
              <condition>
              else
              identifier (ClassB)       <- false branch

        Returns: (var_name, [class_names]) or (None, None)
        """
        var_name = None
        class_names = []

        for child in assignment_node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode("utf-8")

            elif child.type == "conditional_expression" and var_name is not None:
                # Extract identifiers from true and false branches
                # conditional_expression children: [true_expr, "if", condition, "else", false_expr]
                parts = child.children
                if len(parts) >= 5:
                    true_expr = parts[0]
                    false_expr = parts[-1]
                    for expr in (true_expr, false_expr):
                        if expr.type == "identifier":
                            class_names.append(expr.text.decode("utf-8"))
                        elif expr.type == "attribute":
                            class_names.append(expr.text.decode("utf-8"))

        if var_name and class_names:
            # Prefix with @ref: to indicate these are class references,
            # not instances. var() should resolve to __init__, not __call__.
            return var_name, [f"@ref:{c}" for c in class_names]
        return None, None

    def parse_call_node(self, call_node):
        """
        Extract the call target from a call node.

        Handles:
        - Simple call: func() -> "func"
        - Method call: obj.method() -> "obj.method"
        - Chained call: obj.method1().method2() -> just "obj.method2"
          (the method1 call is processed separately)
        - super() calls: super().__init__() -> "super().__init__"
          (preserved for special handling by resolver)

        The key insight is that for chained calls like a().b(), tree-sitter
        represents this as:
          call (b)
            attribute
              call (a)
              identifier: b
            argument_list

        For variable-based chains (builder.method1().method2()), we extract
        just the variable and method name. But for special calls like super(),
        we preserve the full expression.
        """
        if not call_node.children:
            return None

        function_expr = call_node.children[0]

        if function_expr.type == "attribute":
            # Check if the attribute's object is itself a call (chained call)
            for child in function_expr.children:
                if child.type == "call":
                    # This is a chained call
                    # Check if the inner call is to a special function like super()
                    inner_call_text = self.parse_call_node(child)
                    if inner_call_text:
                        parts = inner_call_text.split(".")
                        base = parts[0]

                        # Preserve full expression for special calls
                        special_calls = {"super", "cls", "type"}
                        if base.startswith("super(") or base in special_calls:
                            return function_expr.text.decode("utf-8")

                        # For regular chained calls, extract base.method
                        method_name = None
                        for sibling in function_expr.children:
                            if sibling.type == "identifier":
                                method_name = sibling.text.decode("utf-8")

                        if method_name:
                            return f"{base}.{method_name}"

                    # Fallback: return full text
                    return function_expr.text.decode("utf-8")

            # Regular attribute access (not chained)
            return function_expr.text.decode("utf-8")

        # Simple function call
        return function_expr.text.decode("utf-8")

    def parse_classes(self, source_code, relative_path):
        classes = []
        tree = self.parser.parse(bytes(source_code, "utf8"))
        cursor = QueryCursor(self.class_query)
        matches = cursor.matches(tree.root_node)

        for _, captures_dict in matches:
            class_chunk = self._extract_classes_from_captures(captures_dict, relative_path)
            if class_chunk:
                classes.append(class_chunk)
        return classes

    def _extract_classes_from_captures(self, captures_dict, relative_path):
        class_node = self.get_node(captures_dict, "class")
        name_node = self.get_node(captures_dict, "class_name")
        super_class_node = self.get_node(captures_dict, "superclasses")

        class_name = name_node.text.decode("utf-8")
        start_line = class_node.start_point[0] + 1
        end_line = class_node.end_point[0] + 1

        class_code = class_node.text.decode("utf-8")
        super_classes = []
        if super_class_node:
            super_classes = [
                child.text.decode("utf-8") for child in super_class_node.named_children
            ]

        decorators, _, _ = self._get_function_context(class_node)

        docstring = None
        docstring_node = self.get_node(captures_dict, "docstring")
        if docstring_node:
            docstring = docstring_node.text.decode("utf-8")

        _id = relative_path + "/" + class_name
        _id = _id.replace("/", ".").replace(".py", "")

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

    def parse_imports(self, source_code, all_modules, relative_path=None):
        imports = {}
        tree = self.parser.parse(bytes(source_code, "utf8"))
        cursor = QueryCursor(self.import_query)
        matches = cursor.matches(tree.root_node)

        for _, captures_dict in matches:
            module = self._extract_imports_from_captures(captures_dict, all_modules, relative_path)
            if module:
                imports.update(module)
        return imports

    def _resolve_relative_import(
        self, relative_module_text: str, current_file_path: str, all_modules: dict
    ) -> str | None:
        """
        Resolve a relative import to an absolute module path.

        Args:
            relative_module_text: The relative import text (e.g., ".utils", "..data.utils")
            current_file_path: The current file's relative path (e.g., "ultralytics/data/dataset.py")
            all_modules: Dict of all module paths

        Returns:
            Absolute module path or None if not resolvable
        """
        # Count leading dots to determine how many levels to go up
        dot_count = 0
        for char in relative_module_text:
            if char == ".":
                dot_count += 1
            else:
                break

        # Get the relative module name (after the dots)
        relative_module_name = relative_module_text[dot_count:]

        # Get the current file's directory as a module path
        # e.g., "ultralytics/data/dataset.py" -> "ultralytics.data"
        current_module = current_file_path.replace("/", ".").replace(".py", "")
        current_parts = current_module.split(".")

        # Go up 'dot_count' levels (1 dot = current package, 2 dots = parent, etc.)
        # For "from .utils import x", we stay in current package
        # For "from ..utils import x", we go to parent package
        if dot_count > len(current_parts):
            return None  # Can't go above root

        # Remove the file name and go up (dot_count - 1) more levels
        # -1 because the file itself counts as one level
        parent_parts = current_parts[:-dot_count] if dot_count > 0 else current_parts[:-1]

        # Build the absolute module path
        if relative_module_name:
            absolute_module = (
                ".".join(parent_parts) + "." + relative_module_name
                if parent_parts
                else relative_module_name
            )
        else:
            absolute_module = ".".join(parent_parts)

        return absolute_module

    def _extract_imports_from_captures(self, captures_dict, all_modules, relative_path=None):

        k, v = None, None

        # Handle absolute imports
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

        # Handle relative imports (e.g., from .utils import func)
        elif (
            "import_from.relative_module" in captures_dict
            and "import_from.relative_name" in captures_dict
        ):
            k = captures_dict["import_from.relative_name"][0].text.decode("utf-8")
            relative_module = captures_dict["import_from.relative_module"][0].text.decode("utf-8")

            if relative_path:
                absolute_module = self._resolve_relative_import(
                    relative_module, relative_path, all_modules
                )
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

            if relative_path:
                absolute_module = self._resolve_relative_import(
                    relative_module, relative_path, all_modules
                )
                if absolute_module:
                    v = absolute_module + "." + original_name

        if k is not None and v is not None and k != v:
            # First, check if the full import path exists directly
            # e.g., for "from utils import callbacks", check if "utils.callbacks" exists
            if v in all_modules:
                # The full path exists - use the resolved full path
                resolved = all_modules[v]
                return {k: resolved}

            # Fallback: resolve the module part and append the name
            # e.g., for "from utils import colorstr", resolve "utils" and append "colorstr"
            module = ".".join(v.split(".")[:-1])
            name = v.split(".")[-1]
            if module in all_modules:
                resolved_module = all_modules[module]
                # Use the full resolved module path + name
                # e.g., "utils" -> "utils.__init__" -> "utils.__init__.colorstr"
                v = f"{resolved_module}.{name}"
                return {k: v}

        return {}

    def parse_module_docstring(self, source_code: str) -> str | None:
        """
        Extract the module-level docstring from Python source code.

        A module docstring is a string literal that appears as the first
        statement in the module (before any imports or code).

        Returns:
            The cleaned docstring text (quotes stripped), or None.
        """
        tree = self.parser.parse(bytes(source_code, "utf8"))
        root = tree.root_node

        # Find the first non-comment child
        for child in root.children:
            if child.type == "comment":
                continue
            if child.type == "expression_statement":
                # Check if it contains a string node (docstring)
                for sub in child.children:
                    if sub.type == "string":
                        raw = sub.text.decode("utf-8")
                        # Strip triple quotes or single quotes
                        for quote in ['"""', "'''"]:
                            if raw.startswith(quote) and raw.endswith(quote):
                                return raw[3:-3].strip()
                        # Single-line string quotes
                        for quote in ['"', "'"]:
                            if raw.startswith(quote) and raw.endswith(quote):
                                return raw[1:-1].strip()
                        return raw
            # First non-comment statement is not a docstring
            return None

        return None

    def parse_code(self, source_code: str, relative_path: str, all_modules: dict):
        """Parse Python source code and extract function chunks"""
        classes = self.parse_classes(source_code, relative_path)
        functions = self.parse_functions(source_code, relative_path)
        imports = self.parse_imports(source_code, all_modules, relative_path)
        module_docstring = self.parse_module_docstring(source_code)

        return functions, classes, imports, module_docstring
