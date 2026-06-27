"""Language-agnostic base class for call resolvers.

Concrete subclasses supply:
  - instance_keyword   ('self' for Python, 'this' for TypeScript)
  - constructor_method_name ('__init__' for Python, 'constructor' for TypeScript)
  - _compute_mro()     (C3 for Python, linear for TypeScript)
  - _resolve_super_call()  (language-specific super() handling)

All resolution strategies that don't depend on language-specific semantics
live here: import resolution, internal resolution, variable method calls,
return-type-based resolution, and the method-name fallback.
"""

import math
import os
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List


class BaseCallResolver(ABC):
    """Resolves raw call strings to fully-qualified function_id strings."""

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

        self.all_function_names = {func.name for func in functions}
        self.all_class_names = {cls.name for cls in classes}
        self.all_internals = self.all_function_names.union(self.all_class_names)

        self.class_id_map = {cls.name: cls.id for cls in classes}
        self.all_function_ids = {func.id for func in functions}

        self.method_name_to_ids: Dict[str, List[str]] = {}
        for func in functions:
            if func.name not in self.method_name_to_ids:
                self.method_name_to_ids[func.name] = []
            self.method_name_to_ids[func.name].append(func.id)

        self.self_calls_by_func_id: Dict[str, List[str]] = {}
        instance_kw = self.instance_keyword
        for func in functions:
            if func.calls:
                instance_methods = []
                for call in func.calls:
                    parts = call.split(".")
                    if parts[0] == instance_kw and len(parts) == 2:
                        instance_methods.append(parts[1])
                if instance_methods:
                    self.self_calls_by_func_id[func.id] = instance_methods

        self.return_type_map = {func.id: func.return_type for func in functions if func.return_type}

        self._mro_cache: Dict[str, List[str]] = {}
        self._mro_tls = threading.local()

        self.subclass_map: Dict[str, set] = {cls.name: set() for cls in classes}
        for cls in classes:
            for superclass_raw in cls.superclasses or []:
                parent = self._clean_superclass_name(superclass_raw)
                if parent in self.subclass_map:
                    self.subclass_map[parent].add(cls.name)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def instance_keyword(self) -> str:
        """Keyword used to refer to the current instance ('self' or 'this')."""

    @property
    @abstractmethod
    def constructor_method_name(self) -> str:
        """Name of the constructor method ('__init__' or 'constructor')."""

    @abstractmethod
    def _compute_mro(self, class_name: str) -> List[str]:
        """Return the MRO list for *class_name* (most-derived first)."""

    @abstractmethod
    def _resolve_super_call(self, parts: List[str], function) -> List[str]:
        """Resolve a super()/super.xxx() call to fully-qualified IDs."""

    # ------------------------------------------------------------------
    # Language-agnostic hooks with sensible defaults
    # ------------------------------------------------------------------

    def _resolve_cls_call(self, parts: List[str], function) -> str | None:
        """Resolve cls.xxx() calls. Default: no-op (Python-specific behaviour)."""
        return None

    # Subclasses may override this set with language-specific decorator names
    BUILTIN_DECORATORS: set[str] = {
        "staticmethod",
        "classmethod",
        "property",
        "abstractmethod",
        "dataclass",
        "contextmanager",
        "lru_cache",
        "cached_property",
    }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def resolve_all_calls(self, max_workers: int = 10, resolve_subset: List | None = None) -> List:
        to_resolve = resolve_subset if resolve_subset is not None else self.functions
        if not to_resolve:
            return []
        n_batches = max(1, max_workers * 4)
        batch_size = math.ceil(len(to_resolve) / n_batches)
        batches = [to_resolve[i : i + batch_size] for i in range(0, len(to_resolve), batch_size)]
        resolved: List = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._resolve_batch, batch) for batch in batches]
            for future in as_completed(futures):
                resolved.extend(future.result())
        self._resolve_decorator_calls(resolved)
        return resolved

    def _resolve_batch(self, functions: List) -> List:
        return [self._resolve_function_calls(f) for f in functions]

    # ------------------------------------------------------------------
    # Decorator resolution
    # ------------------------------------------------------------------

    def _resolve_decorator_calls(self, resolved_functions: List) -> None:
        """Add decorated-function call edges to their decorator functions."""
        func_by_id = {f.id: f for f in resolved_functions}
        for func in resolved_functions:
            if not func.decorators:
                continue
            resolved_dec_ids = []
            for dec_text in func.decorators:
                dec_name = dec_text.split("(")[0].strip() if "(" in dec_text else dec_text.strip()
                if dec_name.split(".")[-1] in self.BUILTIN_DECORATORS:
                    continue
                dec_ids = self._resolve_single_call(dec_name, func)
                if dec_ids:
                    resolved_dec_ids.append(dec_ids[0])
            if not resolved_dec_ids:
                continue
            chain = resolved_dec_ids + [func.id]
            for i in range(len(chain) - 1):
                caller_id, callee_id = chain[i], chain[i + 1]
                if caller_id in func_by_id:
                    caller_func = func_by_id[caller_id]
                    if caller_func.calls is None:
                        caller_func.calls = []
                    if callee_id not in caller_func.calls:
                        caller_func.calls.append(callee_id)

    # ------------------------------------------------------------------
    # Per-function resolution
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Return-type helpers (used by var_sources resolution)
    # ------------------------------------------------------------------

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
            parts = BaseCallResolver._split_generic_params(inner)
            return BaseCallResolver._extract_class_from_generic_type(parts[0]) if parts else None
        params = BaseCallResolver._split_generic_params(inner)
        if len(params) == 1:
            return BaseCallResolver._extract_class_from_generic_type(params[0])
        if wrapper in ("Union", "Optional") and len(params) == 2:
            if params[1].strip() == "None":
                return BaseCallResolver._extract_class_from_generic_type(params[0])
            if params[0].strip() == "None":
                return BaseCallResolver._extract_class_from_generic_type(params[1])
        return None

    @staticmethod
    def _split_generic_params(inner: str) -> list:
        parts, depth, current = [], 0, []
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
            return self._get_return_type_of_source(source_expr[11:], function)
        elif source_expr.startswith("@iter:"):
            return None
        else:
            return self._get_return_type_of_source(source_expr, function)

    # ------------------------------------------------------------------
    # Core resolution dispatcher
    # ------------------------------------------------------------------

    def _resolve_single_call(self, call: str, function, _resolving_source=False) -> List[str]:
        parts = call.split(".")
        first_part = parts[0]

        if first_part.startswith("super"):
            return self._resolve_super_call(parts, function)

        if first_part == self.instance_keyword:
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

    # ------------------------------------------------------------------
    # Class hierarchy helpers
    # ------------------------------------------------------------------

    def _expand_class_hierarchy(self, resolved_call: str) -> List[str]:
        last_part = resolved_call.split(".")[-1]
        if last_part not in self.all_class_names:
            return [resolved_call]
        init_call = self._find_init_for_class(last_part)
        return [init_call] if init_call else []

    def _find_init_for_class(self, class_name: str) -> str | None:
        return self._find_method_in_mro(class_name, self.constructor_method_name)

    def _class_has_method(self, class_name: str, method_name: str) -> bool:
        class_full_id = self.class_id_map.get(class_name)
        if not class_full_id:
            return False
        return f"{class_full_id}.{method_name}" in self.all_function_ids

    def _find_method_in_mro(self, class_name: str, method_name: str) -> str | None:
        for mro_class in self._compute_mro(class_name):
            if mro_class not in self.class_id_map:
                continue
            if self._class_has_method(mro_class, method_name):
                return f"{self.class_id_map[mro_class]}.{method_name}"
        return None

    def _resolve_method_for_class(self, class_name: str, parts: list):
        if class_name not in self.class_id_map:
            return None
        class_full_id = self.class_id_map[class_name]
        if len(parts) == 1:
            resolved = self._find_method_in_mro(class_name, "__call__")
            return resolved if resolved else f"{class_full_id}.__call__"
        method_name = parts[1]
        resolved = self._find_method_in_mro(class_name, method_name)
        return resolved if resolved else f"{class_full_id}.{method_name}"

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

    # ------------------------------------------------------------------
    # Specific resolution strategies
    # ------------------------------------------------------------------

    def _file_path_to_module_key(self, file_path: str) -> str:
        """Convert a file path to its module lookup key (strips extension, uses dots)."""
        base, _ = os.path.splitext(file_path)
        return base.replace("/", ".")

    def _resolve_self_call(self, parts: List[str], function) -> List[str]:
        if not function.class_name:
            return []
        file_path_key = self._file_path_to_module_key(function.file_path)
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

    def _resolve_imported_call(self, parts: List[str], import_alias: str) -> str | None:
        full_import_path = self.imports[import_alias]
        if len(parts) == 1:
            resolved = full_import_path
        else:
            resolved = f"{full_import_path}.{'.'.join(parts[1:])}"
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
                return f"{real_base}.{'.'.join(remaining)}" if remaining else real_base
        return path

    def _resolve_internal_call(self, parts: List[str], function) -> str | None:
        file_path_key = self._file_path_to_module_key(function.file_path)
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
            if class_name not in self.all_class_names or class_name not in self.class_id_map:
                continue
            class_full_id = self.class_id_map[class_name]
            if len(parts) == 1:
                if is_class_ref:
                    r = self._find_method_in_mro(class_name, self.constructor_method_name)
                    resolved.append(r or f"{class_full_id}.{self.constructor_method_name}")
                else:
                    r = self._find_method_in_mro(class_name, "__call__")
                    resolved.append(r or f"{class_full_id}.__call__")
            else:
                method_name = parts[1]
                r = self._find_method_in_mro(class_name, method_name)
                resolved.append(r or f"{class_full_id}.{method_name}")
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
        return matching_ids if len(matching_ids) == 1 else []
