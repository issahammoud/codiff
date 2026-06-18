"""
Comprehensive tests for the CallResolver module.

Tests cover:
- Self call resolution (self.method -> ClassName.method)
- Internal call resolution (function_name, Class.method)
- Imported call resolution
- Variable method call resolution (obj.method() where obj = Class())
- Callable object resolution (obj() -> Class.__call__)
- Class hierarchy expansion (__init__ chain for inheritance)
- External call filtering
"""

import pytest

from codiff.code_parsing import CodeParser
from codiff.code_parsing.call_resolver import CallResolver, resolve_internal_calls
from codiff.code_parsing.data_classes import ClassChunk, FunctionChunk


@pytest.fixture
def parser():
    """Create a CodeParser instance for testing."""
    return CodeParser()


def create_function_chunk(
    name: str,
    file_path: str = "test.py",
    class_name: str = None,
    calls: list = None,
    var_types: dict = None,
    var_sources: dict = None,
    return_type: str = None,
) -> FunctionChunk:
    """Helper to create FunctionChunk for testing."""
    func_id = file_path.replace("/", ".").replace(".py", "")
    if class_name:
        func_id = f"{func_id}.{class_name}.{name}"
    else:
        func_id = f"{func_id}.{name}"

    return FunctionChunk(
        id=func_id,
        name=name,
        code=f"def {name}(): pass",
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
        var_sources=var_sources,
    )


def create_class_chunk(
    name: str, file_path: str = "test.py", superclasses: list = None
) -> ClassChunk:
    """Helper to create ClassChunk for testing."""
    class_id = file_path.replace("/", ".").replace(".py", "") + f".{name}"

    return ClassChunk(
        id=class_id,
        name=name,
        code=f"class {name}: pass",
        docstring=None,
        start_line=1,
        end_line=2,
        decorators=[],
        superclasses=superclasses or [],
        file_path=file_path,
    )


class TestSelfCallResolution:
    """Tests for self.method() call resolution."""

    def test_self_method_call(self, parser):
        """Test resolving self.method() to ClassName.method."""
        code = """
class MyClass:
    def caller(self):
        self.target_method()

    def target_method(self):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "module.MyClass.target_method" in caller.calls

    def test_self_call_not_in_class(self, parser):
        """Test that self calls outside class context are not resolved."""
        # This is an edge case - self outside a class shouldn't resolve
        functions = [
            create_function_chunk(
                name="standalone",
                calls=["self.method"],
                class_name=None,  # Not in a class
            )
        ]
        modules_dict = {"test": "test"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        # Should not resolve self.method without class context
        assert "self.method" not in resolved[0].calls
        assert resolved[0].calls == []

    def test_multiple_self_calls(self, parser):
        """Test resolving multiple self method calls."""
        code = """
class Service:
    def process(self):
        self.validate()
        self.transform()
        self.save()

    def validate(self): pass
    def transform(self): pass
    def save(self): pass
"""
        functions = parser.parse_functions(code, "service.py")
        classes = parser.parse_classes(code, "service.py")
        modules_dict = {"service": "service"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        process = next(f for f in resolved if f.name == "process")
        assert "service.Service.validate" in process.calls
        assert "service.Service.transform" in process.calls
        assert "service.Service.save" in process.calls


class TestInternalCallResolution:
    """Tests for internal function and class call resolution."""

    def test_function_call(self, parser):
        """Test resolving calls to module-level functions."""
        code = """
def caller():
    helper()

def helper():
    pass
"""
        functions = parser.parse_functions(code, "utils.py")
        modules_dict = {"utils": "utils"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "utils.helper" in caller.calls

    def test_class_instantiation_to_init(self, parser):
        """Test that Class() resolves to Class.__init__."""
        code = """
class MyClass:
    def __init__(self):
        pass

def create():
    obj = MyClass()
"""
        functions = parser.parse_functions(code, "factory.py")
        classes = parser.parse_classes(code, "factory.py")
        modules_dict = {"factory": "factory"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        assert "factory.MyClass.__init__" in create.calls

    def test_static_method_call(self, parser):
        """Test resolving Class.static_method() calls."""
        code = """
class Utils:
    @staticmethod
    def helper():
        pass

def caller():
    Utils.helper()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "module.Utils.helper" in caller.calls

    def test_cross_module_call_not_resolved(self, parser):
        """Test that calls to unknown modules are filtered out."""
        code = """
def caller():
    unknown_module.function()
    external_lib.method()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        caller = resolved[0]
        # External calls should be filtered out
        assert len(caller.calls) == 0


class TestImportedCallResolution:
    """Tests for imported function/class call resolution."""

    def test_imported_function_call(self, parser):
        """Test resolving calls to imported functions."""
        code = """
from utils import helper

def caller():
    helper()
"""
        functions = parser.parse_functions(code, "module.py")
        # Use a module path that matches the expected resolution
        # The import resolver uses the first part of the from-import path
        modules_dict = {"module": "module", "utils": "utils", "utils.helper": "utils.helper"}
        imports = parser.parse_imports(code, modules_dict)

        resolved = resolve_internal_calls(functions, [], imports, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # Import resolution produces package.name format
        assert "utils.helper" in caller.calls

    def test_imported_class_instantiation(self, parser):
        """Test resolving imported class instantiation."""
        code = """
from models import User

def create_user():
    user = User()
"""
        functions = parser.parse_functions(code, "service.py")
        # User class needs to be defined with proper ID matching the import path
        # We also need a function for User.__init__ to be detected
        classes = [create_class_chunk("User", "pkg/models.py")]
        # Create __init__ function for User
        user_init = create_function_chunk("__init__", "pkg/models.py", class_name="User")
        functions.append(user_init)
        modules_dict = {"service": "service", "pkg.models": "pkg.models"}
        imports = {"User": "pkg.models.User"}

        resolved = resolve_internal_calls(functions, classes, imports, modules_dict)

        create_user = next(f for f in resolved if f.name == "create_user")
        # The imported class resolves via the imports dict, then hierarchy expansion
        assert "pkg.models.User.__init__" in create_user.calls

    def test_init_py_function_resolution(self, parser):
        """Test that functions in __init__.py are resolved correctly.

        When a function is defined in utils/__init__.py:
        - Function ID is: utils.__init__.colorstr
        - Import resolves to: utils.colorstr
        - The resolver should normalize to match the actual ID.
        """
        # Function defined in __init__.py
        init_code = """
