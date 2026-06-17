"""
Call Resolver Module

Resolves raw function calls to match function_id format for call graph analysis.
Converts calls like "self.method" or "imported.func" to full qualified names.
For class instantiations, follows Python's MRO to find the correct __init__ method.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List


class CallResolver:
    """Resolves internal function calls to match function_id format"""

    def __init__(
        self,
        functions: List,
        classes: List,
        imports: Dict[str, str],
        modules_dict: Dict[str, str],
        package_exports: Dict[str, str] | None = None,
    ):
        """
        Initialize resolver with parsed data.

        Args:
            functions: List of FunctionChunk objects
            classes: List of ClassChunk objects
            imports: Dict mapping import aliases to full module paths
            modules_dict: Dict mapping file paths to module names
            package_exports: Dict mapping "package.export" -> "package.submodule.export"
                           Built from __init__.py re-exports
        """
        self.functions = functions
        self.classes = classes
        self.imports = imports
        self.modules_dict = modules_dict
        self.package_exports = package_exports or {}

        # Build lookup sets
        self.all_function_names = {func.name for func in functions}
        self.all_class_names = {cls.name for cls in classes}
        self.all_internals = self.all_function_names.union(self.all_class_names)

        # Build class name to full ID mapping for superclass resolution
        self.class_id_map = {cls.name: cls.id for cls in classes}

        # Build set of all function IDs for efficient lookup
        self.all_function_ids = {func.id for func in functions}

        # Build method name to full function IDs mapping for fallback resolution
        # This maps method names to all functions with that name (could be multiple)
        self.method_name_to_ids: Dict[str, List[str]] = {}
        for func in functions:
            if func.name not in self.method_name_to_ids:
                self.method_name_to_ids[func.name] = []
            self.method_name_to_ids[func.name].append(func.id)

        # Build map of function_id -> list of self.method_name raw calls
        # Captured before resolution so we can inline parent self calls on super()
        self.self_calls_by_func_id: Dict[str, List[str]] = {}
        for func in functions:
            if func.calls:
                self_methods = []
                for call in func.calls:
                    parts = call.split(".")
                    # Only direct self.method calls (not self.attr.method)
                    if parts[0] == "self" and len(parts) == 2:
                        self_methods.append(parts[1])
                if self_methods:
                    self.self_calls_by_func_id[func.id] = self_methods

        # Build return type map for return-type-based resolution
        self.return_type_map = {func.id: func.return_type for func in functions if func.return_type}

        # Cache for computed MROs
        self._mro_cache: Dict[str, List[str]] = {}
        # Thread-local set tracking classes currently on the MRO call stack.
        # Must be thread-local so concurrent workers don't see each other's in-progress
        # classes as false cycles.
        self._mro_tls = threading.local()

        # Build reverse inheritance map: class_name -> set of direct/indirect subclass names
        # Used to resolve downward polymorphic dispatch (self.method() in base class)
        self.subclass_map: Dict[str, set] = {cls.name: set() for cls in classes}
        for cls in classes:
            for superclass_raw in cls.superclasses or []:
                parent = self._clean_superclass_name(superclass_raw)
                if parent in self.subclass_map:
                    self.subclass_map[parent].add(cls.name)

    # Decorators to skip when resolving decorator→function relationships
    BUILTIN_DECORATORS = {
        "staticmethod",
        "classmethod",
        "property",
        "abstractmethod",
        "dataclass",
        "contextmanager",
        "lru_cache",
        "cached_property",
    }

    def resolve_all_calls(self, max_workers: int = 10) -> List:
        """
        Resolve calls for all functions in parallel.

        Returns:
            List of functions with resolved calls
        """
        resolved_functions = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._resolve_function_calls, function)
                for function in self.functions
            ]

            for future in as_completed(futures):
                resolved_function = future.result()
                resolved_functions.append(resolved_function)

        self._resolve_decorator_calls(resolved_functions)
        return resolved_functions

    def _resolve_decorator_calls(self, resolved_functions: List):
        """
        Add decorated functions to their decorators' calls lists.

        For @A @B def f (Python: A(B(f))):
        - B (innermost) calls f
        - A (outermost) calls B
        This models the runtime chain: A → B → f

        Decorators are listed outermost-first in func.decorators: [A, B].
        """
        func_by_id = {f.id: f for f in resolved_functions}

        for func in resolved_functions:
            if not func.decorators:
                continue

            # Resolve each decorator to a function_id
            resolved_dec_ids = []
            for dec_text in func.decorators:
                if "(" in dec_text:
                    dec_name = dec_text.split("(")[0].strip()
                else:
                    dec_name = dec_text.strip()

                if dec_name.split(".")[-1] in self.BUILTIN_DECORATORS:
                    continue

                dec_ids = self._resolve_single_call(dec_name, func)
                if dec_ids:
                    resolved_dec_ids.append(dec_ids[0])

            if not resolved_dec_ids:
                continue

            # Build chain: outermost → ... → innermost → function
            # decorators list is [outermost, ..., innermost]
            # Chain targets: dec[0]→dec[1], dec[1]→dec[2], ..., dec[-1]→func
            chain = resolved_dec_ids + [func.id]

            for i in range(len(chain) - 1):
                caller_id = chain[i]
                callee_id = chain[i + 1]
                if caller_id in func_by_id:
                    caller_func = func_by_id[caller_id]
                    if caller_func.calls is None:
                        caller_func.calls = []
                    if callee_id not in caller_func.calls:
                        caller_func.calls.append(callee_id)

    def _resolve_function_calls(self, function):
        """
        Resolve calls for a single function.

        Converts:
        - "self.method" -> "module.ClassName.method"
        - "imported_func" -> "external.module.imported_func"
        - "LocalClass" -> "module.LocalClass.__init__" (follows MRO if no __init__)

        Note: Parent class __init__ is NOT automatically added. It should only
        be in the calls list if super().__init__() is explicitly called.

        Args:
            function: FunctionChunk with raw calls

        Returns:
            FunctionChunk with resolved calls matching function_id format
        """
        if not function.calls:
            function.calls = []
            return function

        resolved_calls = []

        for call in function.calls:
            resolved_call_list = self._resolve_single_call(call, function)
            if resolved_call_list:
                # _resolve_single_call returns a list now (for class hierarchy)
                resolved_calls.extend(resolved_call_list)

        function.calls = resolved_calls
        return function

    @staticmethod
    def _extract_class_from_generic_type(type_str: str):
        """
        Extract the inner class name from a generic type annotation.

        Examples:
        - "Iterator[PreChunk]" -> "PreChunk"
        - "List[Foo]" -> "Foo"
        - "Optional[Bar]" -> "Bar"
        - "Generator[Yield, Send, Return]" -> "Yield" (first param)
        - "Dict[str, int]" -> None (Dict is a container, not a single class)
        - "int" -> None (primitive)
        - "str" -> None (primitive)
        - "Any" -> None

        Returns: class name string or None
        """
        primitives = {"int", "str", "float", "bool", "bytes", "None", "Any", "object"}

        type_str = type_str.strip()

        if type_str in primitives:
            return None

        # No generic wrapper - could be a bare class name
        if "[" not in type_str:
            # Return it if it looks like a class name (starts with uppercase)
            if type_str and type_str[0].isupper() and type_str not in primitives:
                return type_str
            return None

        # Extract wrapper and inner content
        bracket_pos = type_str.index("[")
        wrapper = type_str[:bracket_pos].strip()
        # Extract content between outermost [ and ]
        inner = type_str[bracket_pos + 1 : -1].strip() if type_str.endswith("]") else None

        if inner is None:
            return None

        # Skip Dict/Mapping - they map keys to values, not a single class
        skip_wrappers = {
            "Dict",
            "Mapping",
            "MutableMapping",
            "DefaultDict",
            "OrderedDict",
            "Tuple",
            "Set",
            "FrozenSet",
        }
        if wrapper in skip_wrappers:
            return None

        # For Generator[Y, S, R], take the first parameter (yield type)
        if wrapper == "Generator":
            # Split at top-level commas only
            parts = CallResolver._split_generic_params(inner)
            if parts:
                return CallResolver._extract_class_from_generic_type(parts[0])
            return None

        # For Iterator, List, Optional, Iterable, Sequence, etc. -> unwrap
        # Split at top-level commas to handle Optional[List[X]] etc.
        params = CallResolver._split_generic_params(inner)
        if len(params) == 1:
            return CallResolver._extract_class_from_generic_type(params[0])

        # Union[X, None] is Optional[X] - extract X
        if wrapper in ("Union", "Optional") and len(params) == 2:
            if params[1].strip() == "None":
                return CallResolver._extract_class_from_generic_type(params[0])
            if params[0].strip() == "None":
                return CallResolver._extract_class_from_generic_type(params[1])

        return None

    @staticmethod
    def _split_generic_params(inner: str) -> list:
        """Split generic parameters at top-level commas (respecting nested brackets)."""
        parts = []
        depth = 0
        current = []
        for ch in inner:
            if ch == "[":
                depth += 1
                current.append(ch)
            elif ch == "]":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current).strip())
        return parts

    def _get_return_type_of_source(self, raw_call: str, function):
        """
        Resolve a raw call string to a function_id, then look up its return type.

        Args:
            raw_call: Raw call string (e.g., "PreChunkCombiner.iter_combined_pre_chunks")
            function: The function context for resolution

        Returns:
            Return type string or None
        """
        resolved_list = self._resolve_single_call(raw_call, function, _resolving_source=True)
        if not resolved_list:
            return None

        for resolved_id in resolved_list:
            if resolved_id in self.return_type_map:
                return self.return_type_map[resolved_id]

        return None

    def _resolve_var_source_type(self, source_expr: str, function):
        """
        Resolve a var_sources entry to a class name.

        Handles:
        - "ClassName.method" -> resolve -> get return type -> extract class
        - "@iter:var_name" -> look up var_name's source -> get return type -> unwrap generic
        - "@iter_call:raw_call" -> resolve call -> get return type -> unwrap generic

        Args:
            source_expr: A var_sources value
            function: The function context

        Returns:
            Class name string or None
        """
        if source_expr.startswith("@iter:"):
            # Iterating over a variable - look up that variable's source
            iter_var = source_expr[6:]  # strip "@iter:"

            # Check if the iterated variable itself has a source
            if function.var_sources and iter_var in function.var_sources:
                # Resolve the source of the iterated variable
                inner_source = function.var_sources[iter_var]
                return_type = self._resolve_source_to_return_type(inner_source, function)
            else:
                return None

            if not return_type:
                return None

            # Unwrap the generic (e.g., Iterator[PreChunk] -> PreChunk)
            return self._extract_class_from_generic_type(return_type)

        elif source_expr.startswith("@iter_call:"):
            # Iterating over a call result
            raw_call = source_expr[11:]  # strip "@iter_call:"
            return_type = self._get_return_type_of_source(raw_call, function)
            if not return_type:
                return None
            return self._extract_class_from_generic_type(return_type)

        else:
            # Direct chained call source: "ClassName.method"
            return_type = self._get_return_type_of_source(source_expr, function)
            if not return_type:
                return None
            return self._extract_class_from_generic_type(return_type)

    def _resolve_source_to_return_type(self, source_expr: str, function):
        """
        Resolve a source expression to a return type string.
        Handles both direct sources and @iter_call sources.
        """
        if source_expr.startswith("@iter_call:"):
            raw_call = source_expr[11:]
            return self._get_return_type_of_source(raw_call, function)
        elif source_expr.startswith("@iter:"):
            # Nested @iter - don't recurse further to avoid complexity
            return None
        else:
            return self._get_return_type_of_source(source_expr, function)

    def _resolve_method_for_class(self, class_name: str, parts: list):
        """
        Resolve a method call given a known class name.

        Like _resolve_variable_method_call but takes class_name directly
        instead of looking it up from var_types.

        Args:
            class_name: The resolved class name
            parts: Original call parts (e.g., ["pre_chunk", "iter_chunks"])

        Returns:
            Fully qualified method ID or None
        """
        if class_name not in self.class_id_map:
            return None

        class_full_id = self.class_id_map[class_name]

        if len(parts) == 1:
            # Variable called directly: obj() -> find __call__ in MRO
            resolved = self._find_method_in_mro(class_name, "__call__")
            return resolved if resolved else f"{class_full_id}.__call__"
        else:
            method_name = parts[1]
            resolved = self._find_method_in_mro(class_name, method_name)
            return resolved if resolved else f"{class_full_id}.{method_name}"

    def _resolve_single_call(self, call: str, function, _resolving_source=False) -> List[str]:
        """
        Resolve a single call to match function_id format.

        Args:
            call: Raw call string (e.g., "self.method", "Class.func")
            function: The function making the call (for context)

        Returns:
            List of resolved calls (usually 1 element)
        """
        parts = call.split(".")
        first_part = parts[0]

        # Handle super() calls - e.g., "super().__init__" or "super(ClassName, self).method"
        if first_part.startswith("super("):
            return self._resolve_super_call(parts, function)

        # Handle self calls
        if first_part == "self":
            return self._resolve_self_call(parts, function)

        # Handle cls calls in classmethods — cls() -> __init__, cls.method() -> MRO
        # Only when function is a method (has class_name); otherwise cls might
        # be a regular variable (e.g., cls = SomeClass) handled by var_types.
        if (first_part == "cls" or first_part.startswith("cls(")) and function.class_name:
            resolved = self._resolve_cls_call(parts, function)
            if resolved:
                return [resolved]

        # Handle variable method calls (obj.method() where obj = ClassName())
        # var_types maps variable name to list of possible class names
        # (supports conditional assignments like x = A if cond else B)
        if function.var_types and first_part in function.var_types:
            resolved_calls = self._resolve_variable_method_call(parts, function)
            if resolved_calls:
                return resolved_calls

        # Handle var_sources resolution (chained calls, for-loop variables)
        if not _resolving_source and function.var_sources and first_part in function.var_sources:
            class_name = self._resolve_var_source_type(function.var_sources[first_part], function)
            if class_name and class_name in self.all_class_names:
                resolved = self._resolve_method_for_class(class_name, parts)
                if resolved:
                    return [resolved]

        # Handle imported calls
        if first_part in self.imports:
            resolved = self._resolve_imported_call(parts, first_part)
            if resolved:
                return self._expand_class_hierarchy(resolved)
            return []

        # Handle direct internal calls (Class.method or function_name)
        if first_part in self.all_internals:
            resolved = self._resolve_internal_call(parts, function)
            if resolved:
                return self._expand_class_hierarchy(resolved)
            return []

        # Fallback: Try to resolve by method name alone
        # This handles cases like: labels["instances"].convert_bbox()
        # where the object comes from a dict lookup or untyped argument
        if len(parts) >= 2 and first_part not in self.imports:
            fallback = self._resolve_by_method_name_fallback(parts)
            if fallback:
                return fallback

        return []

    def _expand_class_hierarchy(self, resolved_call: str) -> List[str]:
        """
        Handle class instantiation by finding which __init__ would be called.

        Python's Method Resolution Order (MRO):
        - If the class has its own __init__, call that
        - If not, traverse parent classes to find the first __init__

        NOTE: Parent __init__ is NOT automatically called. It's only called if
        the child explicitly calls super().__init__(). That call should be
        detected separately during call extraction.

        Args:
            resolved_call: Fully qualified name (e.g., "module.MyClass")

        Returns:
            List with single __init__ call (the one that would actually execute)
            If not a class, returns [resolved_call] unchanged.
        """
        # Get the last part (could be class name)
        parts = resolved_call.split(".")
        last_part = parts[-1]

        # Check if last part is a class name
        if last_part not in self.all_class_names:
            return [resolved_call]  # Not a class, return as-is

        # This is a class instantiation - find which __init__ is called
        init_call = self._find_init_for_class(last_part)

        return [init_call] if init_call else []

    def _find_init_for_class(self, class_name: str) -> str | None:
        """
        Find which __init__ method would be called when instantiating this class.

        Uses proper C3 linearization MRO:
        - If class has its own __init__, use that
        - Otherwise, traverse MRO until one with __init__ is found

        Args:
            class_name: Name of the class being instantiated

        Returns:
            Fully qualified __init__ function ID, or None if not found
        """
        return self._find_method_in_mro(class_name, "__init__")

    def _class_has_method(self, class_name: str, method_name: str) -> bool:
        """
        Check if a class has a specific method defined.

        Args:
            class_name: Name of the class
            method_name: Name of the method to check

        Returns:
            True if the class has this method, False otherwise
        """
        class_full_id = self.class_id_map.get(class_name)
        if not class_full_id:
            return False

        expected_func_id = f"{class_full_id}.{method_name}"
        return expected_func_id in self.all_function_ids

    def _compute_mro(self, class_name: str) -> List[str]:
        """
        Compute the Method Resolution Order using C3 linearization.

        Python uses C3 linearization for diamond inheritance:
        - D(B, C) where B(A) and C(A) gives MRO: [D, B, C, A]

        Args:
            class_name: The class to compute MRO for

        Returns:
            List of class names in MRO order
        """
        # Check cache first
        if class_name in self._mro_cache:
            return self._mro_cache[class_name]

        # Cycle detected: this class is already on the call stack in this thread
        in_progress = self._mro_tls.__dict__.setdefault("in_progress", set())
        if class_name in in_progress:
            return [class_name]

        # Base case: class not found or has no parents
        if class_name not in self.class_id_map:
            return [class_name]

        class_obj = next((c for c in self.classes if c.name == class_name), None)
        if not class_obj or not class_obj.superclasses:
            mro = [class_name]
            self._mro_cache[class_name] = mro
            return mro

        # Get parent class names (filter out None from unresolvable generics)
        parents = [
            n for s in class_obj.superclasses if (n := self._clean_superclass_name(s)) is not None
        ]

        in_progress.add(class_name)
        try:
            parent_mros = [self._compute_mro(p) for p in parents]
        finally:
            in_progress.discard(class_name)

        # Strip class_name from parent MROs: those entries are cycle-break stubs
        # and would cause C3 to order class_name after its own parent.
        filtered_parent_mros = [[c for c in mro if c != class_name] for mro in parent_mros]
        filtered_parent_mros = [mro for mro in filtered_parent_mros if mro]

        try:
            mro = self._c3_merge([[class_name]] + filtered_parent_mros + [parents])
        except ValueError:
            # Inconsistent hierarchy - fall back to simple depth-first
            mro = [class_name] + parents

        self._mro_cache[class_name] = mro
        return mro

    def _c3_merge(self, sequences: List[List[str]]) -> List[str]:
        """
        C3 linearization merge algorithm.

        Args:
            sequences: List of sequences to merge

        Returns:
            Merged MRO list

        Raises:
            ValueError: If no consistent MRO exists
        """
        result: list[str] = []

        # IMPORTANT: Copy sequences to avoid modifying cached MRO lists
        sequences = [list(s) for s in sequences]

        while True:
            # Remove empty sequences
            sequences = [s for s in sequences if s]
            if not sequences:
                return result

            # Find the first candidate that doesn't appear in the tail of any sequence
            candidate = None
            for seq in sequences:
                head = seq[0]
                # Check if head appears in tail of any sequence
                in_tail = any(head in s[1:] for s in sequences)
                if not in_tail:
                    candidate = head
                    break

            if candidate is None:
                raise ValueError("Inconsistent hierarchy")

            result.append(candidate)

            # Remove candidate from all sequences
            for seq in sequences:
                if seq and seq[0] == candidate:
                    seq.pop(0)

    def _find_method_in_mro(self, class_name: str, method_name: str) -> str | None:
        """
        Find which class in the MRO has the method.

        Uses proper C3 linearization for correct diamond inheritance handling.

        Args:
            class_name: Starting class name
            method_name: Method to find

        Returns:
            Fully qualified method ID (e.g., "module.ClassName.method"), or None
        """
        mro = self._compute_mro(class_name)

        for mro_class in mro:
            if mro_class not in self.class_id_map:
                continue

            if self._class_has_method(mro_class, method_name):
                class_full_id = self.class_id_map[mro_class]
                return f"{class_full_id}.{method_name}"

        return None

    def _clean_superclass_name(self, superclass: str) -> str | None:
        """
        Clean superclass name from generics and type hints.

        Examples:
        - "TensorSchema" -> "TensorSchema"
        - "List[str]" -> "List"
        - "BaseModel[T]" -> "BaseModel"
        """
        # Remove everything after [ (generics)
        if "[" in superclass:
            superclass = superclass.split("[")[0]

        # Strip whitespace
        return superclass.strip()

    def _resolve_superclass(self, superclass_name: str, child_class) -> str | None:
        """
        Resolve superclass name to full qualified path.

        Args:
            superclass_name: Name of parent class (e.g., "TensorSchema")
            child_class: The child class object (for context)

        Returns:
            Full qualified name (e.g., "module.TensorSchema") or None
        """
        # Check if it's a known internal class
        if superclass_name in self.class_id_map:
            # Use the class ID which is already fully qualified
            return self.class_id_map[superclass_name]

        # Check if it's in imports (from child's perspective)
        # Note: We don't have per-file import context here, so this is best-effort
        if superclass_name in self.imports:
            return self.imports[superclass_name]

        # Try to find it in the same module as the child class
        child_module = ".".join(child_class.id.split(".")[:-1])
        potential_path = f"{child_module}.{superclass_name}"

        # Verify it exists
        if potential_path in self.class_id_map.values():
            return potential_path

        # Could not resolve - superclass might be external (like built-ins)
        return None

    def _resolve_self_call(self, parts: List[str], function) -> List[str]:
        """
        Resolve self.method calls following MRO, plus downward polymorphic dispatch.

        "self.method" -> "module.ClassName.method" (if ClassName has method)
        "self.method" -> "module.ParentClass.method" (if method is inherited)

        Additionally emits edges to all known subclass overrides so that a base
        class calling self.apply_instances() is connected to Mosaic.apply_instances,
        CopyPaste.apply_instances, etc. at index time.
        """
        if not function.class_name:
            return []

        # Get module path for this function's file
        file_path_key = function.file_path.replace("/", ".").replace(".py", "")

        if file_path_key not in self.modules_dict:
            return []

        module_path = self.modules_dict[file_path_key]

        # Just "self" - refers to the class
        if len(parts) == 1:
            return [f"{module_path}.{function.class_name}"]

        method_name = parts[1]

        # Use MRO to find the actual method (could be in parent class)
        resolved = self._find_method_in_mro(function.class_name, method_name)
        if not resolved:
            # Fallback: method might be defined but not parsed (e.g., property)
            if method_name in self.all_internals:
                return [f"{module_path}.{function.class_name}.{method_name}"]
            return []

        return [resolved]

    def _resolve_cls_call(self, parts: List[str], function) -> str | None:
        """
        Resolve cls calls in classmethods.

        "cls" (bare call)        -> "module.ClassName.__init__"  (instantiation)
        "cls.method"             -> "module.ClassName.method"    (follows MRO)
        "cls().method" (chained) -> "module.ClassName.method"    (follows MRO)
        """
        if not function.class_name:
            return None

        # Normalise: "cls()" or "cls(args)" in first part means chained call
        # e.g., parts = ["cls()", "method"] — treat like cls.method
        if parts[0].startswith("cls("):
            # Chained: cls().method() — resolve the method on the instance
            if len(parts) >= 2:
                method_name = parts[1]
                resolved = self._find_method_in_mro(function.class_name, method_name)
                if resolved:
                    return resolved
            # cls() alone in chained context still means instantiation
            return self._find_init_for_class(function.class_name)

        if len(parts) == 1:
            # cls() — class instantiation, resolve to __init__
            return self._find_init_for_class(function.class_name)
        else:
            # cls.method() — resolve method via MRO
            method_name = parts[1]
            resolved = self._find_method_in_mro(function.class_name, method_name)
            if resolved:
                return resolved

            # Fallback: build qualified path
            file_path_key = function.file_path.replace("/", ".").replace(".py", "")
            if file_path_key in self.modules_dict:
                module_path = self.modules_dict[file_path_key]
                return f"{module_path}.{function.class_name}.{method_name}"

        return None

    def _resolve_super_call(self, parts: List[str], function) -> List[str]:
        """
        Resolve super() calls to parent class methods, inlining the parent's
        self.xxx calls re-resolved from the child's MRO.

        "super().__init__" -> ["module.ParentClass.__init__", "module.Child.method1", ...]

        When a child calls super().method(), the parent's method runs with
        self being the child instance. Any self.xxx() calls in the parent
        dispatch to the child's overrides. We inline those calls here so
        the call graph connects the child directly to its overridden methods.

        Args:
            parts: Call split by "." (e.g., ["super()", "__init__"])
            function: The function making the call (for context)

        Returns:
            List of resolved calls: the parent method + inlined self calls
        """
        if not function.class_name:
            return []

        if len(parts) < 2:
            return []

        method_name = parts[1]

        # Get the class object to find its superclasses
        class_obj = next((c for c in self.classes if c.name == function.class_name), None)
        if not class_obj or not class_obj.superclasses:
            return []

        # Follow MRO: check each superclass in order for the method
        resolved = None
        for superclass_name in class_obj.superclasses:
            clean_name = self._clean_superclass_name(superclass_name)
            if not clean_name:
                continue
            resolved = self._find_method_in_mro(clean_name, method_name)
            if resolved:
                break

        if not resolved:
            return []

        results = [resolved]

        # Inline parent's self.xxx calls that the child OVERRIDES.
        # e.g., BaseDataset.__init__ calls self.get_img_files() ->
        #   re-resolve from YOLODataset's MRO -> YOLODataset.get_img_files
        # Only inline when child's resolution differs from parent's (actual override).
        # Non-overridden methods are already reachable via the parent edge.
        parent_class = resolved.rsplit(".", 1)[0]  # e.g., "module.BaseDataset"
        parent_self_methods = self.self_calls_by_func_id.get(resolved, [])
        for method in parent_self_methods:
            child_resolved = self._find_method_in_mro(function.class_name, method)
            if not child_resolved or child_resolved in results:
                continue
            # Only inline if the child's MRO resolves to a different class (override)
            parent_resolved = self._find_method_in_mro(parent_class.rsplit(".", 1)[-1], method)
            if child_resolved != parent_resolved:
                results.append(child_resolved)

        return results

    def _resolve_imported_call(self, parts: List[str], import_alias: str) -> str | None:
        """
        Resolve imported function calls.

        "imported_func" -> "external.module.imported_func"
        "imported.Class.method" -> "external.module.Class.method"

        Also handles package re-exports:
        If callbacks is a package and get_default_callbacks is re-exported from callbacks.base,
        "callbacks.get_default_callbacks" -> "package.callbacks.base.get_default_callbacks"
        """
        full_import_path = self.imports[import_alias]

        if len(parts) == 1:
            # Just the import itself
            resolved = full_import_path
        else:
            # Import with additional parts
            remaining_parts = parts[1:]
            resolved = f"{full_import_path}.{'.'.join(remaining_parts)}"

        # Check if this path needs to be resolved through package exports
        # e.g., "utils.callbacks.get_default_callbacks" -> "utils.callbacks.base.get_default_callbacks"
        return self._resolve_through_package_exports(resolved)

    def _resolve_through_package_exports(self, path: str) -> str | None:
        """
        Resolve a path through package exports if needed.

        If the path refers to something re-exported from a package's __init__.py,
        resolve it to the actual location.

        Args:
            path: The initially resolved path (e.g., "utils.callbacks.__init__.get_default_callbacks")

        Returns:
            The real path if found in package_exports, otherwise the original path
        """
        # Normalize path by removing .__init__ segments for lookup
        # e.g., "utils.callbacks.__init__.func" -> "utils.callbacks.func"
        normalized = path.replace(".__init__.", ".").replace(".__init__", "")

        # Direct lookup
        if normalized in self.package_exports:
            return self.package_exports[normalized]

        # Try progressively longer prefixes to find package exports
        # e.g., for "a.b.c.func", try "a.b.c.func", then check if "a.b.c" is a package with "func" export
        parts = normalized.split(".")
        for i in range(len(parts) - 1, 0, -1):
            # Check if parts[:i] + parts[i] is in package_exports
            potential_export = ".".join(parts[: i + 1])
            if potential_export in self.package_exports:
                # Found it - replace with real path and append remaining parts
                real_base = self.package_exports[potential_export]
                remaining = parts[i + 1 :]
                if remaining:
                    return f"{real_base}.{'.'.join(remaining)}"
                return real_base

        return path

    def _resolve_internal_call(self, parts: List[str], function) -> str | None:
        """
        Resolve direct internal calls.

        "Class.method" -> "module.Class.method"
        "function_name" -> "module.function_name"
        "nested_func" -> "module.outer_func.nested_func" (if called from outer_func)
        """
        # Get module path for this function's file
        file_path_key = function.file_path.replace("/", ".").replace(".py", "")

        if file_path_key not in self.modules_dict:
            return None

        module_path = self.modules_dict[file_path_key]

        # For simple function calls (not dotted), check for nested function first
        # e.g., if calling "inner_func" from "outer_func", try "module.outer_func.inner_func"
        if len(parts) == 1:
            call_name = parts[0]

            # Try nested under current function's scope
            # The caller might be the outer function calling its nested function
            nested_path = f"{module_path}.{function.name}.{call_name}"
            if nested_path in self.all_function_ids:
                return nested_path

            # Also try if caller is itself nested (sibling nested functions)
            if function.nested:
                sibling_path = f"{module_path}.{function.nested}.{call_name}"
                if sibling_path in self.all_function_ids:
                    return sibling_path

        # Fall back to module-level resolution
        return f"{module_path}.{'.'.join(parts)}"

    def _resolve_variable_method_call(self, parts: List[str], function) -> List[str]:
        """
        Resolve method calls on instantiated objects following MRO.

        Given: obj.method() where obj = ClassName()
        Resolves: "obj.method" -> ["module.ClassName.method"]

        Given: obj() where obj = A if cond else B (conditional assignment)
        Resolves: "obj" -> ["module.A.__init__", "module.B.__init__"]

        Args:
            parts: Call split by "." (e.g., ["obj", "method"] or ["obj"])
            function: The function making the call (has var_types)

        Returns:
            List of fully qualified method calls, or empty list if unresolvable
        """
        var_name = parts[0]

        # Get the list of possible class names from var_types
        class_refs = function.var_types.get(var_name)
        if not class_refs:
            return []

        resolved = []
        for class_ref in class_refs:
            # class_ref could be:
            # - "ClassName" (instance from obj = ClassName())
            # - "module.ClassName" (dotted path instance)
            # - "@ref:ClassName" (class reference from conditional assignment)
            is_class_ref = class_ref.startswith("@ref:")
            if is_class_ref:
                class_ref = class_ref[5:]  # strip "@ref:" prefix

            # Extract the actual class name (last part of dotted path)
            class_name = class_ref.split(".")[-1]

            # Check if the class is internal
            if class_name not in self.all_class_names:
                continue

            # Get the full class ID from our class map
            if class_name not in self.class_id_map:
                continue

            class_full_id = self.class_id_map[class_name]

            if len(parts) == 1:
                if is_class_ref:
                    # Class reference called: dataset = ClassA if ... else ClassB;
                    # dataset(...) -> __init__
                    r = self._find_method_in_mro(class_name, "__init__")
                    resolved.append(r or f"{class_full_id}.__init__")
                else:
                    # Instance called: obj = ClassName(); obj() -> __call__
                    r = self._find_method_in_mro(class_name, "__call__")
                    resolved.append(r or f"{class_full_id}.__call__")
            else:
                # Method call: obj.method() -> find method in MRO
                method_name = parts[1]
                r = self._find_method_in_mro(class_name, method_name)
                if r:
                    resolved.append(r)
                else:
                    resolved.append(f"{class_full_id}.{method_name}")

        return resolved

    def _resolve_by_method_name_fallback(self, parts: List[str]) -> List[str]:
        """
        Fallback resolution: try to match method name against all known methods.

        This handles cases like:
        - labels["instances"].convert_bbox()  (dict lookup)
        - data.transform()  (untyped argument)

        Where we can't determine the object's type, but the method name
        might uniquely identify an internal function.

        Args:
            parts: Call split by "." (e.g., ["labels[\"instances\"]", "convert_bbox"])

        Returns:
            List of matching function IDs. If exactly one match, returns that.
            If multiple matches, returns all of them.
            Empty list if no matches.
        """
        # Get the method name (last part for chained calls like a.b.method)
        method_name = parts[-1]

        # Skip common built-in method names that would have too many false positives
        builtin_methods = {
            "append",
            "extend",
            "insert",
            "remove",
            "pop",
            "clear",
            "index",
            "count",
            "sort",
            "reverse",
            "copy",
            "get",
            "set",
            "keys",
            "values",
            "items",
            "update",
            "format",
            "split",
            "join",
            "strip",
            "replace",
            "find",
            "startswith",
            "endswith",
            "lower",
            "upper",
            "encode",
            "decode",
            "read",
            "write",
            "close",
            "open",
            "flush",
            "seek",
            "tell",
            "__init__",
            "__call__",
            "__str__",
            "__repr__",
            "__len__",
            "__iter__",
            "__next__",
            "__getitem__",
            "__setitem__",
            "__delitem__",
            "__contains__",
        }

        if method_name in builtin_methods:
            return []

        # Look up all functions with this method name
        if method_name not in self.method_name_to_ids:
            return []

        matching_ids = self.method_name_to_ids[method_name]

        # Only return if there's exactly ONE match (unique method name)
        # Multiple matches would create ambiguous edges in the call graph
        if len(matching_ids) == 1:
            return matching_ids

        # Multiple matches - don't resolve to avoid false call graph edges
        return []


def resolve_internal_calls(
    functions: List,
    classes: List,
    imports: Dict[str, str],
    modules_dict: Dict[str, str],
    package_exports: Dict[str, str] | None = None,
    max_workers: int = 10,
) -> List:
    """
    Convenience function to resolve all internal calls.

    Args:
        functions: List of FunctionChunk objects
        classes: List of ClassChunk objects
        imports: Import mappings
        modules_dict: File path to module name mappings
        package_exports: Package export mappings from __init__.py files
        max_workers: Number of parallel workers

    Returns:
        List of functions with resolved calls
    """
    resolver = CallResolver(functions, classes, imports, modules_dict, package_exports)
    return resolver.resolve_all_calls(max_workers=max_workers)
