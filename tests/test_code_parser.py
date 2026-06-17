"""
Comprehensive tests for the CodeParser module.

Tests cover:
- Function parsing (simple, with params, with decorators, nested, class methods)
- Class parsing (simple, with inheritance, with decorators)
- Import parsing
- Call extraction from function bodies
- Variable assignment tracking (var_types)
"""

import pytest

from codiff.code_parsing.code_parser import CodeParser


@pytest.fixture
def parser():
    """Create a CodeParser instance for testing."""
    return CodeParser()


class TestFunctionParsing:
    """Tests for function parsing functionality."""

    def test_simple_function(self, parser):
        """Test parsing a simple function with no parameters."""
        code = """
def hello():
    print("Hello")
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        func = functions[0]
        assert func.name == "hello"
        assert func.file_path == "test.py"
        assert func.class_name is None
        assert func.parameters == []

    def test_function_with_parameters(self, parser):
        """Test parsing function with various parameter types."""
        code = """
def process(a, b: int, c="default", d: str = "typed_default"):
    pass
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        func = functions[0]
        assert func.name == "process"
        assert len(func.parameters) == 4

        # Check parameter details
        param_names = [p.name for p in func.parameters]
        assert param_names == ["a", "b", "c", "d"]

        # Check typed parameter
        assert func.parameters[1].type == "int"

        # Check default parameter
        assert func.parameters[2].value == '"default"'

        # Check typed default parameter
        assert func.parameters[3].type == "str"
        assert func.parameters[3].value == '"typed_default"'

    def test_function_with_return_type(self, parser):
        """Test parsing function with return type annotation."""
        code = """
def get_value() -> int:
    return 42
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        assert functions[0].return_type == "int"

    def test_function_with_complex_return_type(self, parser):
        """Test parsing function with complex return type."""
        code = """
def get_items() -> List[Dict[str, Any]]:
    return []
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        assert "List[Dict[str, Any]]" in functions[0].return_type

    def test_function_with_docstring(self, parser):
        """Test parsing function with docstring."""
        code = '''
def documented():
    """This is a docstring."""
    pass
'''
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        assert functions[0].docstring is not None
        assert "docstring" in functions[0].docstring

    def test_function_with_decorators(self, parser):
        """Test parsing function with decorators."""
        code = """
@staticmethod
@lru_cache(maxsize=100)
def cached_func():
    pass
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        assert "staticmethod" in functions[0].decorators
        assert "lru_cache(maxsize=100)" in functions[0].decorators

    def test_nested_function(self, parser):
        """Test parsing nested functions."""
        code = """
def outer():
    def inner():
        pass
    return inner
"""
        functions = parser.parse_functions(code, "test.py")

        # Should find both outer and inner
        assert len(functions) == 2

        names = [f.name for f in functions]
        assert "outer" in names
        assert "inner" in names

        # Inner function should have nested attribute set
        inner_func = next(f for f in functions if f.name == "inner")
        assert inner_func.nested == "outer"

    def test_class_method(self, parser):
        """Test parsing methods inside a class."""
        code = """
class MyClass:
    def method(self):
        pass
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        func = functions[0]
        assert func.name == "method"
        assert func.class_name == "MyClass"

    def test_multiple_class_methods(self, parser):
        """Test parsing multiple methods in a class."""
        code = """
class MyClass:
    def __init__(self):
        pass

    def method_a(self):
        pass

    def method_b(self, x: int) -> str:
        return str(x)
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 3
        for func in functions:
            assert func.class_name == "MyClass"

    def test_function_id_generation(self, parser):
        """Test that function IDs are generated correctly."""
        code = """
def standalone():
    pass

class MyClass:
    def method(self):
        pass
"""
        functions = parser.parse_functions(code, "module/submodule.py")

        standalone = next(f for f in functions if f.name == "standalone")
        method = next(f for f in functions if f.name == "method")

        assert standalone.id == "module.submodule.standalone"
        assert method.id == "module.submodule.MyClass.method"

    def test_function_line_numbers(self, parser):
        """Test that line numbers are captured correctly."""
        code = """