def colorstr(string):
    return string
"""
        # Code that imports and calls the function
        main_code = """
from utils import colorstr

def process():
    return colorstr("hello")
"""
        init_functions = parser.parse_functions(init_code, "utils/__init__.py")
        main_functions = parser.parse_functions(main_code, "main.py")

        modules_dict = {
            "utils": "utils.__init__",
            "utils.__init__": "utils.__init__",
            "main": "main",
        }
        imports = parser.parse_imports(main_code, modules_dict, "main.py")

        all_functions = init_functions + main_functions
        resolved = resolve_internal_calls(all_functions, [], imports, modules_dict)

        process_func = next(f for f in resolved if f.name == "process")
        # Should resolve to the __init__ path, not utils.colorstr
        assert "utils.__init__.colorstr" in process_func.calls


class TestVariableMethodCallResolution:
    """Tests for resolving method calls on instantiated objects."""

    def test_simple_variable_method_call(self, parser):
        """Test obj.method() where obj = Class()."""
        code = """
class Processor:
    def __init__(self):
        pass

    def process(self):
        pass

def run():
    proc = Processor()
    proc.process()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        run_func = next(f for f in resolved if f.name == "run")
        assert "module.Processor.__init__" in run_func.calls
        assert "module.Processor.process" in run_func.calls

    def test_multiple_variable_method_calls(self, parser):
        """Test multiple method calls on the same object."""
        code = """
class Builder:
    def __init__(self): pass
    def set_name(self): pass
    def set_value(self): pass
    def build(self): pass

def create():
    builder = Builder()
    builder.set_name()
    builder.set_value()
    builder.build()
"""
        functions = parser.parse_functions(code, "factory.py")
        classes = parser.parse_classes(code, "factory.py")
        modules_dict = {"factory": "factory"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        assert "factory.Builder.__init__" in create.calls
        assert "factory.Builder.set_name" in create.calls
        assert "factory.Builder.set_value" in create.calls
        assert "factory.Builder.build" in create.calls

    def test_multiple_objects_method_calls(self, parser):
        """Test method calls on different objects."""
        code = """
class ClassA:
    def __init__(self): pass
    def method_a(self): pass

class ClassB:
    def __init__(self): pass
    def method_b(self): pass

def run():
    a = ClassA()
    b = ClassB()
    a.method_a()
    b.method_b()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        run_func = next(f for f in resolved if f.name == "run")
        assert "module.ClassA.__init__" in run_func.calls
        assert "module.ClassB.__init__" in run_func.calls
        assert "module.ClassA.method_a" in run_func.calls
        assert "module.ClassB.method_b" in run_func.calls

    def test_external_class_variable_not_resolved(self, parser):
        """Test that method calls on external class instances are filtered."""
        code = """
def fetch():
    client = requests.Session()
    client.get("http://example.com")
"""
        functions = parser.parse_functions(code, "http_client.py")
        modules_dict = {"http_client": "http_client"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        fetch = resolved[0]
        # External class method calls should be filtered
        assert len(fetch.calls) == 0


class TestCallableResolution:
    """Tests for resolving obj() -> Class.__call__."""

    def test_callable_object(self, parser):
        """Test that obj() resolves to Class.__call__."""
        code = """
class Callable:
    def __init__(self):
        pass

    def __call__(self, x):
        return x * 2

def use():
    func = Callable()
    result = func(42)
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use")
        assert "module.Callable.__init__" in use_func.calls
        assert "module.Callable.__call__" in use_func.calls

    def test_callable_and_method_calls(self, parser):
        """Test mixing __call__ and regular method calls."""
        code = """
class Processor:
    def __init__(self):
        pass

    def __call__(self, data):
        return self.process(data)

    def process(self, data):
        return data

def run():
    proc = Processor()
    proc.process("data")  # Regular method call
    proc("more data")      # __call__
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        run_func = next(f for f in resolved if f.name == "run")
        assert "module.Processor.__init__" in run_func.calls
        assert "module.Processor.process" in run_func.calls
        assert "module.Processor.__call__" in run_func.calls


class TestConditionalAssignmentResolution:
    """Tests for resolving conditional assignments: x = A if cond else B."""

    def test_conditional_assignment_calls_both_inits(self, parser):
        """Test dataset = A if cond else B; dataset() -> both __init__."""
        code = """
class DatasetA:
    def __init__(self):
        pass

class DatasetB:
    def __init__(self):
        pass

def build(flag):
    dataset = DatasetA if flag else DatasetB
    dataset()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        build_func = next(f for f in resolved if f.name == "build")
        assert "module.DatasetA.__init__" in build_func.calls
        assert "module.DatasetB.__init__" in build_func.calls

    def test_conditional_assignment_method_call(self, parser):
        """Test dataset = A if cond else B; dataset.method() -> both methods."""
        code = """
class DatasetA:
    def __init__(self): pass
    def load(self): pass

class DatasetB:
    def __init__(self): pass
    def load(self): pass

def build(flag):
    dataset = DatasetA if flag else DatasetB
    dataset.load()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        build_func = next(f for f in resolved if f.name == "build")
        assert "module.DatasetA.load" in build_func.calls
        assert "module.DatasetB.load" in build_func.calls

    def test_conditional_assignment_one_external(self, parser):
        """Test conditional where one class is external (not in codebase)."""
        code = """
class InternalClass:
    def __init__(self):
        pass

def build(flag):
    cls = InternalClass if flag else ExternalClass
    cls()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        build_func = next(f for f in resolved if f.name == "build")
        # Only internal class should resolve
        assert "module.InternalClass.__init__" in build_func.calls

    def test_conditional_vs_instance_assignment(self, parser):
        """Test that conditional ref uses __init__ while instance uses __call__."""
        code = """
class Processor:
    def __init__(self): pass
    def __call__(self, x): return x

def run(flag):
    # Instance assignment: obj = Processor()
    proc = Processor()
    result = proc(42)  # should call __call__

    # Class reference: cls = Processor if ... else Processor
    cls = Processor if flag else Processor
    obj = cls()  # should call __init__
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        run_func = next(f for f in resolved if f.name == "run")
        assert "module.Processor.__init__" in run_func.calls
        assert "module.Processor.__call__" in run_func.calls

    def test_conditional_assignment_with_args(self, parser):
        """Test dataset = A if cond else B; dataset(arg1, arg2) -> both __init__."""
        code = """
class YOLODataset:
    def __init__(self, *args, **kwargs):
        pass

class YOLOMultiModalDataset:
    def __init__(self, *args, **kwargs):
        pass

def build_yolo_dataset(cfg, img_path, batch, data, multi_modal=False):
    dataset = YOLOMultiModalDataset if multi_modal else YOLODataset
    dataset(
        img_path=img_path,
        data=data,
        batch_size=batch,
    )
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        build_func = next(f for f in resolved if f.name == "build_yolo_dataset")
        assert "module.YOLOMultiModalDataset.__init__" in build_func.calls
        assert "module.YOLODataset.__init__" in build_func.calls
        # Class reference call is a constructor, NOT a module invocation — no .forward
        assert "module.YOLOMultiModalDataset.forward" not in build_func.calls
        assert "module.YOLODataset.forward" not in build_func.calls


class TestClassHierarchyExpansion:
    """Tests for inheritance chain and MRO __init__ resolution."""

    def test_single_inheritance_child_has_init(self, parser):
        """Test that Child() only calls Child.__init__ when child has its own __init__."""
        code = """
class Parent:
    def __init__(self):
        pass

class Child(Parent):
    def __init__(self):
        super().__init__()

def create():
    obj = Child()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        # Only Child.__init__ is called from create()
        # Parent.__init__ is called from INSIDE Child.__init__ (via super())
        assert "module.Child.__init__" in create.calls
        # Parent.__init__ should NOT be in create's calls - it's in Child.__init__'s calls
        assert "module.Parent.__init__" not in create.calls

        # Child.__init__ should have Parent.__init__ in its calls (via super())
        child_init = next(f for f in resolved if f.name == "__init__" and f.class_name == "Child")
        assert "module.Parent.__init__" in child_init.calls

    def test_single_inheritance_child_no_init(self, parser):
        """Test that Child() calls Parent.__init__ when child has no __init__ (MRO)."""
        code = """
class Parent:
    def __init__(self):
        pass

class Child(Parent):
    pass

def create():
    obj = Child()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        # Child has no __init__, so MRO finds Parent.__init__
        assert "module.Parent.__init__" in create.calls
        # Child.__init__ doesn't exist, so it shouldn't be in calls
        assert "module.Child.__init__" not in create.calls

    def test_multiple_inheritance_no_inits(self, parser):
        """Test multiple inheritance where no class has __init__."""
        code = """
class MixinA:
    pass

class MixinB:
    pass

class Child(MixinA, MixinB):
    pass

def create():
    obj = Child()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        # No class has __init__, so no __init__ call is resolved
        assert "module.Child.__init__" not in create.calls
        assert "module.MixinA.__init__" not in create.calls
        assert "module.MixinB.__init__" not in create.calls

    def test_deep_inheritance_chain_mro(self, parser):
        """Test deep inheritance chain follows MRO to find first __init__."""
        code = """
class GrandParent:
    def __init__(self):
        pass

class Parent(GrandParent):
    pass

class Child(Parent):
    pass

def create():
    obj = Child()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        # Child and Parent have no __init__, MRO finds GrandParent.__init__
        assert "module.GrandParent.__init__" in create.calls
        assert "module.Child.__init__" not in create.calls
        assert "module.Parent.__init__" not in create.calls

    def test_external_parent_class(self, parser):
        """Test that external parent classes stop the chain."""
        code = """
class MyModel(BaseModel):  # BaseModel is external (e.g., pydantic)
    def __init__(self):
        pass

def create():
    obj = MyModel()
"""
        functions = parser.parse_functions(code, "models.py")
        classes = parser.parse_classes(code, "models.py")
        modules_dict = {"models": "models"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create = next(f for f in resolved if f.name == "create")
        # Should include MyModel.__init__ only
        assert "models.MyModel.__init__" in create.calls
        # BaseModel is external, not included


class TestMethodResolutionOrder:
    """Tests for MRO-based method resolution."""

    def test_self_call_resolves_to_parent_method(self, parser):
        """Test that self.method() resolves to parent when child doesn't have it."""
        code = """
class Parent:
    def parent_method(self):
        pass

class Child(Parent):
    def child_method(self):
        self.parent_method()  # Should resolve to Parent.parent_method
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_method = next(f for f in resolved if f.name == "child_method")
        # Should resolve to Parent.parent_method, not Child.parent_method
        assert "module.Parent.parent_method" in child_method.calls
        assert "module.Child.parent_method" not in child_method.calls

    def test_self_call_resolves_to_own_method_when_overridden(self, parser):
        """Test that self.method() resolves to own method when child overrides it."""
        code = """
class Parent:
    def method(self):
        pass

class Child(Parent):
    def method(self):
        pass

    def caller(self):
        self.method()  # Should resolve to Child.method, NOT Parent.method
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # Should resolve to Child.method (the overriding method)
        assert "module.Child.method" in caller.calls
        # Should NOT include Parent.method
        assert "module.Parent.method" not in caller.calls

    def test_variable_method_call_resolves_to_parent(self, parser):
        """Test that obj.method() resolves to parent when child doesn't have it."""
        code = """
class Parent:
    def __init__(self):
        pass

    def parent_method(self):
        pass

class Child(Parent):
    def __init__(self):
        pass

def caller():
    obj = Child()
    obj.parent_method()  # Should resolve to Parent.parent_method
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        # Should resolve to Parent.parent_method via MRO
        assert "module.Parent.parent_method" in caller_func.calls
        assert "module.Child.parent_method" not in caller_func.calls

    def test_variable_method_call_resolves_to_own_when_overridden(self, parser):
        """Test that obj.method() resolves to child's method when overridden."""
        code = """
class Parent:
    def __init__(self):
        pass

    def method(self):
        pass

class Child(Parent):
    def __init__(self):
        pass

    def method(self):
        pass

def caller():
    obj = Child()
    obj.method()  # Should resolve to Child.method, NOT Parent.method
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        # Should resolve to Child.method (not Parent.method)
        assert "module.Child.method" in caller_func.calls
        assert "module.Parent.method" not in caller_func.calls

    def test_deep_mro_method_resolution(self, parser):
        """Test MRO through deep inheritance chain."""
        code = """
class GrandParent:
    def method(self):
        pass

class Parent(GrandParent):
    pass  # No method override

class Child(Parent):
    def caller(self):
        self.method()  # Should resolve to GrandParent.method
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # Should resolve to GrandParent.method (first in MRO with the method)
        assert "module.GrandParent.method" in caller.calls
        assert "module.Parent.method" not in caller.calls
        assert "module.Child.method" not in caller.calls

    def test_mro_with_multiple_methods(self, parser):
        """Test that each method resolves correctly based on where it's defined."""
        code = """
class Parent:
    def __init__(self):
        pass

    def parent_only(self):
        pass

    def overridden(self):
        pass

class Child(Parent):
    def __init__(self):
        pass

    def child_only(self):
        pass

    def overridden(self):
        pass

def caller():
    obj = Child()
    obj.parent_only()   # Should resolve to Parent.parent_only
    obj.child_only()    # Should resolve to Child.child_only
    obj.overridden()    # Should resolve to Child.overridden
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        assert "module.Parent.parent_only" in caller_func.calls
        assert "module.Child.child_only" in caller_func.calls
        assert "module.Child.overridden" in caller_func.calls
        # Should NOT have Parent.overridden (it's overridden by Child)
        assert "module.Parent.overridden" not in caller_func.calls


class TestExternalCallFiltering:
    """Tests for filtering out external/unknown calls."""

    def test_builtin_calls_filtered(self, parser):
        """Test that builtin function calls are filtered out."""
        code = """
def func():
    print("hello")
    len([1, 2, 3])
    str(42)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = resolved[0]
        # Builtins should be filtered (not in all_internals)
        assert "print" not in func.calls
        assert "len" not in func.calls
        assert "str" not in func.calls

    def test_external_module_calls_filtered(self, parser):
        """Test that external module calls are filtered."""
        code = """
def func():
    os.path.join("a", "b")
    json.dumps({"key": "value"})
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = resolved[0]
        assert len(func.calls) == 0

    def test_mixed_internal_external_calls(self, parser):
        """Test that only internal calls are kept."""
        code = """
class InternalClass:
    def __init__(self):
        pass

    def method(self):
        pass

def internal_func():
    pass

def caller():
    internal_func()          # Should be kept
    obj = InternalClass()    # Should be kept
    print("external")        # Should be filtered
    external.call()          # Should be filtered
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "module.internal_func" in caller.calls
        assert "module.InternalClass.__init__" in caller.calls
        assert len(caller.calls) == 2  # Only internal calls


class TestCircularInheritance:
    """Tests for circular/cyclic inheritance in the class hierarchy."""

    def test_direct_cycle_does_not_recurse(self):
        """A -> B -> A should not cause infinite recursion in _compute_mro."""
        functions = [
            create_function_chunk("method", "module.py", class_name="A"),
            create_function_chunk("method", "module.py", class_name="B"),
            create_function_chunk("caller", "module.py", calls=["self.method"], class_name="A"),
        ]
        classes = [
            create_class_chunk("A", "module.py", superclasses=["B"]),
            create_class_chunk("B", "module.py", superclasses=["A"]),
        ]
        modules_dict = {"module": "module"}

        # Must not raise RecursionError
        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "module.A.method" in caller.calls

    def test_three_class_cycle_does_not_recurse(self):
        """A -> B -> C -> A should not cause infinite recursion."""
        functions = [
            create_function_chunk("method", "module.py", class_name="A"),
            create_function_chunk("caller", "module.py", calls=["self.method"], class_name="C"),
        ]
        classes = [
            create_class_chunk("A", "module.py", superclasses=["B"]),
            create_class_chunk("B", "module.py", superclasses=["C"]),
            create_class_chunk("C", "module.py", superclasses=["A"]),
        ]
        modules_dict = {"module": "module"}

        # Must not raise RecursionError
        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)
        assert resolved is not None

    def test_self_referential_class_does_not_recurse(self):
        """A -> A (self-referential) should not cause infinite recursion."""
        functions = [
            create_function_chunk("method", "module.py", class_name="A"),
            create_function_chunk("caller", "module.py", calls=["self.method"], class_name="A"),
        ]
        classes = [
            create_class_chunk("A", "module.py", superclasses=["A"]),
        ]
        modules_dict = {"module": "module"}

        # Must not raise RecursionError
        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "module.A.method" in caller.calls


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_empty_function_calls(self, parser):
        """Test function with no calls."""
        code = """
def empty():
    x = 1
    return x
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        assert resolved[0].calls == []

    def test_no_var_types(self, parser):
        """Test function with no variable assignments."""
        code = """
class MyClass:
    def method(self):
        pass

def func():
    MyClass.method()  # Static-style call, no instantiation
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "func")
        # Should still resolve the static-style call
        assert "module.MyClass.method" in func.calls

    def test_recursive_function_call(self, parser):
        """Test recursive function call resolution."""
        code = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
