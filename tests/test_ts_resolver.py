"""Tests for TypeScriptCallResolver.

Structure mirrors test_call_resolver.py so that Python and TypeScript
resolver behaviour can be compared side-by-side.
"""

from codiff.resolvers.typescript_resolver import TypeScriptCallResolver
from codiff.schema.parsing import ClassChunk, FunctionChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_func(
    name: str,
    file_path: str = "test.ts",
    class_name: str | None = None,
    calls: list | None = None,
    var_types: dict | None = None,
    return_type: str | None = None,
) -> FunctionChunk:
    base = file_path.replace("/", ".").rsplit(".", 1)[0]
    func_id = f"{base}.{class_name}.{name}" if class_name else f"{base}.{name}"
    return FunctionChunk(
        id=func_id,
        name=name,
        code=f"function {name}() {{}}",
        docstring=None,
        start_line=1,
        end_line=2,
        parameters=[],
        decorators=[],
        file_path=file_path,
        class_name=class_name,
        nested=None,
        return_type=return_type,
        calls=calls or [],
        var_types=var_types,
        var_sources=None,
    )


def make_class(
    name: str, file_path: str = "test.ts", superclasses: list | None = None
) -> ClassChunk:
    base = file_path.replace("/", ".").rsplit(".", 1)[0]
    return ClassChunk(
        id=f"{base}.{name}",
        name=name,
        code=f"class {name} {{}}",
        docstring=None,
        start_line=1,
        end_line=2,
        decorators=[],
        superclasses=superclasses or [],
        file_path=file_path,
    )


def resolve(functions, classes, imports=None, modules_dict=None):
    modules_dict = modules_dict or {"test": "test"}
    imports = imports or {}
    resolver = TypeScriptCallResolver(functions, classes, imports, modules_dict)
    return resolver.resolve_all_calls()


# ---------------------------------------------------------------------------
# this.method() resolution
# ---------------------------------------------------------------------------


class TestThisCallResolution:
    def test_this_method_call_resolved(self):
        functions = [
            make_func("process", class_name="Service", calls=["this.validate"]),
            make_func("validate", class_name="Service"),
        ]
        classes = [make_class("Service")]
        modules_dict = {"test": "test"}
        resolved = resolve(functions, classes, modules_dict=modules_dict)
        process = next(f for f in resolved if f.name == "process")
        assert "test.Service.validate" in process.calls

    def test_multiple_this_calls(self):
        functions = [
            make_func("run", class_name="App", calls=["this.init", "this.start", "this.stop"]),
            make_func("init", class_name="App"),
            make_func("start", class_name="App"),
            make_func("stop", class_name="App"),
        ]
        classes = [make_class("App")]
        resolved = resolve(functions, classes)
        run = next(f for f in resolved if f.name == "run")
        assert "test.App.init" in run.calls
        assert "test.App.start" in run.calls
        assert "test.App.stop" in run.calls

    def test_this_call_outside_class_not_resolved(self):
        functions = [make_func("standalone", calls=["this.method"])]
        resolved = resolve(functions, [])
        assert resolved[0].calls == []

    def test_this_call_resolves_to_parent_via_mro(self):
        """this.method() in Child resolves to Parent.method if not overridden."""
        functions = [
            make_func("caller", class_name="Child", calls=["this.parentMethod"]),
            make_func("parentMethod", class_name="Parent"),
        ]
        classes = [
            make_class("Parent"),
            make_class("Child", superclasses=["Parent"]),
        ]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.Parent.parentMethod" in caller.calls
        assert "test.Child.parentMethod" not in caller.calls

    def test_this_call_resolves_to_own_method_when_overridden(self):
        """this.method() in Child resolves to Child.method when overridden."""
        functions = [
            make_func("caller", class_name="Child", calls=["this.method"]),
            make_func("method", class_name="Child"),
            make_func("method", class_name="Parent"),
        ]
        classes = [
            make_class("Parent"),
            make_class("Child", superclasses=["Parent"]),
        ]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.Child.method" in caller.calls
        assert "test.Parent.method" not in caller.calls


# ---------------------------------------------------------------------------
# new ClassName() resolution → constructor
# ---------------------------------------------------------------------------