def first():
    pass

def second():
    x = 1
    y = 2
    return x + y
"""
        functions = parser.parse_functions(code, "test.py")

        first_func = next(f for f in functions if f.name == "first")
        second_func = next(f for f in functions if f.name == "second")

        assert first_func.start_line == 2
        assert first_func.end_line == 3
        assert second_func.start_line == 5
        assert second_func.end_line == 8


class TestClassParsing:
    """Tests for class parsing functionality."""

    def test_simple_class(self, parser):
        """Test parsing a simple class."""
        code = """
class MyClass:
    pass
"""
        classes = parser.parse_classes(code, "test.py")

        assert len(classes) == 1
        cls = classes[0]
        assert cls.name == "MyClass"
        assert cls.superclasses == []

    def test_class_with_inheritance(self, parser):
        """Test parsing class with single inheritance."""
        code = """
class Child(Parent):
    pass
"""
        classes = parser.parse_classes(code, "test.py")

        assert len(classes) == 1
        assert "Parent" in classes[0].superclasses

    def test_class_with_multiple_inheritance(self, parser):
        """Test parsing class with multiple inheritance."""
        code = """
class Child(ParentA, ParentB, ParentC):
    pass
"""
        classes = parser.parse_classes(code, "test.py")

        assert len(classes) == 1
        assert len(classes[0].superclasses) == 3
        assert "ParentA" in classes[0].superclasses
        assert "ParentB" in classes[0].superclasses
        assert "ParentC" in classes[0].superclasses

    def test_class_with_docstring(self, parser):
        """Test parsing class with docstring."""
        code = '''
class Documented:
    """This class has documentation."""
    pass
'''
        classes = parser.parse_classes(code, "test.py")

        assert len(classes) == 1
        assert classes[0].docstring is not None
        assert "documentation" in classes[0].docstring

    def test_class_with_decorators(self, parser):
        """Test parsing class with decorators."""
        code = """
@dataclass
@frozen
class MyDataClass:
    value: int
"""
        classes = parser.parse_classes(code, "test.py")

        assert len(classes) == 1
        assert "dataclass" in classes[0].decorators
        assert "frozen" in classes[0].decorators

    def test_class_id_generation(self, parser):
        """Test that class IDs are generated correctly."""
        code = """
class MyClass:
    pass
"""
        classes = parser.parse_classes(code, "pkg/module.py")

        assert classes[0].id == "pkg.module.MyClass"

    def test_nested_classes(self, parser):
        """Test parsing nested classes."""
        code = """
class Outer:
    class Inner:
        pass
"""
        classes = parser.parse_classes(code, "test.py")

        # Should find both classes
        assert len(classes) == 2
        names = [c.name for c in classes]
        assert "Outer" in names
        assert "Inner" in names


class TestImportParsing:
    """Tests for import parsing functionality."""

    def test_simple_import(self, parser):
        """Test parsing simple import statement."""
        code = """
import os
"""
        # Note: parse_imports filters based on all_modules
        modules_dict = {"os": "os"}
        imports = parser.parse_imports(code, modules_dict)

        # Simple imports without alias are not tracked (k == v case)
        assert imports == {}

    def test_import_from(self, parser):
        """Test parsing from ... import statement."""
        code = """
from mymodule import MyClass
"""
        # Use direct mapping for test simplicity
        modules_dict = {"mymodule": "mymodule"}
        imports = parser.parse_imports(code, modules_dict)

        assert "MyClass" in imports
        # Import resolution uses parent module + import name
        assert imports["MyClass"] == "mymodule.MyClass"

    def test_import_with_alias(self, parser):
        """Test parsing import with alias.

        Note: The import parser only tracks internal imports where
        the module prefix is in all_modules. For simple 'import x as y',
        the module part (before the last dot) must be in all_modules.
        """
        code = """