"""
        functions = parser.parse_functions(code, "math_utils.py")
        modules_dict = {"math_utils": "math_utils"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        factorial = resolved[0]
        assert "math_utils.factorial" in factorial.calls

    def test_lambda_not_parsed_as_function(self, parser):
        """Test that lambdas don't create FunctionChunks."""
        code = """
def func():
    f = lambda x: x * 2
    return f(10)
"""
        functions = parser.parse_functions(code, "module.py")

        # Should only have 'func', not the lambda
        assert len(functions) == 1
        assert functions[0].name == "func"

    def test_comprehension_calls(self, parser):
        """Test calls within comprehensions are captured."""
        code = """
def process_items():
    items = [transform(x) for x in data]
    return items

def transform(x):
    return x
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        process = next(f for f in resolved if f.name == "process_items")
        assert "module.transform" in process.calls


class TestMethodNameFallback:
    """Tests for fallback resolution by method name."""

    def test_dict_lookup_method_call(self, parser):
        """Test resolving method calls on dict lookups like labels["instances"].method()."""
        code = """
class Instances:
    def convert_bbox(self, format):
        pass

    def denormalize(self, width, height):
        pass

    def add_padding(self, padding):
        pass

def transform(self, labels):
    labels["instances"].convert_bbox("xyxy")
    labels["instances"].denormalize(640, 480)
    labels["instances"].add_padding(10)