class TestNewExpressionResolution:
    def test_new_resolves_to_constructor(self):
        functions = [
            make_func("factory"),
            make_func("constructor", class_name="MyService"),
        ]
        classes = [make_class("MyService")]
        # Simulate: factory calls new MyService()
        functions[0].calls = ["MyService"]
        resolved = resolve(functions, classes)
        factory = next(f for f in resolved if f.name == "factory")
        assert "test.MyService.constructor" in factory.calls

    def test_new_with_no_constructor_no_edge(self):
        """Class without a constructor method → no edge generated."""
        functions = [make_func("factory", calls=["MyService"])]
        classes = [make_class("MyService")]
        resolved = resolve(functions, classes)
        factory = resolved[0]
        # MyService has no constructor method → _find_init_for_class returns None → no edge
        assert "test.MyService.constructor" not in factory.calls

    def test_var_types_method_call_resolved(self):
        """const obj = new MyService(); obj.process() → MyService.process"""
        functions = [
            make_func(
                "run",
                calls=["obj.process"],
                var_types={"obj": ["MyService"]},
            ),
            make_func("constructor", class_name="MyService"),
            make_func("process", class_name="MyService"),
        ]
        classes = [make_class("MyService")]
        resolved = resolve(functions, classes)
        run = next(f for f in resolved if f.name == "run")
        assert "test.MyService.process" in run.calls

    def test_var_types_multiple_method_calls(self):
        functions = [
            make_func(
                "run",
                calls=["svc.getUser", "svc.saveUser"],
                var_types={"svc": ["UserService"]},
            ),
            make_func("getUser", class_name="UserService"),
            make_func("saveUser", class_name="UserService"),
        ]
        classes = [make_class("UserService")]
        resolved = resolve(functions, classes)
        run = next(f for f in resolved if f.name == "run")
        assert "test.UserService.getUser" in run.calls
        assert "test.UserService.saveUser" in run.calls


# ---------------------------------------------------------------------------
# super() / super.method() resolution
# ---------------------------------------------------------------------------


class TestSuperCallResolution:
    def test_super_in_constructor_resolves_to_parent_constructor(self):
        functions = [
            make_func("constructor", class_name="Dog", calls=["super"]),
            make_func("constructor", class_name="Animal"),
        ]
        classes = [
            make_class("Animal"),
            make_class("Dog", superclasses=["Animal"]),
        ]
        resolved = resolve(functions, classes)
        dog_ctor = next(f for f in resolved if f.name == "constructor" and f.class_name == "Dog")
        assert "test.Animal.constructor" in dog_ctor.calls

    def test_super_method_resolves_to_parent(self):
        functions = [
            make_func("speak", class_name="Dog", calls=["super.speak"]),
            make_func("speak", class_name="Animal"),
        ]
        classes = [
            make_class("Animal"),
            make_class("Dog", superclasses=["Animal"]),
        ]
        resolved = resolve(functions, classes)
        dog_speak = next(f for f in resolved if f.name == "speak" and f.class_name == "Dog")
        assert "test.Animal.speak" in dog_speak.calls

    def test_super_method_follows_mro(self):
        """super.method() follows linear MRO to find first parent with method."""
        functions = [
            make_func("method", class_name="GrandParent"),
            make_func("caller", class_name="Child", calls=["super.method"]),
        ]
        classes = [
            make_class("GrandParent"),
            make_class("Parent", superclasses=["GrandParent"]),
            make_class("Child", superclasses=["Parent"]),
        ]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.GrandParent.method" in caller.calls

    def test_super_outside_class_not_resolved(self):
        functions = [make_func("standalone", calls=["super.init"])]
        resolved = resolve(functions, [])
        assert resolved[0].calls == []

    def test_super_with_no_parent_not_resolved(self):
        functions = [make_func("constructor", class_name="Orphan", calls=["super"])]
        classes = [make_class("Orphan")]
        resolved = resolve(functions, classes)
        ctor = resolved[0]
        assert ctor.calls == []


# ---------------------------------------------------------------------------
# Linear MRO (single inheritance)
# ---------------------------------------------------------------------------