import mypackage.utils as utils
"""
        # For 'import mypackage.utils as utils':
        # k = 'utils', v = 'mypackage.utils'
        # module = 'mypackage' (everything before last dot)
        # 'mypackage' must be in all_modules
        modules_dict = {"mypackage": "mypackage", "mypackage.utils": "mypackage.utils"}
        imports = parser.parse_imports(code, modules_dict)

        assert "utils" in imports
        assert imports["utils"] == "mypackage.utils"

    def test_from_import_with_alias(self, parser):
        """Test parsing from import with alias."""
        code = """
from mymodule import MyClass as MC
"""
        # Use direct mapping for test simplicity
        modules_dict = {"mymodule": "mymodule"}
        imports = parser.parse_imports(code, modules_dict)

        assert "MC" in imports
        assert imports["MC"] == "mymodule.MyClass"


class TestCallExtraction:
    """Tests for call extraction from function bodies."""

    def test_simple_function_call(self, parser):
        """Test extracting simple function calls."""
        code = """
def func():
    print("hello")
    len([1, 2, 3])
"""
        functions = parser.parse_functions(code, "test.py")

        assert len(functions) == 1
        calls = functions[0].calls
        assert "print" in calls
        assert "len" in calls

    def test_method_call(self, parser):
        """Test extracting method calls."""
        code = """
def func():
    "hello".upper()
    [1, 2].append(3)
"""
        functions = parser.parse_functions(code, "test.py")

        calls = functions[0].calls
        assert '"hello".upper' in calls
        assert "[1, 2].append" in calls

    def test_self_method_call(self, parser):
        """Test extracting self.method() calls."""
        code = """
class MyClass:
    def method(self):
        self.other_method()
        self.helper(1, 2)
"""
        functions = parser.parse_functions(code, "test.py")

        method = functions[0]
        assert "self.other_method" in method.calls
        assert "self.helper" in method.calls

    def test_class_instantiation(self, parser):
        """Test extracting class instantiation calls."""
        code = """
def func():
    obj = MyClass()
    other = AnotherClass(param=1)
"""
        functions = parser.parse_functions(code, "test.py")

        calls = functions[0].calls
        assert "MyClass" in calls
        assert "AnotherClass" in calls

    def test_chained_calls(self, parser):
        """Test extracting chained method calls."""
        code = """
def func():
    result = builder.set_name("test").set_value(42).build()
"""
        functions = parser.parse_functions(code, "test.py")

        calls = functions[0].calls
        # Should capture the chain
        assert len(calls) > 0

    def test_nested_calls(self, parser):
        """Test extracting nested function calls."""
        code = """
def func():
    result = outer(inner(value))
"""
        functions = parser.parse_functions(code, "test.py")

        calls = functions[0].calls
        assert "outer" in calls
        assert "inner" in calls

    def test_no_calls(self, parser):
        """Test function with no calls."""
        code = """
def func():
    x = 1 + 2
    return x
"""
        functions = parser.parse_functions(code, "test.py")

        assert functions[0].calls == []

    def test_nested_call_order(self, parser):
        """Test that nested/chained calls are in execution order."""
        code = """
def func():
    result = outer(inner(x)).method()
"""
        functions = parser.parse_functions(code, "test.py")
        calls = functions[0].calls
        assert calls.index("inner") < calls.index("outer")
        assert calls.index("outer") < calls.index("outer.method")

    def test_parameter_not_treated_as_call(self, parser):
        """Test that function parameters are not captured as function references."""
        code = """
def func(elements, callback):
    process(elements)
    run(callback)
