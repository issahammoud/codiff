"""TypeScript-specific call resolver.

TypeScriptCallResolver extends BaseCallResolver with:
  - Linear (single-inheritance) MRO — TypeScript does not support multiple inheritance
  - super() / super.method() resolution for TypeScript classes
  - 'this' as the instance keyword, 'constructor' as the constructor method name
"""

from typing import List

from codiff.languages.base_resolver import BaseCallResolver


class TypeScriptCallResolver(BaseCallResolver):
    # ------------------------------------------------------------------
    # BaseCallResolver abstract interface
    # ------------------------------------------------------------------

    @property
    def instance_keyword(self) -> str:
        return "this"

    @property
    def constructor_method_name(self) -> str:
        return "constructor"

    # ------------------------------------------------------------------
    # MRO (linear — TypeScript has single inheritance only)
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

        parent_name = self._clean_superclass_name(class_obj.superclasses[0])
        if parent_name is None:
            mro = [class_name]
            self._mro_cache[class_name] = mro
            return mro

        in_progress.add(class_name)
        try:
            parent_mro = self._compute_mro(parent_name)
        finally:
            in_progress.discard(class_name)

        # Filter current class from parent's MRO to avoid cycles
        parent_mro = [c for c in parent_mro if c != class_name]

        mro = [class_name] + parent_mro
        self._mro_cache[class_name] = mro
        return mro

    # ------------------------------------------------------------------
    # super() / super.method() resolution
    # ------------------------------------------------------------------

    def _resolve_super_call(self, parts: List[str], function) -> List[str]:
        """Resolve TypeScript super() and super.method() calls.

        super()           in constructor → parent's constructor
        super.method()    → first parent in MRO that defines method
        """
        if not function.class_name:
            return []

        class_obj = next((c for c in self.classes if c.name == function.class_name), None)
        if not class_obj or not class_obj.superclasses:
            return []

        parent_name = self._clean_superclass_name(class_obj.superclasses[0])
        if parent_name is None:
            return []

        if len(parts) == 1:
            # super() → parent constructor
            resolved = self._find_method_in_mro(parent_name, self.constructor_method_name)
            return [resolved] if resolved else []

        method_name = parts[1]
        resolved = self._find_method_in_mro(parent_name, method_name)
        return [resolved] if resolved else []