class TestLinearMRO:
    def test_single_inheritance_mro(self):
        functions = [
            make_func("method", class_name="GrandParent"),
            make_func("caller", class_name="Child", calls=["this.method"]),
        ]
        classes = [
            make_class("GrandParent"),
            make_class("Parent", superclasses=["GrandParent"]),
            make_class("Child", superclasses=["Parent"]),
        ]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.GrandParent.method" in caller.calls

    def test_constructor_mro_no_own_constructor(self):
        """new Child() → searches MRO for constructor; finds Parent.constructor."""
        functions = [
            make_func("factory", calls=["Child"]),
            make_func("constructor", class_name="Parent"),
        ]
        classes = [
            make_class("Parent"),
            make_class("Child", superclasses=["Parent"]),
        ]
        resolved = resolve(functions, classes)
        factory = next(f for f in resolved if f.name == "factory")
        assert "test.Parent.constructor" in factory.calls

    def test_circular_inheritance_no_infinite_loop(self):
        """Circular extends (A → B → A) must not cause infinite recursion."""
        functions = [
            make_func("method", class_name="A"),
            make_func("caller", class_name="B", calls=["this.method"]),
        ]
        classes = [
            make_class("A", superclasses=["B"]),
            make_class("B", superclasses=["A"]),
        ]
        # Must not raise RecursionError
        resolved = resolve(functions, classes)
        assert resolved is not None


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


class TestImportResolution:
    def test_imported_function_called(self):
        functions = [
            make_func("caller", calls=["helper"]),
            make_func("helper", file_path="utils.ts"),
        ]
        modules_dict = {"test": "test", "utils": "utils"}
        imports = {"helper": "utils.helper"}
        resolved = resolve(functions, [], imports=imports, modules_dict=modules_dict)
        caller = next(f for f in resolved if f.name == "caller")
        assert "utils.helper" in caller.calls

    def test_external_import_filtered(self):
        """Calls to external (npm) modules are not resolved."""
        functions = [make_func("caller", calls=["console.log", "Math.max"])]
        resolved = resolve(functions, [])
        # No internal functions match these → filtered out
        assert "console.log" not in resolved[0].calls
        assert "Math.max" not in resolved[0].calls

    def test_cross_extension_class_visible_to_resolver(self):
        """.tsx caller can resolve a .ts class when both are in the same resolver bucket.

        Simulates the scenario fixed in code_parser.py: TypeScriptCallResolver now
        receives both .ts and .tsx functions/classes so cross-extension edges aren't lost.
        """
        # UserService lives in a .ts file; Button lives in a .tsx file.
        functions = [
            make_func("render", file_path="Button.tsx", calls=["UserService"]),
            make_func("constructor", file_path="UserService.ts", class_name="UserService"),
        ]
        classes = [make_class("UserService", file_path="UserService.ts")]
        modules_dict = {"Button": "Button", "UserService": "UserService"}
        imports = {"UserService": "UserService.UserService"}
        resolved = resolve(functions, classes, imports=imports, modules_dict=modules_dict)
        render = next(f for f in resolved if f.name == "render")
        assert "UserService.UserService.constructor" in render.calls


# ---------------------------------------------------------------------------
# Internal call resolution
# ---------------------------------------------------------------------------


class TestInternalCallResolution:
    def test_module_level_function_call(self):
        functions = [
            make_func("caller", calls=["helper"]),
            make_func("helper"),
        ]
        modules_dict = {"test": "test"}
        resolved = resolve(functions, [], modules_dict=modules_dict)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.helper" in caller.calls

    def test_static_class_method_call(self):
        functions = [
            make_func("caller", calls=["Utils.format"]),
            make_func("format", class_name="Utils"),
        ]
        classes = [make_class("Utils")]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.Utils.format" in caller.calls

    def test_external_call_filtered(self):
        functions = [make_func("caller", calls=["unknownLib.doSomething"])]
        resolved = resolve(functions, [])
        assert resolved[0].calls == []


# ---------------------------------------------------------------------------
# Method-name fallback
# ---------------------------------------------------------------------------


class TestMethodNameFallback:
    def test_unique_method_name_resolved(self):
        functions = [
            make_func("caller", calls=["obj.uniqueMethod"]),
            make_func("uniqueMethod", class_name="MyClass"),
        ]
        classes = [make_class("MyClass")]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.MyClass.uniqueMethod" in caller.calls

    def test_ambiguous_method_name_not_resolved(self):
        functions = [
            make_func("caller", calls=["obj.sharedMethod"]),
            make_func("sharedMethod", class_name="ClassA"),
            make_func("sharedMethod", class_name="ClassB"),
        ]
        classes = [make_class("ClassA"), make_class("ClassB")]
        resolved = resolve(functions, classes)
        caller = next(f for f in resolved if f.name == "caller")
        assert "test.ClassA.sharedMethod" not in caller.calls
        assert "test.ClassB.sharedMethod" not in caller.calls