"""
        functions = parser.parse_functions(code, "augment.py")
        classes = parser.parse_classes(code, "augment.py")
        modules_dict = {"augment": "augment"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        transform_func = next(f for f in resolved if f.name == "transform")
        # These should be resolved via fallback since labels["instances"] can't be typed
        assert "augment.Instances.convert_bbox" in transform_func.calls
        assert "augment.Instances.denormalize" in transform_func.calls
        assert "augment.Instances.add_padding" in transform_func.calls

    def test_untyped_argument_method_call(self, parser):
        """Test resolving method calls on untyped function arguments."""
        code = """
class DataProcessor:
    def process(self, data):
        pass

    def validate(self):
        pass

def handle_data(processor):
    # processor has no type hint, but process() is unique
    processor.process(data)
    processor.validate()
"""
        functions = parser.parse_functions(code, "handler.py")
        classes = parser.parse_classes(code, "handler.py")
        modules_dict = {"handler": "handler"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        handle_func = next(f for f in resolved if f.name == "handle_data")
        assert "handler.DataProcessor.process" in handle_func.calls
        assert "handler.DataProcessor.validate" in handle_func.calls

    def test_unique_method_name_resolved(self, parser):
        """Test that unique method names are resolved."""
        code = """
class MyClass:
    def unique_method_name(self):
        pass