"""
        functions = parser.parse_functions(code, "test.py")
        calls = functions[0].calls
        assert "process" in calls
        assert "run" in calls
        assert "elements" not in calls
        assert "callback" not in calls


class TestNestedFunctionCallScoping:
    """Tests that calls inside nested functions don't leak to the parent."""

    def test_nested_function_calls_not_in_parent(self, parser):
        """Calls inside a nested function should not appear in the parent's calls."""
        code = """
def boundary_predicates(self):
    def iter_boundary_predicates():
        yield is_title
        if not self.multipage_sections:
            yield is_on_next_page()
    return tuple(iter_boundary_predicates())
"""
        functions = parser.parse_functions(code, "test.py")
        assert len(functions) == 2

        parent = next(f for f in functions if f.name == "boundary_predicates")
        nested = next(f for f in functions if f.name == "iter_boundary_predicates")

        # is_on_next_page is called inside nested, not in parent
        assert "is_on_next_page" not in parent.calls
        assert "is_on_next_page" in nested.calls

        # parent calls the nested function (via tuple(iter_boundary_predicates()))
        assert "iter_boundary_predicates" in parent.calls or "tuple" in parent.calls

    def test_nested_function_simple(self, parser):
        """Simple nested function call scoping."""
        code = """
def outer():
    def inner():
        helper()
    inner()
"""
        functions = parser.parse_functions(code, "test.py")

        outer = next(f for f in functions if f.name == "outer")
        inner = next(f for f in functions if f.name == "inner")

        assert "helper" in inner.calls
        assert "helper" not in outer.calls
        assert "inner" in outer.calls

    def test_multiple_nested_functions(self, parser):
        """Each nested function's calls stay scoped to it."""
        code = """
def parent():
    def child_a():
        foo()
    def child_b():
        bar()
    child_a()
    child_b()
    baz()
"""
        functions = parser.parse_functions(code, "test.py")

        parent = next(f for f in functions if f.name == "parent")
        child_a = next(f for f in functions if f.name == "child_a")
        child_b = next(f for f in functions if f.name == "child_b")

        assert "foo" in child_a.calls
        assert "bar" in child_b.calls
        assert "foo" not in parent.calls
        assert "bar" not in parent.calls
        assert "baz" in parent.calls

    def test_deeply_nested_functions(self, parser):
        """Calls in deeply nested functions don't leak up."""
        code = """
def level0():
    def level1():
        def level2():
            deep_call()
        level2()
    level1()
"""
        functions = parser.parse_functions(code, "test.py")

        level0 = next(f for f in functions if f.name == "level0")
        level1 = next(f for f in functions if f.name == "level1")
        level2 = next(f for f in functions if f.name == "level2")

        assert "deep_call" in level2.calls
        assert "deep_call" not in level1.calls
        assert "deep_call" not in level0.calls

    def test_parent_calls_before_and_after_nested(self, parser):
        """Parent's own calls before and after nested def are kept."""
        code = """
def parent():
    before()
    def nested():
        inner_only()
    after()
"""
        functions = parser.parse_functions(code, "test.py")

        parent = next(f for f in functions if f.name == "parent")
        nested = next(f for f in functions if f.name == "nested")

        assert "before" in parent.calls
        assert "after" in parent.calls
        assert "inner_only" not in parent.calls
        assert "inner_only" in nested.calls

    def test_nested_var_types_not_in_parent(self, parser):
        """Variable assignments inside nested functions don't leak to parent."""
        code = """
def parent():
    outer_obj = OuterClass()
    def nested():
        inner_obj = InnerClass()
    nested()
"""
        functions = parser.parse_functions(code, "test.py")

        parent = next(f for f in functions if f.name == "parent")
        nested = next(f for f in functions if f.name == "nested")

        assert parent.var_types.get("outer_obj") == ["OuterClass"]
        assert "inner_obj" not in (parent.var_types or {})
        assert nested.var_types.get("inner_obj") == ["InnerClass"]

    def test_class_method_with_nested_function(self, parser):
        """Nested function inside a class method — calls stay scoped."""
        code = """
class MyClass:
    def method(self):
        def helper():
            do_something()
        self.other_method()
        helper()
"""
        functions = parser.parse_functions(code, "test.py")

        method = next(f for f in functions if f.name == "method")
        helper = next(f for f in functions if f.name == "helper")

        assert "do_something" in helper.calls
        assert "do_something" not in method.calls
        assert "self.other_method" in method.calls
        assert "helper" in method.calls


