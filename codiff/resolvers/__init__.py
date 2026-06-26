from codiff.resolvers.base_resolver import BaseCallResolver
from codiff.resolvers.call_resolver import CallResolver, PythonCallResolver, resolve_internal_calls
from codiff.resolvers.typescript_resolver import TypeScriptCallResolver

__all__ = [
    "BaseCallResolver",
    "PythonCallResolver",
    "CallResolver",
    "TypeScriptCallResolver",
    "resolve_internal_calls",
]
