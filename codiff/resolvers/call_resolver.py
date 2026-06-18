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
        self.method_name_to_ids: Dict[str, List[str]] = {}
        for func in functions:
            if func.name not in self.method_name_to_ids:
                self.method_name_to_ids[func.name] = []
            self.method_name_to_ids[func.name].append(func.id)

        # Build map of function_id -> list of self.method_name raw calls
        self.self_calls_by_func_id: Dict[str, List[str]] = {}
        for func in functions:
            if func.calls:
                self_methods = []
                for call in func.calls:
                    parts = call.split(".")
                    if parts[0] == "self" and len(parts) == 2:
                        self_methods.append(parts[1])
                if self_methods:
                    self.self_calls_by_func_id[func.id] = self_methods

        # Build return type map for return-type-based resolution
        self.return_type_map = {func.id: func.return_type for func in functions if func.return_type}

        # Cache for computed MROs
        self._mro_cache: Dict[str, List[str]] = {}
        # Thread-local set tracking classes currently on the MRO call stack.
        self._mro_tls = threading.local()

        # Build reverse inheritance map
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
        if not function.calls:
            function.calls = []
            return function

        resolved_calls = []

        for call in function.calls:
            resolved_call_list = self._resolve_single_call(call, function)
            if resolved_call_list:
                resolved_calls.extend(resolved_call_list)

        function.calls = resolved_calls
        return function

    @staticmethod
    def _extract_class_from_generic_type(type_str: str):
        primitives = {"int", "str", "float", "bool", "bytes", "None", "Any", "object"}

        type_str = type_str.strip()

        if type_str in primitives:
            return None

        if "[" not in type_str:
            if type_str and type_str[0].isupper() and type_str not in primitives:
                return type_str
            return None

        bracket_pos = type_str.index("[")
        wrapper = type_str[:bracket_pos].strip()
        inner = type_str[bracket_pos + 1 : -1].strip() if type_str.endswith("]") else None

        if inner is None:
            return None

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

        if wrapper == "Generator":
            parts = CallResolver._split_generic_params(inner)
            if parts:
                return CallResolver._extract_class_from_generic_type(parts[0])
            return None

        params = CallResolver._split_generic_params(inner)
        if len(params) == 1:
            return CallResolver._extract_class_from_generic_type(params[0])

        if wrapper in ("Union", "Optional") and len(params) == 2:
            if params[1].strip() == "None":
                return CallResolver._extract_class_from_generic_type(params[0])
            if params[0].strip() == "None":
                return CallResolver._extract_class_from_generic_type(params[1])

        return None

    @staticmethod
    def _split_generic_params(inner: str) -> list:
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
        resolved_list = self._resolve_single_call(raw_call, function, _resolving_source=True)
        if not resolved_list:
            return None

        for resolved_id in resolved_list:
            if resolved_id in self.return_type_map:
                return self.return_type_map[resolved_id]

        return None

    def _resolve_var_source_type(self, source_expr: str, function):
        if source_expr.startswith("@iter:"):
            iter_var = source_expr[6:]

            if function.var_sources and iter_var in function.var_sources:
                inner_source = function.var_sources[iter_var]
                return_type = self._resolve_source_to_return_type(inner_source, function)
            else:
                return None

            if not return_type:
                return None

            return self._extract_class_from_generic_type(return_type)

        elif source_expr.startswith("@iter_call:"):
            raw_call = source_expr[11:]
            return_type = self._get_return_type_of_source(raw_call, function)
            if not return_type:
                return None
            return self._extract_class_from_generic_type(return_type)

        else:
            return_type = self._get_return_type_of_source(source_expr, function)
            if not return_type:
                return None
            return self._extract_class_from_generic_type(return_type)

    def _resolve_source_to_return_type(self, source_expr: str, function):
        if source_expr.startswith("@iter_call:"):
            raw_call = source_expr[11:]
            return self._get_return_type_of_source(raw_call, function)
        elif source_expr.startswith("@iter:"):
            return None
        else:
            return self._get_return_type_of_source(source_expr, function)

    def _resolve_method_for_class(self, class_name: str, parts: list):
        if class_name not in self.class_id_map:
            return None

        class_full_id = self.class_id_map[class_name]

        if len(parts) == 1:
            resolved = self._find_method_in_mro(class_name, "__call__")
            return resolved if resolved else f"{class_full_id}.__call__"
        else:
            method_name = parts[1]
            resolved = self._find_method_in_mro(class_name, method_name)
            return resolved if resolved else f"{class_full_id}.{method_name}"

    def _resolve_single_call(self, call: str, function, _resolving_source=False) -> List[str]:
        parts = call.split(".")
        first_part = parts[0]

        if first_part.startswith("super("):
            return self._resolve_super_call(parts, function)

        if first_part == "self":
            return self._resolve_self_call(parts, function)

        if (first_part == "cls" or first_part.startswith("cls(")) and function.class_name:
            resolved = self._resolve_cls_call(parts, function)
            if resolved:
                return [resolved]

        if function.var_types and first_part in function.var_types:
            resolved_calls = self._resolve_variable_method_call(parts, function)
            if resolved_calls:
                return resolved_calls

        if not _resolving_source and function.var_sources and first_part in function.var_sources:
            class_name = self._resolve_var_source_type(function.var_sources[first_part], function)
            if class_name and class_name in self.all_class_names:
                resolved = self._resolve_method_for_class(class_name, parts)
                if resolved:
                    return [resolved]

        if first_part in self.imports:
            resolved = self._resolve_imported_call(parts, first_part)
            if resolved:
                return self._expand_class_hierarchy(resolved)
            return []

        if first_part in self.all_internals:
            resolved = self._resolve_internal_call(parts, function)
            if resolved:
                return self._expand_class_hierarchy(resolved)
            return []

        if len(parts) >= 2 and first_part not in self.imports:
            fallback = self._resolve_by_method_name_fallback(parts)
            if fallback:
                return fallback

        return []

    def _expand_class_hierarchy(self, resolved_call: str) -> List[str]:
        parts = resolved_call.split(".")
        last_part = parts[-1]

        if last_part not in self.all_class_names:
            return [resolved_call]

        init_call = self._find_init_for_class(last_part)

        return [init_call] if init_call else []

    def _find_init_for_class(self, class_name: str) -> str | None:
        return self._find_method_in_mro(class_name, "__init__")

    def _class_has_method(self, class_name: str, method_name: str) -> bool:
        class_full_id = self.class_id_map.get(class_name)
        if not class_full_id:
            return False

        expected_func_id = f"{class_full_id}.{method_name}"
        return expected_func_id in self.all_function_ids

    def _compute_mro(self, class_name: str) -> List[str]:
        if class_name in self._mro_cache:
            return self._mro_cache[class_name]

        in_progress = self._mro_tls.__dict__.setdefault("in_progress", set())
        if class_name in in_progress:
            return [class_name]

        if class_name not in self.class_id_map:
            return [class_name]

        class_obj = next((c for c in self.classes if c.name == class_name), None)
        if not class_obj or not class_obj.superclasses:
            mro = [class_name]
            self._mro_cache[class_name] = mro
            return mro

        parents = [
            n for s in class_obj.superclasses if (n := self._clean_superclass_name(s)) is not None
        ]

        in_progress.add(class_name)
        try:
            parent_mros = [self._compute_mro(p) for p in parents]
        finally:
            in_progress.discard(class_name)

        filtered_parent_mros = [[c for c in mro if c != class_name] for mro in parent_mros]
        filtered_parent_mros = [mro for mro in filtered_parent_mros if mro]

        try:
            mro = self._c3_merge([[class_name]] + filtered_parent_mros + [parents])
        except ValueError:
            mro = [class_name] + parents

        self._mro_cache[class_name] = mro
        return mro

    def _c3_merge(self, sequences: List[List[str]]) -> List[str]:
        result: list[str] = []

        sequences = [list(s) for s in sequences]

        while True:
            sequences = [s for s in sequences if s]
            if not sequences:
                return result

            candidate = None
            for seq in sequences:
                head = seq[0]
                in_tail = any(head in s[1:] for s in sequences)
                if not in_tail:
                    candidate = head
                    break

            if candidate is None:
                raise ValueError("Inconsistent hierarchy")

            result.append(candidate)

            for seq in sequences:
                if seq and seq[0] == candidate:
                    seq.pop(0)

    def _find_method_in_mro(self, class_name: str, method_name: str) -> str | None:
        mro = self._compute_mro(class_name)

        for mro_class in mro:
            if mro_class not in self.class_id_map:
                continue

            if self._class_has_method(mro_class, method_name):
                class_full_id = self.class_id_map[mro_class]
                return f"{class_full_id}.{method_name}"

        return None

    def _clean_superclass_name(self, superclass: str) -> str | None:
        if "[" in superclass:
            superclass = superclass.split("[")[0]
        return superclass.strip()

    def _resolve_superclass(self, superclass_name: str, child_class) -> str | None:
        if superclass_name in self.class_id_map:
            return self.class_id_map[superclass_name]

        if superclass_name in self.imports:
            return self.imports[superclass_name]

        child_module = ".".join(child_class.id.split(".")[:-1])
        potential_path = f"{child_module}.{superclass_name}"

        if potential_path in self.class_id_map.values():
            return potential_path

        return None

    def _resolve_self_call(self, parts: List[str], function) -> List[str]:
        if not function.class_name:
            return []

        file_path_key = function.file_path.replace("/", ".").replace(".py", "")

        if file_path_key not in self.modules_dict:
            return []

        module_path = self.modules_dict[file_path_key]

        if len(parts) == 1:
            return [f"{module_path}.{function.class_name}"]

        method_name = parts[1]

        resolved = self._find_method_in_mro(function.class_name, method_name)
        if not resolved:
            if method_name in self.all_internals:
                return [f"{module_path}.{function.class_name}.{method_name}"]
            return []

        return [resolved]

    def _resolve_cls_call(self, parts: List[str], function) -> str | None:
        if not function.class_name:
            return None

        if parts[0].startswith("cls("):
            if len(parts) >= 2:
                method_name = parts[1]
                resolved = self._find_method_in_mro(function.class_name, method_name)
                if resolved:
                    return resolved
            return self._find_init_for_class(function.class_name)

        if len(parts) == 1:
            return self._find_init_for_class(function.class_name)
        else:
            method_name = parts[1]
            resolved = self._find_method_in_mro(function.class_name, method_name)
            if resolved:
                return resolved

            file_path_key = function.file_path.replace("/", ".").replace(".py", "")
            if file_path_key in self.modules_dict:
                module_path = self.modules_dict[file_path_key]
                return f"{module_path}.{function.class_name}.{method_name}"

        return None

    def _resolve_super_call(self, parts: List[str], function) -> List[str]:
        """
        Resolve super() calls to parent class methods, inlining the parent's
        self.xxx calls re-resolved from the child's MRO.
        """
        if not function.class_name:
            return []

        if len(parts) < 2:
            return []

        method_name = parts[1]

        class_obj = next((c for c in self.classes if c.name == function.class_name), None)
        if not class_obj or not class_obj.superclasses:
            return []

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

        parent_class = resolved.rsplit(".", 1)[0]
        parent_self_methods = self.self_calls_by_func_id.get(resolved, [])
        for method in parent_self_methods:
            child_resolved = self._find_method_in_mro(function.class_name, method)
            if not child_resolved or child_resolved in results:
                continue
            parent_resolved = self._find_method_in_mro(parent_class.rsplit(".", 1)[-1], method)
            if child_resolved != parent_resolved:
                results.append(child_resolved)

        return results

    def _resolve_imported_call(self, parts: List[str], import_alias: str) -> str | None:
        full_import_path = self.imports[import_alias]

        if len(parts) == 1:
            resolved = full_import_path
        else:
            remaining_parts = parts[1:]
            resolved = f"{full_import_path}.{'.'.join(remaining_parts)}"

        return self._resolve_through_package_exports(resolved)

    def _resolve_through_package_exports(self, path: str) -> str | None:
        normalized = path.replace(".__init__.", ".").replace(".__init__", "")

        if normalized in self.package_exports:
            return self.package_exports[normalized]

        parts = normalized.split(".")
        for i in range(len(parts) - 1, 0, -1):
            potential_export = ".".join(parts[: i + 1])
            if potential_export in self.package_exports:
                real_base = self.package_exports[potential_export]
                remaining = parts[i + 1 :]
                if remaining:
                    return f"{real_base}.{'.'.join(remaining)}"
                return real_base

        return path

    def _resolve_internal_call(self, parts: List[str], function) -> str | None:
        file_path_key = function.file_path.replace("/", ".").replace(".py", "")

        if file_path_key not in self.modules_dict:
            return None

        module_path = self.modules_dict[file_path_key]

        if len(parts) == 1:
            call_name = parts[0]

            nested_path = f"{module_path}.{function.name}.{call_name}"
            if nested_path in self.all_function_ids:
                return nested_path

            if function.nested:
                sibling_path = f"{module_path}.{function.nested}.{call_name}"
                if sibling_path in self.all_function_ids:
                    return sibling_path

        return f"{module_path}.{'.'.join(parts)}"

    def _resolve_variable_method_call(self, parts: List[str], function) -> List[str]:
        var_name = parts[0]

        class_refs = function.var_types.get(var_name)
        if not class_refs:
            return []

        resolved = []
        for class_ref in class_refs:
            is_class_ref = class_ref.startswith("@ref:")
            if is_class_ref:
                class_ref = class_ref[5:]

            class_name = class_ref.split(".")[-1]

            if class_name not in self.all_class_names:
                continue

            if class_name not in self.class_id_map:
                continue

            class_full_id = self.class_id_map[class_name]

            if len(parts) == 1:
                if is_class_ref:
                    r = self._find_method_in_mro(class_name, "__init__")
                    resolved.append(r or f"{class_full_id}.__init__")
                else:
                    r = self._find_method_in_mro(class_name, "__call__")
                    resolved.append(r or f"{class_full_id}.__call__")
            else:
                method_name = parts[1]
                r = self._find_method_in_mro(class_name, method_name)
                if r:
                    resolved.append(r)
                else:
                    resolved.append(f"{class_full_id}.{method_name}")

        return resolved

    def _resolve_by_method_name_fallback(self, parts: List[str]) -> List[str]:
        method_name = parts[-1]

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

        if method_name not in self.method_name_to_ids:
            return []

        matching_ids = self.method_name_to_ids[method_name]

        if len(matching_ids) == 1:
            return matching_ids

        return []


def resolve_internal_calls(
    functions: List,
    classes: List,
    imports: Dict[str, str],
    modules_dict: Dict[str, str],
    package_exports: Dict[str, str] | None = None,
    max_workers: int = 10,
) -> List:
    resolver = CallResolver(functions, classes, imports, modules_dict, package_exports)
    return resolver.resolve_all_calls(max_workers=max_workers)