def caller(obj):
    obj.unique_method_name()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        assert "module.MyClass.unique_method_name" in caller_func.calls

    def test_multiple_matching_methods_not_resolved(self, parser):
        """Test that multiple matching methods are NOT resolved (ambiguous)."""
        code = """
class ClassA:
    def shared_method(self):
        pass

class ClassB:
    def shared_method(self):
        pass

def caller(obj):
    obj.shared_method()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        # Multiple matches should NOT be resolved to avoid ambiguous call graph edges
        assert "module.ClassA.shared_method" not in caller_func.calls
        assert "module.ClassB.shared_method" not in caller_func.calls

    def test_builtin_method_names_not_resolved(self, parser):
        """Test that common builtin method names are not resolved."""
        code = """
class MyClass:
    def append(self, item):  # Same name as list.append
        pass

def caller(obj):
    obj.append(item)  # Could be list.append or MyClass.append
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        # append is in builtin_methods list, so should NOT be resolved
        assert "module.MyClass.append" not in caller_func.calls

    def test_imported_module_not_fallback_resolved(self, parser):
        """Test that imported module calls don't trigger fallback."""
        code = """
class MyClass:
    def array(self):  # Same name as np.array
        pass

def caller():
    np.array([1, 2, 3])  # Should NOT resolve to MyClass.array
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}
        imports = {"np": "numpy"}

        resolved = resolve_internal_calls(functions, classes, imports, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        # np is an import, so it should not fallback to MyClass.array
        assert "module.MyClass.array" not in caller_func.calls

    def test_chained_method_call_fallback(self, parser):
        """Test fallback resolution for chained calls like a.b.method()."""
        code = """
class Processor:
    def transform(self, data):
        pass

def process(data):
    data.pipeline.steps[0].transform(x)
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        process_func = next(f for f in resolved if f.name == "process")
        # The method name 'transform' should match Processor.transform
        assert "module.Processor.transform" in process_func.calls


class TestSuperCallResolution:
    """Tests for super() call resolution."""

    def test_super_init_resolves_to_parent(self, parser):
        """Test super().__init__() resolves to parent class __init__."""
        code = """
class Parent:
    def __init__(self):
        pass

class Child(Parent):
    def __init__(self):
        super().__init__()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_init = next(f for f in resolved if f.name == "__init__" and f.class_name == "Child")
        assert "module.Parent.__init__" in child_init.calls

    def test_super_method_resolves_to_parent(self, parser):
        """Test super().method() resolves to parent class method."""
        code = """
class Parent:
    def do_something(self):
        pass

class Child(Parent):
    def do_something(self):
        super().do_something()
        # Additional child logic
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_method = next(
            f for f in resolved if f.name == "do_something" and f.class_name == "Child"
        )
        assert "module.Parent.do_something" in child_method.calls

    def test_super_resolves_through_mro(self, parser):
        """Test super() follows MRO for deep inheritance."""
        code = """
class GrandParent:
    def method(self):
        pass

class Parent(GrandParent):
    pass  # No method override

class Child(Parent):
    def caller(self):
        super().method()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # Should resolve to GrandParent.method (first in MRO with the method)
        assert "module.GrandParent.method" in caller.calls

    def test_super_multiple_inheritance(self, parser):
        """Test super() with multiple inheritance follows MRO order."""
        code = """
class MixinA:
    def mixin_method(self):
        pass

class MixinB:
    def mixin_method(self):
        pass

