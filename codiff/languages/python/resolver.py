"""Python-specific call resolver.

PythonCallResolver extends BaseCallResolver with:
  - C3 MRO linearisation (Python's multiple-inheritance algorithm)
  - super().__init__() resolution with parent self-call inlining
  - cls.xxx() / cls() resolution for classmethods

The module-level resolve_internal_calls() function is kept for
backward compatibility and uses PythonCallResolver.
"""

from typing import Dict, List

from codiff.languages.resolver import BaseCallResolver


class PythonCallResolver(BaseCallResolver):
    # ------------------------------------------------------------------
    # BaseCallResolver abstract interface
    # ------------------------------------------------------------------

    @property
    def instance_keyword(self) -> str:
        return "self"

    @property
    def constructor_method_name(self) -> str:
        return "__init__"

    # ------------------------------------------------------------------
    # MRO (C3 linearisation)
    # ------------------------------------------------------------------

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
                if not any(head in s[1:] for s in sequences):
                    candidate = head
                    break
            if candidate is None:
                raise ValueError("Inconsistent hierarchy")
            result.append(candidate)
            for seq in sequences:
                if seq and seq[0] == candidate:
                    seq.pop(0)

    # ------------------------------------------------------------------
    # super() resolution with parent self-call inlining
    # ------------------------------------------------------------------

    def _resolve_super_call(self, parts: List[str], function) -> List[str]:
        """Resolve super().__init__() / super().method() calls.

        Also inlines the parent's self.xxx() calls re-resolved from the
        child's MRO so that the child gets direct edges to overriding methods.
        """
        if not function.class_name or len(parts) < 2:
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

    # ------------------------------------------------------------------
    # cls.xxx() / cls() resolution (Python classmethod pattern)
    # ------------------------------------------------------------------

    def _resolve_cls_call(self, parts: List[str], function) -> str | None:
        if not function.class_name:
            return None

        if parts[0].startswith("cls("):
            if len(parts) >= 2:
                resolved = self._find_method_in_mro(function.class_name, parts[1])
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
            file_path_key = self._file_path_to_module_key(function.file_path)
            if file_path_key in self.modules_dict:
                module_path = self.modules_dict[file_path_key]
                return f"{module_path}.{function.class_name}.{method_name}"
        return None


# Backward-compatible alias
CallResolver = PythonCallResolver


def resolve_internal_calls(
    functions: List,
    classes: List,
    imports: Dict[str, str],
    modules_dict: Dict[str, str],
    package_exports: Dict[str, str] | None = None,
    max_workers: int = 10,
) -> List:
    resolver = PythonCallResolver(functions, classes, imports, modules_dict, package_exports)
    return resolver.resolve_all_calls(max_workers=max_workers)
