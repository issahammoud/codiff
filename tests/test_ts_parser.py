"""Tests for the TypeScript parser (TypeScriptParser / TypeScriptXParser).

Structure mirrors test_code_parser.py so that Python and TypeScript
parser behaviour can be compared side-by-side.
"""

import pytest

from codiff.parsers.typescript_parser import TypeScriptParser, TypeScriptXParser


@pytest.fixture
def parser():
    return TypeScriptParser()


@pytest.fixture
def tsx_parser():
    return TypeScriptXParser()


# ---------------------------------------------------------------------------
# Function parsing
# ---------------------------------------------------------------------------


class TestFunctionParsing:
    def test_simple_function(self, parser):
        code = "function hello(): void { }"
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        func = functions[0]
        assert func.name == "hello"
        assert func.file_path == "test.ts"
        assert func.class_name is None
        assert func.parameters == []

    def test_function_with_parameters(self, parser):
        code = "function process(a: string, b: number, c: boolean): void { }"
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        func = functions[0]
        assert func.name == "process"
        assert len(func.parameters) == 3
        names = [p.name for p in func.parameters]
        assert names == ["a", "b", "c"]
        assert func.parameters[0].type == "string"
        assert func.parameters[1].type == "number"
        assert func.parameters[2].type == "boolean"

    def test_function_with_return_type(self, parser):
        code = "function getValue(): number { return 42; }"
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        assert functions[0].return_type == "number"

    def test_function_with_void_return(self, parser):
        code = "function doWork(): void { }"
        functions = parser.parse_functions(code, "test.ts")
        assert functions[0].return_type == "void"

    def test_function_with_complex_return_type(self, parser):
        code = "function getItems(): Array<string> { return []; }"
        functions = parser.parse_functions(code, "test.ts")
        assert "Array" in functions[0].return_type or "string" in functions[0].return_type

    def test_class_method(self, parser):
        code = """
class MyClass {
    greet(): void { }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        func = functions[0]
        assert func.name == "greet"
        assert func.class_name == "MyClass"

    def test_multiple_class_methods(self, parser):
        code = """
class MyClass {
    constructor(name: string) { }
    methodA(): void { }
    methodB(x: number): string { return String(x); }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 3
        for func in functions:
            assert func.class_name == "MyClass"

    def test_constructor_parsed(self, parser):
        code = """
class MyClass {
    constructor(private name: string) { }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        assert functions[0].name == "constructor"
        assert functions[0].class_name == "MyClass"

    def test_constructor_accessibility_modifier_param(self, parser):
        code = """
class MyClass {
    constructor(public name: string, private age: number) { }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        func = functions[0]
        # Parameters with accessibility modifiers should still be extracted
        param_names = [p.name for p in func.parameters]
        assert "name" in param_names
        assert "age" in param_names

    def test_function_id_generation(self, parser):
        code = """
function standalone(): void { }

class MyClass {
    method(): void { }
}
"""
        functions = parser.parse_functions(code, "module/submodule.ts")
        standalone = next(f for f in functions if f.name == "standalone")
        method = next(f for f in functions if f.name == "method")
        assert standalone.id == "module.submodule.standalone"
        assert method.id == "module.submodule.MyClass.method"

    def test_function_line_numbers(self, parser):
        code = "function first(): void { }\n\nfunction second(): void { }\n"
        functions = parser.parse_functions(code, "test.ts")
        first = next(f for f in functions if f.name == "first")
        second = next(f for f in functions if f.name == "second")
        assert first.start_line == 1
        assert second.start_line == 3

    def test_multiple_top_level_functions(self, parser):
        code = """
function alpha(): void { }
function beta(): void { }
function gamma(): void { }
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 3
        names = {f.name for f in functions}
        assert names == {"alpha", "beta", "gamma"}


# ---------------------------------------------------------------------------
# Class parsing
# ---------------------------------------------------------------------------


class TestClassParsing:
    def test_simple_class(self, parser):
        code = "class MyClass { }"
        classes = parser.parse_classes(code, "test.ts")
        assert len(classes) == 1
        cls = classes[0]
        assert cls.name == "MyClass"
        assert cls.superclasses == []

    def test_class_with_inheritance(self, parser):
        code = "class Child extends Parent { }"
        classes = parser.parse_classes(code, "test.ts")
        assert len(classes) == 1
        assert "Parent" in classes[0].superclasses

    def test_class_without_inheritance(self, parser):
        code = "class Standalone { method(): void { } }"
        classes = parser.parse_classes(code, "test.ts")
        assert len(classes) == 1
        assert classes[0].superclasses == []

    def test_class_id_generation(self, parser):
        code = "class MyClass { }"
        classes = parser.parse_classes(code, "pkg/module.ts")
        assert classes[0].id == "pkg.module.MyClass"

    def test_multiple_classes(self, parser):
        code = """
class ClassA { }
class ClassB extends ClassA { }
"""
        classes = parser.parse_classes(code, "test.ts")
        assert len(classes) == 2
        names = {c.name for c in classes}
        assert names == {"ClassA", "ClassB"}
        b = next(c for c in classes if c.name == "ClassB")
        assert "ClassA" in b.superclasses

    def test_class_file_path(self, parser):
        code = "class Foo { }"
        classes = parser.parse_classes(code, "src/models/foo.ts")
        assert classes[0].file_path == "src/models/foo.ts"

    def test_class_start_end_lines(self, parser):
        code = "class Foo {\n    method(): void { }\n}\n"
        classes = parser.parse_classes(code, "test.ts")
        assert classes[0].start_line == 1
        assert classes[0].end_line == 3


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


class TestCallExtraction:
    def test_simple_function_call(self, parser):
        code = """
function caller(): void {
    helper();
    greet("world");
}
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        calls = functions[0].calls
        assert "helper" in calls
        assert "greet" in calls