class Child(MixinA, MixinB):
    def caller(self):
        super().mixin_method()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # MRO is Child -> MixinA -> MixinB, so super() should find MixinA first
        assert "module.MixinA.mixin_method" in caller.calls

    def test_super_outside_class_not_resolved(self, parser):
        """Test that super() outside class context is not resolved."""
        functions = [
            create_function_chunk(
                name="standalone",
                calls=["super().__init__"],
                class_name=None,  # Not in a class
            )
        ]
        modules_dict = {"test": "test"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        # Should not resolve super() without class context
        assert resolved[0].calls == []


class TestPackageExportsResolution:
    """Tests for package exports resolution from __init__.py re-exports."""

    def test_package_export_resolution(self):
        """Test that package re-exports are resolved correctly."""
        # Simulate: from .base import get_default_callbacks in callbacks/__init__.py
        # Call: callbacks.get_default_callbacks() should resolve to callbacks.base.get_default_callbacks

        functions = [
            create_function_chunk(name="get_default_callbacks", file_path="pkg/callbacks/base.py"),
            create_function_chunk(
                name="caller", file_path="pkg/trainer.py", calls=["callbacks.get_default_callbacks"]
            ),
        ]
        classes = []
        imports = {"callbacks": "pkg.callbacks.__init__"}
        modules_dict = {
            "pkg.callbacks.base": "pkg.callbacks.base",
            "pkg.callbacks.__init__": "pkg.callbacks.__init__",
            "pkg.trainer": "pkg.trainer",
        }
        package_exports = {
            "pkg.callbacks.get_default_callbacks": "pkg.callbacks.base.get_default_callbacks"
        }

        resolver = CallResolver(functions, classes, imports, modules_dict, package_exports)
        resolved = resolver.resolve_all_calls()

        caller = next(f for f in resolved if f.name == "caller")
        assert "pkg.callbacks.base.get_default_callbacks" in caller.calls

    def test_package_export_with_init_in_path(self):
        """Test resolution when import path contains __init__."""
        functions = [
            create_function_chunk(name="helper_func", file_path="utils/helpers/base.py"),
            create_function_chunk(
                name="caller", file_path="main.py", calls=["helpers.helper_func"]
            ),
        ]
        classes = []
        # Import resolves to __init__.py of helpers package
        imports = {"helpers": "utils.helpers.__init__"}
        modules_dict = {
            "utils.helpers.base": "utils.helpers.base",
            "utils.helpers.__init__": "utils.helpers.__init__",
            "main": "main",
        }
        # Package exports map the re-export
        package_exports = {"utils.helpers.helper_func": "utils.helpers.base.helper_func"}

        resolver = CallResolver(functions, classes, imports, modules_dict, package_exports)
        resolved = resolver.resolve_all_calls()

        caller = next(f for f in resolved if f.name == "caller")
        # Should resolve through package_exports, stripping __init__
        assert "utils.helpers.base.helper_func" in caller.calls


class TestIntegration:
    """Integration tests with realistic code scenarios."""

    def test_service_pattern(self, parser):
        """Test typical service class pattern."""
        code = """
class Repository:
    def __init__(self):
        pass

    def get(self, id):
        pass

    def save(self, entity):
        pass

class Service:
    def __init__(self):
        self.repo = Repository()

    def get_item(self, id):
        return self.repo.get(id)

    def create_item(self, data):
        item = Item(data)
        self.repo.save(item)
        return item

class Item:
    def __init__(self, data):
        self.data = data

def main():
    service = Service()
    item = service.get_item(1)
    new_item = service.create_item({"name": "test"})
"""
        functions = parser.parse_functions(code, "service.py")
        classes = parser.parse_classes(code, "service.py")
        modules_dict = {"service": "service"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        # Check main function
        main_func = next(f for f in resolved if f.name == "main")
        assert "service.Service.__init__" in main_func.calls
        assert "service.Service.get_item" in main_func.calls
        assert "service.Service.create_item" in main_func.calls

        # Check Service.__init__
        init_func = next(f for f in resolved if f.name == "__init__" and f.class_name == "Service")
        assert "service.Repository.__init__" in init_func.calls

        # Check create_item - note: self.repo.save() is NOT resolved because
        # self.repo is an instance attribute, not a local variable in var_types.
        # This is by design - we only track direct variable assignments.
        create_func = next(f for f in resolved if f.name == "create_item")
        assert "service.Item.__init__" in create_func.calls
        # self.repo.save is not resolved (would need attribute type tracking)

    def test_factory_pattern(self, parser):
        """Test factory pattern with multiple classes."""
        code = """
class ProductA:
    def __init__(self):
        pass

    def operation(self):
        pass

class ProductB:
    def __init__(self):
        pass

    def operation(self):
        pass

class Factory:
    def __init__(self):
        pass

    def create(self, type):
        if type == "A":
            return ProductA()
        return ProductB()

def client():
    factory = Factory()
    product = factory.create("A")
"""
        functions = parser.parse_functions(code, "factory.py")
        classes = parser.parse_classes(code, "factory.py")
        modules_dict = {"factory": "factory"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        # Check Factory.create
        create_method = next(f for f in resolved if f.name == "create")
        assert "factory.ProductA.__init__" in create_method.calls
        assert "factory.ProductB.__init__" in create_method.calls

        # Check client
        client_func = next(f for f in resolved if f.name == "client")
        assert "factory.Factory.__init__" in client_func.calls
        assert "factory.Factory.create" in client_func.calls

    def test_decorator_pattern(self, parser):
        """Test decorator/wrapper pattern."""
        code = """
class Component:
    def __init__(self):
        pass

    def operation(self):
        pass

class Decorator(Component):
    def __init__(self, component):
        self.component = component

    def operation(self):
        self.component.operation()

def main():
    base = Component()
    decorated = Decorator(base)
    decorated.operation()
"""
        functions = parser.parse_functions(code, "decorator.py")
        classes = parser.parse_classes(code, "decorator.py")
        modules_dict = {"decorator": "decorator"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        main_func = next(f for f in resolved if f.name == "main")
        assert "decorator.Component.__init__" in main_func.calls  # From base = Component()
        assert "decorator.Decorator.__init__" in main_func.calls  # From decorated = Decorator(base)
        assert "decorator.Decorator.operation" in main_func.calls  # From decorated.operation()


class TestNoSubclassOverrideLeakage:
    """Tests that self.xxx() does NOT include subclass overrides.

    A class should never have call edges to methods in subclasses it
    doesn't know about via self calls alone.
    """

    def test_parent_self_call_does_not_include_child_override(self, parser):
        """Test that parent self.method() does NOT include child override."""
        code = """
class BaseDataset:
    def __init__(self):
        self.get_img_files()

    def get_img_files(self):
        pass

class YOLODataset(BaseDataset):
    def __init__(self):
        super().__init__()

    def get_img_files(self):
        pass
"""
        functions = parser.parse_functions(code, "dataset.py")
        classes = parser.parse_classes(code, "dataset.py")
        modules_dict = {"dataset": "dataset"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        base_init = next(
            f for f in resolved if f.name == "__init__" and f.class_name == "BaseDataset"
        )
        assert "dataset.BaseDataset.get_img_files" in base_init.calls
        # Child override should NOT appear in the PARENT's calls
        assert "dataset.YOLODataset.get_img_files" not in base_init.calls

    def test_self_call_no_grandchild_leakage(self):
        """Test that self.method() does not leak to grandchild overrides."""
        functions = [
            create_function_chunk("run", "module.py", class_name="Child", calls=["self.method"]),
            create_function_chunk("method", "module.py", class_name="Child"),
            create_function_chunk("method", "module.py", class_name="GrandChild"),
        ]
        functions[2].id = "module.GrandChild.method"

        classes = [
            create_class_chunk("Child", "module.py"),
            create_class_chunk("GrandChild", "module.py", superclasses=["Child"]),
        ]
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_run = next(f for f in resolved if f.name == "run")
        assert "module.Child.method" in child_run.calls
        assert "module.GrandChild.method" not in child_run.calls


class TestSuperCallInlining:
    """Tests for inlining parent's self.xxx calls on super() resolution.

    When a child calls super().__init__(), the parent's __init__ runs with
    self being the child instance. Any self.xxx() calls in the parent
    dispatch to the child's overrides. We inline those calls so the child
    gets direct edges to the correctly resolved methods.
    """

    def test_super_inlines_overridden_method(self, parser):
        """Test that super().__init__() inlines parent's self calls with child's MRO."""
        code = """
class BaseDataset:
    def __init__(self):
        self.get_img_files()
        self.update_labels()

    def get_img_files(self):
        pass

    def update_labels(self):
        pass

class YOLODataset(BaseDataset):
    def __init__(self):
        super().__init__()

    def get_img_files(self):
        pass

    def update_labels(self):
        pass
"""
        functions = parser.parse_functions(code, "dataset.py")
        classes = parser.parse_classes(code, "dataset.py")
        modules_dict = {"dataset": "dataset"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        yolo_init = next(
            f for f in resolved if f.name == "__init__" and f.class_name == "YOLODataset"
        )
        # super().__init__() edge
        assert "dataset.BaseDataset.__init__" in yolo_init.calls
        # Inlined self calls resolved from YOLODataset's MRO (overrides)
        assert "dataset.YOLODataset.get_img_files" in yolo_init.calls
        assert "dataset.YOLODataset.update_labels" in yolo_init.calls

        # BaseDataset.__init__ itself is NOT modified
        base_init = next(
            f for f in resolved if f.name == "__init__" and f.class_name == "BaseDataset"
        )
        assert "dataset.BaseDataset.get_img_files" in base_init.calls
        assert "dataset.YOLODataset.get_img_files" not in base_init.calls

    def test_super_only_inlines_overridden_methods(self, parser):
        """Test that only overridden methods are inlined, not inherited ones."""
        code = """
class Base:
    def __init__(self):
        self.setup()
        self.configure()

    def setup(self):
        pass

    def configure(self):
        pass

class Child(Base):
    def __init__(self):
        super().__init__()

    def setup(self):
        pass
    # configure is NOT overridden
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_init = next(f for f in resolved if f.name == "__init__" and f.class_name == "Child")
        assert "module.Base.__init__" in child_init.calls
        # Overridden: inlined with child's resolution
        assert "module.Child.setup" in child_init.calls
        # Not overridden: NOT inlined (already reachable via Base.__init__)
        assert "module.Base.configure" not in child_init.calls

    def test_super_inlining_works_for_non_init_methods(self, parser):
        """Test that inlining works for any super().method(), not just __init__."""
        code = """
class Base:
    def build(self):
        self.create_layers()
        self.init_weights()

    def create_layers(self):
        pass

    def init_weights(self):
        pass

class Child(Base):
    def build(self):
        super().build()

    def create_layers(self):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_build = next(f for f in resolved if f.name == "build" and f.class_name == "Child")
        assert "module.Base.build" in child_build.calls
        # Overridden: inlined
        assert "module.Child.create_layers" in child_build.calls
        # Not overridden: NOT inlined (reachable via Base.build)
        assert "module.Base.init_weights" not in child_build.calls

    def test_super_inlining_no_leakage_to_sibling(self, parser):
        """Test that inlining for one child doesn't affect another child."""
        code = """
class Base:
    def __init__(self):
        self.process()

    def process(self):
        pass

class ChildA(Base):
    def __init__(self):
        super().__init__()

    def process(self):
        pass

class ChildB(Base):
    def __init__(self):
        super().__init__()

    def process(self):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_a_init = next(
            f for f in resolved if f.name == "__init__" and f.class_name == "ChildA"
        )
        assert "module.ChildA.process" in child_a_init.calls
        assert "module.ChildB.process" not in child_a_init.calls

        child_b_init = next(
            f for f in resolved if f.name == "__init__" and f.class_name == "ChildB"
        )
        assert "module.ChildB.process" in child_b_init.calls
        assert "module.ChildA.process" not in child_b_init.calls

    def test_super_inlining_parent_no_self_calls(self, parser):
        """Test super() with parent that has no self calls — just the edge."""
        code = """
class Base:
    def __init__(self):
        x = 1

class Child(Base):
    def __init__(self):
        super().__init__()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        child_init = next(f for f in resolved if f.name == "__init__" and f.class_name == "Child")
        assert "module.Base.__init__" in child_init.calls
        assert len([c for c in child_init.calls if "Base.__init__" in c]) == 1


class TestExtractClassFromGenericType:
    """Tests for _extract_class_from_generic_type static method."""

    def test_iterator(self):
        assert CallResolver._extract_class_from_generic_type("Iterator[PreChunk]") == "PreChunk"

    def test_list(self):
        assert CallResolver._extract_class_from_generic_type("List[Foo]") == "Foo"

    def test_optional(self):
        assert CallResolver._extract_class_from_generic_type("Optional[Bar]") == "Bar"

    def test_optional_union_none(self):
        assert CallResolver._extract_class_from_generic_type("Union[Baz, None]") == "Baz"

    def test_generator_first_param(self):
        assert (
            CallResolver._extract_class_from_generic_type("Generator[Yield, Send, Return]")
            == "Yield"
        )

    def test_nested_generic(self):
        assert CallResolver._extract_class_from_generic_type("Iterator[List[Chunk]]") == "Chunk"

    def test_dict_returns_none(self):
        assert CallResolver._extract_class_from_generic_type("Dict[str, int]") is None

    def test_primitives_return_none(self):
        assert CallResolver._extract_class_from_generic_type("int") is None
        assert CallResolver._extract_class_from_generic_type("str") is None
        assert CallResolver._extract_class_from_generic_type("float") is None
        assert CallResolver._extract_class_from_generic_type("bool") is None

    def test_any_returns_none(self):
        assert CallResolver._extract_class_from_generic_type("Any") is None

    def test_bare_class_name(self):
        assert CallResolver._extract_class_from_generic_type("PreChunk") == "PreChunk"

    def test_sequence(self):
        assert CallResolver._extract_class_from_generic_type("Sequence[Item]") == "Item"

    def test_iterable(self):
        assert CallResolver._extract_class_from_generic_type("Iterable[Element]") == "Element"


class TestReturnTypeResolution:
    """Tests for resolving method calls through return type annotations."""

    def test_chained_call_plus_for_loop_resolves_method(self):
        """Test the motivating example: PreChunkCombiner().iter_combined_pre_chunks() -> for pre_chunk -> pre_chunk.iter_chunks()."""
        functions = [
            # PreChunkCombiner.iter_combined_pre_chunks returns Iterator[PreChunk]
            create_function_chunk(
                "iter_combined_pre_chunks",
                "pkg/combiner.py",
                class_name="PreChunkCombiner",
                return_type="Iterator[PreChunk]",
            ),
            # PreChunk.iter_chunks is the method we want to resolve to
            create_function_chunk("iter_chunks", "pkg/chunk.py", class_name="PreChunk"),
            # The calling function
            create_function_chunk(
                "caller",
                "pkg/main.py",
                calls=["pre_chunk.iter_chunks"],
                var_sources={
                    "pre_chunks": "PreChunkCombiner.iter_combined_pre_chunks",
                    "pre_chunk": "@iter:pre_chunks",
                },
            ),
        ]
        classes = [
            create_class_chunk("PreChunkCombiner", "pkg/combiner.py"),
            create_class_chunk("PreChunk", "pkg/chunk.py"),
        ]
        modules_dict = {
            "pkg.combiner": "pkg.combiner",
            "pkg.chunk": "pkg.chunk",
            "pkg.main": "pkg.main",
        }

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "pkg.chunk.PreChunk.iter_chunks" in caller.calls

    def test_direct_chained_call_resolves_method(self):
        """Test var_sources with direct chained call (no for loop)."""
        functions = [
            create_function_chunk(
                "get_config", "pkg/factory.py", class_name="Factory", return_type="Config"
            ),
            create_function_chunk("validate", "pkg/config.py", class_name="Config"),
            create_function_chunk(
                "caller",
                "pkg/main.py",
                calls=["cfg.validate"],
                var_sources={"cfg": "Factory.get_config"},
            ),
        ]
        classes = [
            create_class_chunk("Factory", "pkg/factory.py"),
            create_class_chunk("Config", "pkg/config.py"),
        ]
        modules_dict = {
            "pkg.factory": "pkg.factory",
            "pkg.config": "pkg.config",
            "pkg.main": "pkg.main",
        }

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "pkg.config.Config.validate" in caller.calls

    def test_iter_call_resolves_method(self):
        """Test var_sources with @iter_call (for x in func())."""
        functions = [
            create_function_chunk("get_items", "pkg/service.py", return_type="List[Item]"),
            create_function_chunk("process", "pkg/item.py", class_name="Item"),
            create_function_chunk(
                "caller",
                "pkg/main.py",
                calls=["item.process"],
                var_sources={"item": "@iter_call:get_items"},
            ),
        ]
        classes = [
            create_class_chunk("Item", "pkg/item.py"),
        ]
        modules_dict = {
            "pkg.service": "pkg.service",
            "pkg.item": "pkg.item",
            "pkg.main": "pkg.main",
        }

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "pkg.item.Item.process" in caller.calls

    def test_var_types_takes_priority_over_var_sources(self):
        """Test that var_types is checked before var_sources."""
        functions = [
            create_function_chunk("method", "pkg/module.py", class_name="TypedClass"),
            create_function_chunk("method", "pkg/other.py", class_name="SourceClass"),
            create_function_chunk(
                "caller",
                "pkg/main.py",
                calls=["obj.method"],
                var_types={"obj": ["TypedClass"]},
                var_sources={"obj": "SourceClass.factory"},
            ),
        ]
        classes = [
            create_class_chunk("TypedClass", "pkg/module.py"),
            create_class_chunk("SourceClass", "pkg/other.py"),
        ]
        modules_dict = {
            "pkg.module": "pkg.module",
            "pkg.other": "pkg.other",
            "pkg.main": "pkg.main",
        }

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # var_types should win
        assert "pkg.module.TypedClass.method" in caller.calls
        assert "pkg.other.SourceClass.method" not in caller.calls

    def test_primitive_return_type_no_resolution(self):
        """Test that primitive return types don't cause false resolutions."""
        functions = [
            create_function_chunk(
                "get_count", "pkg/service.py", class_name="Service", return_type="int"
            ),
            create_function_chunk(
                "caller",
                "pkg/main.py",
                calls=["result.bit_length"],
                var_sources={"result": "Service.get_count"},
            ),
        ]
        classes = [
            create_class_chunk("Service", "pkg/service.py"),
        ]
        modules_dict = {"pkg.service": "pkg.service", "pkg.main": "pkg.main"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # int is a primitive, so no resolution should happen
        assert len(caller.calls) == 0

    def test_unknown_return_type_class_no_resolution(self):
        """Test that return type pointing to non-internal class doesn't resolve."""
        functions = [
            create_function_chunk(
                "get_session",
                "pkg/service.py",
                class_name="Service",
                return_type="ExternalSession",  # Not an internal class
            ),
            create_function_chunk(
                "caller",
                "pkg/main.py",
                calls=["session.execute"],
                var_sources={"session": "Service.get_session"},
            ),
        ]
        classes = [
            create_class_chunk("Service", "pkg/service.py"),
        ]
        modules_dict = {"pkg.service": "pkg.service", "pkg.main": "pkg.main"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert len(caller.calls) == 0

    def test_no_var_sources_no_crash(self):
        """Test that functions without var_sources work normally."""
        functions = [
            create_function_chunk("method", "pkg/module.py", class_name="MyClass"),
            create_function_chunk(
                "caller", "pkg/main.py", calls=["unknown.method"], var_sources=None
            ),
        ]
        classes = [
            create_class_chunk("MyClass", "pkg/module.py"),
        ]
        modules_dict = {"pkg.module": "pkg.module", "pkg.main": "pkg.main"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        # Should not crash, method falls through to fallback
        caller = next(f for f in resolved if f.name == "caller")
        assert "pkg.module.MyClass.method" in caller.calls