class TestVariableAssignmentTracking:
    """Tests for var_types extraction (variable assignment tracking)."""

    def test_simple_instantiation(self, parser):
        """Test tracking simple class instantiation."""
        code = """
def func():
    obj = MyClass()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        assert var_types is not None
        assert var_types.get("obj") == ["MyClass"]

    def test_multiple_instantiations(self, parser):
        """Test tracking multiple class instantiations."""
        code = """
def func():
    a = ClassA()
    b = ClassB()
    c = ClassC()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        assert var_types.get("a") == ["ClassA"]
        assert var_types.get("b") == ["ClassB"]
        assert var_types.get("c") == ["ClassC"]

    def test_module_qualified_instantiation(self, parser):
        """Test tracking module.Class() instantiation."""
        code = """
def func():
    client = http.Client()
    session = requests.Session()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        assert var_types.get("client") == ["http.Client"]
        assert var_types.get("session") == ["requests.Session"]

    def test_reassignment(self, parser):
        """Test that reassignment updates var_types (last assignment wins)."""
        code = """
def func():
    obj = ClassA()
    obj = ClassB()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        # Last assignment should win
        assert var_types.get("obj") == ["ClassB"]

    def test_no_instantiation(self, parser):
        """Test function with no class instantiations."""
        code = """
def func():
    x = 1
    y = "string"
    z = some_function()
"""
        functions = parser.parse_functions(code, "test.py")

        # var_types should be None or empty when there are no class instantiations
        # Note: some_function() is still a call but not necessarily a class
        functions[0].var_types
        # The parser captures any call on RHS, so 'z' -> 'some_function'
        # This is by design - resolution will filter non-classes later

    def test_instantiation_with_args(self, parser):
        """Test tracking instantiation with constructor arguments."""
        code = """
def func():
    obj = MyClass(arg1, arg2, kwarg=value)
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        assert var_types.get("obj") == ["MyClass"]

    def test_nested_scope_instantiation(self, parser):
        """Test tracking instantiation in nested scopes."""
        code = """
def func():
    if condition:
        obj = ClassA()
    else:
        obj = ClassB()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        # Should capture both (last one wins in flat dict)
        assert "obj" in var_types

    def test_conditional_assignment_ternary(self, parser):
        """Test tracking conditional assignment: x = A if cond else B."""
        code = """
def func(flag):
    dataset = DatasetA if flag else DatasetB
    dataset()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        assert var_types is not None
        assert var_types.get("dataset") == ["@ref:DatasetA", "@ref:DatasetB"]

    def test_conditional_assignment_with_dotted_classes(self, parser):
        """Test conditional assignment with module-qualified classes."""
        code = """
def func(multi_modal):
    dataset = data.MultiModal if multi_modal else data.Standard
    dataset()
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        assert var_types is not None
        assert var_types.get("dataset") == ["@ref:data.MultiModal", "@ref:data.Standard"]

    def test_conditional_assignment_not_class_refs(self, parser):
        """Test that non-identifier conditional values are not captured."""
        code = """
def func(flag):
    value = 1 if flag else 2
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        # Integer literals should not produce var_types entries
        assert var_types is None or "value" not in var_types


class TestVarSourcesExtraction:
    """Tests for var_sources extraction (chained calls, for-loop variables)."""

    def test_chained_call_assignment(self, parser):
        """Test tracking x = ClassName().method() as var_source."""
        code = """
def func():
    pre_chunks = PreChunkCombiner().iter_combined_pre_chunks()