    def test_this_method_call(self, parser):
        code = """
class MyClass {
    process(): void {
        this.validate();
        this.save();
    }
    validate(): void { }
    save(): void { }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        process = next(f for f in functions if f.name == "process")
        assert "this.validate" in process.calls
        assert "this.save" in process.calls

    def test_new_expression_in_call_list(self, parser):
        code = """
function create(): void {
    const obj = new MyClass();
}
"""
        functions = parser.parse_functions(code, "test.ts")
        assert len(functions) == 1
        calls = functions[0].calls
        assert "MyClass" in calls

    def test_super_call_in_constructor(self, parser):
        code = """
class Child extends Parent {
    constructor() {
        super();
    }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        ctor = next(f for f in functions if f.name == "constructor")
        assert "super" in ctor.calls

    def test_super_method_call(self, parser):
        code = """
class Child extends Parent {
    speak(): void {
        super.speak();
    }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        speak = next(f for f in functions if f.name == "speak")
        assert "super.speak" in speak.calls

    def test_member_expression_call(self, parser):
        code = """
function run(): void {
    service.process();
    logger.info("done");
}
"""
        functions = parser.parse_functions(code, "test.ts")
        calls = functions[0].calls
        assert "service.process" in calls
        assert "logger.info" in calls

    def test_no_calls(self, parser):
        code = "function empty(): void { const x = 1; }"
        functions = parser.parse_functions(code, "test.ts")
        # No function calls (new or call_expression pointing to functions)
        # x = 1 is a literal assignment, not a call
        assert "x" not in functions[0].calls

    def test_nested_method_calls_scoped(self, parser):
        code = """
class Service {
    execute(): void {
        this.helper();
    }
    helper(): void {
        this.internal();
    }
    internal(): void { }
}
"""
        functions = parser.parse_functions(code, "test.ts")
        execute = next(f for f in functions if f.name == "execute")
        helper = next(f for f in functions if f.name == "helper")
        assert "this.helper" in execute.calls
        assert "this.internal" in helper.calls
        assert "this.internal" not in execute.calls


# ---------------------------------------------------------------------------
# Variable type tracking
# ---------------------------------------------------------------------------


class TestVarTypesExtraction:
    def test_const_new_expression(self, parser):
        code = """
function create(): void {
    const obj = new MyService();
}
"""
        functions = parser.parse_functions(code, "test.ts")
        var_types = functions[0].var_types
        assert var_types is not None
        assert var_types.get("obj") == ["MyService"]

    def test_let_new_expression(self, parser):
        code = """
function run(): void {
    let repo = new Repository();
}
"""
        functions = parser.parse_functions(code, "test.ts")
        var_types = functions[0].var_types
        assert var_types is not None
        assert var_types.get("repo") == ["Repository"]

    def test_multiple_new_expressions(self, parser):
        code = """
function build(): void {
    const svc = new Service();
    const repo = new Repository();
}
"""
        functions = parser.parse_functions(code, "test.ts")
        var_types = functions[0].var_types
        assert var_types.get("svc") == ["Service"]
        assert var_types.get("repo") == ["Repository"]

    def test_no_new_expression(self, parser):
        code = """
function pure(): void {
    const x = 42;
    const s = "hello";
}
"""
        functions = parser.parse_functions(code, "test.ts")
        # var_types should be None or empty (no class instantiations)
        assert not functions[0].var_types


# ---------------------------------------------------------------------------
# parse_code integration
# ---------------------------------------------------------------------------


class TestParseCode:
    def test_parse_code_returns_all(self, parser):
        code = """
class MyClass {
    method(): void { }
}
function standalone(): void { }
"""
        functions, classes, imports, docstring = parser.parse_code(code, "test.ts", {})
        assert len(functions) == 2
        assert len(classes) == 1
        assert docstring is None  # TypeScript has no string-literal module docstrings

    def test_parse_code_class_and_function_together(self, parser):
        code = """
class Animal {
    constructor(public name: string) { }
    speak(): void { }
}

function createAnimal(name: string): Animal {
    return new Animal(name);
}
"""
        functions, classes, imports, _ = parser.parse_code(code, "animals.ts", {})
        assert len(classes) == 1
        assert classes[0].name == "Animal"
        func_names = {f.name for f in functions}
        assert "speak" in func_names
        assert "createAnimal" in func_names
        create = next(f for f in functions if f.name == "createAnimal")
        assert "Animal" in create.calls


# ---------------------------------------------------------------------------
# TSX parser
# ---------------------------------------------------------------------------


class TestTsxParser:
    def test_tsx_simple_function(self, tsx_parser):
        code = "function Component(): void { }"
        functions = tsx_parser.parse_functions(code, "App.tsx")
        assert len(functions) == 1
        assert functions[0].name == "Component"

    def test_tsx_class(self, tsx_parser):
        code = "class MyComponent extends React.Component { render(): void { } }"
        classes = tsx_parser.parse_classes(code, "App.tsx")
        assert len(classes) == 1
        assert classes[0].name == "MyComponent"

    def test_tsx_extension(self, tsx_parser):
        assert tsx_parser.extension == ".tsx"

    def test_tsx_file_to_module_id(self, tsx_parser):
        assert tsx_parser.file_to_module_id("src/App.tsx") == "src.App"

    def test_tsx_function_id(self, tsx_parser):
        code = "function App(): void { }"
        functions = tsx_parser.parse_functions(code, "src/App.tsx")
        assert functions[0].id == "src.App.App"