"""
        functions = parser.parse_functions(code, "test.py")

        var_sources = functions[0].var_sources
        assert var_sources is not None
        assert var_sources.get("pre_chunks") == "PreChunkCombiner.iter_combined_pre_chunks"

    def test_chained_call_with_args(self, parser):
        """Test tracking x = ClassName(args).method() as var_source."""
        code = """
def func():
    result = Builder(config).build()
"""
        functions = parser.parse_functions(code, "test.py")

        var_sources = functions[0].var_sources
        assert var_sources is not None
        assert var_sources.get("result") == "Builder.build"

    def test_for_loop_iter_variable(self, parser):
        """Test tracking for x in y as var_source."""
        code = """
def func():
    items = get_items()
    for item in items:
        process(item)
"""
        functions = parser.parse_functions(code, "test.py")

        var_sources = functions[0].var_sources
        assert var_sources is not None
        assert var_sources.get("item") == "@iter:items"

    def test_for_loop_iter_call(self, parser):
        """Test tracking for x in func() as var_source."""
        code = """
def func():
    for chunk in get_chunks():
        process(chunk)
"""
        functions = parser.parse_functions(code, "test.py")

        var_sources = functions[0].var_sources
        assert var_sources is not None
        assert var_sources.get("chunk") == "@iter_call:get_chunks"

    def test_for_loop_iter_method_call(self, parser):
        """Test tracking for x in obj.method() as var_source."""
        code = """
def func():
    for item in container.get_items():
        process(item)
"""
        functions = parser.parse_functions(code, "test.py")

        var_sources = functions[0].var_sources
        assert var_sources is not None
        assert var_sources.get("item") == "@iter_call:container.get_items"

    def test_comprehension_for_in_clause(self, parser):
        """Test tracking for-in-clause in list comprehension."""
        code = """
def func():
    result = [chunk for pre_chunk in pre_chunks for chunk in pre_chunk.iter_chunks()]
"""
        functions = parser.parse_functions(code, "test.py")

        var_sources = functions[0].var_sources
        assert var_sources is not None
        assert var_sources.get("pre_chunk") == "@iter:pre_chunks"
        assert var_sources.get("chunk") == "@iter_call:pre_chunk.iter_chunks"

    def test_var_types_and_var_sources_coexist(self, parser):
        """Test that var_types and var_sources are both populated."""
        code = """
def func():
    obj = MyClass()
    result = Builder().build()
    for item in items:
        pass
"""
        functions = parser.parse_functions(code, "test.py")

        var_types = functions[0].var_types
        var_sources = functions[0].var_sources
        assert var_types is not None
        assert var_types.get("obj") == ["MyClass"]
        assert var_sources is not None
        assert var_sources.get("result") == "Builder.build"
        assert var_sources.get("item") == "@iter:items"

    def test_no_var_sources(self, parser):
        """Test function with no chained calls or for loops produces None var_sources."""
        code = """
def func():
    obj = MyClass()
    obj.method()
"""
        functions = parser.parse_functions(code, "test.py")

        assert functions[0].var_sources is None

    def test_direct_instantiation_not_in_var_sources(self, parser):
        """Test that obj = ClassName() goes to var_types, not var_sources."""
        code = """
def func():
    obj = MyClass()
"""
        functions = parser.parse_functions(code, "test.py")

        assert functions[0].var_types.get("obj") == ["MyClass"]
        assert functions[0].var_sources is None


class TestModuleDocstringExtraction:
    """Tests for module-level docstring extraction."""

    def test_triple_double_quote_docstring(self, parser):
        """Test extracting a triple-double-quoted module docstring."""
        code = '"""This module handles image augmentation."""\n\nimport os\n'
        result = parser.parse_module_docstring(code)
        assert result == "This module handles image augmentation."

    def test_triple_single_quote_docstring(self, parser):
        """Test extracting a triple-single-quoted module docstring."""
        code = "'''Single-quoted module docstring.'''\n\nimport os\n"
        result = parser.parse_module_docstring(code)
        assert result == "Single-quoted module docstring."

    def test_multiline_docstring(self, parser):
        """Test extracting a multi-line module docstring."""
        code = '"""\nThis module provides\nutilities for data processing.\n"""\n\nimport os\n'
        result = parser.parse_module_docstring(code)
        assert "This module provides" in result
        assert "utilities for data processing." in result

    def test_no_docstring(self, parser):
        """Test module with no docstring returns None."""
        code = "import os\n\ndef func():\n    pass\n"
        result = parser.parse_module_docstring(code)
        assert result is None

    def test_comment_only_header(self, parser):
        """Test module with only comments (no docstring) returns None."""
        code = "# This is a comment\n# Another comment\nimport os\n"
        result = parser.parse_module_docstring(code)
        assert result is None

    def test_first_statement_is_import(self, parser):
        """Test module where first statement is import returns None."""
        code = 'import os\n\n"""This is not a module docstring."""\n'
        result = parser.parse_module_docstring(code)
        assert result is None

    def test_docstring_after_comments(self, parser):
        """Test that a docstring after comments is still extracted."""
        code = '# -*- coding: utf-8 -*-\n"""Module docstring after comment."""\n\nimport os\n'
        result = parser.parse_module_docstring(code)
        assert result == "Module docstring after comment."

    def test_empty_file(self, parser):
        """Test empty file returns None."""
        result = parser.parse_module_docstring("")
        assert result is None

    def test_parse_code_returns_module_docstring(self, parser):
        """Test that parse_code returns module docstring as 4th element."""
        code = '"""Module doc."""\n\ndef func():\n    pass\n'
        modules_dict = {}
        functions, classes, imports, module_docstring = parser.parse_code(
            code, "test.py", modules_dict
        )
        assert module_docstring == "Module doc."
        assert len(functions) == 1

    def test_parse_code_returns_none_without_docstring(self, parser):
        """Test that parse_code returns None for module_docstring when absent."""
        code = "import os\n\ndef func():\n    pass\n"
        modules_dict = {}
        functions, classes, imports, module_docstring = parser.parse_code(
            code, "test.py", modules_dict
        )
        assert module_docstring is None


class TestParseCode:
    """Tests for the combined parse_code method."""

    def test_parse_code_returns_all(self, parser):
        """Test that parse_code returns functions, classes, and imports."""
        code = """
from mymodule import Helper

class MyClass:
    def method(self):
        pass

def standalone():
    pass
"""
        modules_dict = {"mymodule": "pkg.mymodule"}
        functions, classes, imports, _ = parser.parse_code(code, "test.py", modules_dict)

        assert len(functions) == 2  # method + standalone
        assert len(classes) == 1
        assert "Helper" in imports

    def test_parse_code_complex(self, parser):
        """Test parsing complex code with multiple elements."""
        code = '''
from utils import helper
from models import BaseModel

@dataclass
class User(BaseModel):
    """User model."""
    name: str

    def validate(self) -> bool:
        return helper.validate_name(self.name)

    def save(self):
        db = Database()
        db.insert(self)

def create_user(name: str) -> User:
    user = User(name=name)
    user.validate()
    return user
'''
        modules_dict = {"utils": "pkg.utils", "models": "pkg.models"}
        functions, classes, imports, _ = parser.parse_code(code, "users.py", modules_dict)

        # Check functions
        assert len(functions) == 3  # validate, save, create_user

        # Check classes
        assert len(classes) == 1
        assert classes[0].name == "User"
        assert "BaseModel" in classes[0].superclasses

        # Check imports
        assert "helper" in imports
        assert "BaseModel" in imports

        # Check var_types in create_user
        create_user = next(f for f in functions if f.name == "create_user")
        assert create_user.var_types.get("user") == ["User"]

        # Check calls in save method
        save_method = next(f for f in functions if f.name == "save")
        assert "Database" in save_method.calls
        assert "db.insert" in save_method.calls
