"""
Comprehensive tests for ALL Python call patterns.

This file tests every possible way a function/method can be called in Python
to ensure the call resolver handles all cases correctly.
"""

import pytest

from codiff.languages import CodeParser, resolve_internal_calls
from codiff.schema.parsing import ClassChunk, FunctionChunk


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
        return_type=None,
        calls=calls or [],
        var_types=var_types,
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


# =============================================================================
# CHAINED METHOD CALLS
# =============================================================================


class TestChainedMethodCalls:
    """Tests for method chaining patterns like obj.method1().method2()."""

    def test_fluent_api_chain(self, parser):
        """Test fluent API pattern: builder.set_a().set_b().build()."""
        code = """
class Builder:
    def __init__(self):
        pass

    def set_name(self):
        return self

    def set_value(self):
        return self

    def build(self):
        return Result()

class Result:
    def __init__(self):
        pass

def create():
    builder = Builder()
    result = builder.set_name().set_value().build()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create_func = next(f for f in resolved if f.name == "create")
        # Should resolve Builder.__init__ and the chained methods
        assert "module.Builder.__init__" in create_func.calls
        assert "module.Builder.set_name" in create_func.calls
        assert "module.Builder.set_value" in create_func.calls
        assert "module.Builder.build" in create_func.calls

    def test_method_chain_on_return_value(self, parser):
        """Test calling method on returned object: get_obj().method()."""
        code = """
class Processor:
    def __init__(self):
        pass

    def process(self):
        pass

def get_processor():
    return Processor()

def use():
    get_processor().process()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use")
        # Should resolve get_processor call
        assert "module.get_processor" in use_func.calls
        # process() call depends on return type inference

    def test_deep_attribute_chain(self, parser):
        """Test deep attribute chain: a.b.c.d.method()."""
        code = """
class Leaf:
    def action(self):
        pass

def deep_access(obj):
    obj.level1.level2.level3.action()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "deep_access")
        # Should try to resolve 'action' via fallback if unique
        assert "module.Leaf.action" in func.calls


# =============================================================================
# DECORATOR CALLS
# =============================================================================


class TestDecoratorCalls:
    """Tests for decorator call relationships.

    A decorator CALLS the decorated function, not the inverse.
    @my_decorator on func is equivalent to func = my_decorator(func),
    so my_decorator's calls should include the decorated function.
    The decorated function does NOT call the decorator.
    """

    def test_decorated_function_does_not_call_decorator(self, parser):
        """Test that the decorated function does NOT list the decorator in its calls."""
        code = """
def my_decorator(func):
    return func

@my_decorator
def decorated_function():
    pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        decorated = next(f for f in resolved if f.name == "decorated_function")
        assert "module.my_decorator" not in decorated.calls

    def test_parametrized_decorator_not_in_decorated_calls(self, parser):
        """Test @decorator(args) — decorated function does NOT call the decorator."""
        code = """
def parametrized_decorator(param):
    def wrapper(func):
        return func
    return wrapper

@parametrized_decorator("value")
def decorated():
    pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        decorated = next(f for f in resolved if f.name == "decorated")
        assert "module.parametrized_decorator" not in decorated.calls

    def test_class_decorator(self, parser):
        """Test @decorator on class — just verifies no crash."""
        code = """
def class_decorator(cls):
    return cls

@class_decorator
class MyClass:
    def __init__(self):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)

    def test_method_decorator_not_in_decorated_calls(self, parser):
        """Test decorators on methods — decorated method does NOT call the decorator."""
        code = """
def method_decorator(func):
    return func

class MyClass:
    @method_decorator
    def decorated_method(self):
        pass

    @staticmethod
    def static_method():
        pass

    @classmethod
    def class_method(cls):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        decorated = next(f for f in resolved if f.name == "decorated_method")
        assert "module.method_decorator" not in decorated.calls

    def test_multiple_decorators_not_in_decorated_calls(self, parser):
        """Test stacked decorators — decorated function does NOT call any decorator."""
        code = """
def decorator1(func):
    return func

def decorator2(func):
    return func

def decorator3(func):
    return func

@decorator1
@decorator2
@decorator3
def multi_decorated():
    pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "multi_decorated")
        assert "module.decorator1" not in func.calls
        assert "module.decorator2" not in func.calls
        assert "module.decorator3" not in func.calls


# =============================================================================
# CONTEXT MANAGERS
# =============================================================================


class TestContextManagerCalls:
    """Tests for with statement and context manager calls."""

    def test_context_manager_class(self, parser):
        """Test with ContextManager() as ctx."""
        code = """
class FileHandler:
    def __init__(self, path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        pass

def process_file():
    with FileHandler("file.txt") as f:
        f.read()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "process_file")
        # Should resolve FileHandler instantiation
        assert "module.FileHandler.__init__" in func.calls
        # Should resolve read() call on the context variable
        assert "module.FileHandler.read" in func.calls

    def test_multiple_context_managers(self, parser):
        """Test with A() as a, B() as b."""
        code = """
class ManagerA:
    def __init__(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass

class ManagerB:
    def __init__(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass

def multi_context():
    with ManagerA() as a, ManagerB() as b:
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "multi_context")
        assert "module.ManagerA.__init__" in func.calls
        assert "module.ManagerB.__init__" in func.calls

    def test_contextlib_contextmanager(self, parser):
        """Test @contextmanager decorated generator."""
        code = """
def my_context():
    setup()
    yield
    teardown()

def setup():
    pass

def teardown():
    pass

def use_context():
    with my_context():
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use_context")
        assert "module.my_context" in use_func.calls

        ctx_func = next(f for f in resolved if f.name == "my_context")
        assert "module.setup" in ctx_func.calls
        assert "module.teardown" in ctx_func.calls


# =============================================================================
# ASYNC CALLS
# =============================================================================


class TestAsyncCalls:
    """Tests for async/await call patterns."""

    def test_await_async_function(self, parser):
        """Test await async_func()."""
        code = """
async def fetch_data():
    pass

async def process():
    data = await fetch_data()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        process_func = next(f for f in resolved if f.name == "process")
        assert "module.fetch_data" in process_func.calls

    def test_await_method_call(self, parser):
        """Test await obj.async_method()."""
        code = """
class AsyncService:
    def __init__(self):
        pass

    async def fetch(self):
        pass

async def use_service():
    service = AsyncService()
    await service.fetch()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_service")
        assert "module.AsyncService.__init__" in func.calls
        assert "module.AsyncService.fetch" in func.calls

    def test_async_for(self, parser):
        """Test async for iteration."""
        code = """
class AsyncIterator:
    def __init__(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        pass

async def iterate():
    async for item in AsyncIterator():
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "iterate")
        assert "module.AsyncIterator.__init__" in func.calls

    def test_async_with(self, parser):
        """Test async with context manager."""
        code = """
class AsyncContext:
    def __init__(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

async def use_async_context():
    async with AsyncContext() as ctx:
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_async_context")
        assert "module.AsyncContext.__init__" in func.calls


# =============================================================================
# CLASS METHOD AND STATIC METHOD CALLS
# =============================================================================


class TestClassMethodCalls:
    """Tests for @classmethod and @staticmethod calls."""

    def test_classmethod_from_class(self, parser):
        """Test Class.class_method()."""
        code = """
class Factory:
    @classmethod
    def create(cls):
        return cls()

    def __init__(self):
        pass

def use_factory():
    obj = Factory.create()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use_factory")
        assert "module.Factory.create" in use_func.calls

    def test_classmethod_from_instance(self, parser):
        """Test instance.class_method() (valid in Python)."""
        code = """
class MyClass:
    def __init__(self):
        pass

    @classmethod
    def class_method(cls):
        pass

def use():
    obj = MyClass()
    obj.class_method()  # Valid: calling classmethod on instance
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use")
        assert "module.MyClass.__init__" in use_func.calls
        assert "module.MyClass.class_method" in use_func.calls

    def test_staticmethod_call(self, parser):
        """Test Class.static_method() and instance.static_method()."""
        code = """
class Utils:
    @staticmethod
    def helper(x):
        return x * 2

    def __init__(self):
        pass

def use_static():
    Utils.helper(5)  # From class
    obj = Utils()
    obj.helper(10)   # From instance (also valid)
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use_static")
        # Both should resolve to Utils.helper
        assert "module.Utils.helper" in use_func.calls

    def test_classmethod_calling_other_classmethod(self, parser):
        """Test classmethod calling another classmethod via cls."""
        code = """
class Builder:
    @classmethod
    def create_default(cls):
        return cls.create_with_config({})

    @classmethod
    def create_with_config(cls, config):
        return cls()

    def __init__(self):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        create_default = next(f for f in resolved if f.name == "create_default")
        # cls.create_with_config should resolve
        assert "module.Builder.create_with_config" in create_default.calls


# =============================================================================
# NESTED CLASSES
# =============================================================================


class TestNestedClassCalls:
    """Tests for nested class instantiation and method calls."""

    def test_nested_class_instantiation(self, parser):
        """Test Outer.Inner() instantiation."""
        code = """
class Outer:
    class Inner:
        def __init__(self):
            pass

        def inner_method(self):
            pass

def use_nested():
    inner = Outer.Inner()
    inner.inner_method()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        next(f for f in resolved if f.name == "use_nested")
        # Should resolve to Outer.Inner.__init__
        # Note: This depends on how nested classes are parsed

    def test_outer_creates_inner(self, parser):
        """Test outer class creating inner class instance."""
        code = """
class Outer:
    def __init__(self):
        self.inner = self.Inner()

    class Inner:
        def __init__(self):
            pass

def create_outer():
    return Outer()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        next(f for f in resolved if f.name == "__init__" and f.class_name == "Outer")
        # self.Inner() should resolve


# =============================================================================
# EXCEPTION HANDLING
# =============================================================================


class TestExceptionCalls:
    """Tests for exception raising and handling calls."""

    def test_raise_custom_exception(self, parser):
        """Test raise CustomError()."""
        code = """
class CustomError(Exception):
    def __init__(self, message):
        super().__init__(message)

def might_fail():
    raise CustomError("Something went wrong")
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "might_fail")
        # raise CustomError() should resolve to CustomError.__init__
        assert "module.CustomError.__init__" in func.calls

    def test_exception_with_args(self, parser):
        """Test raise CustomError(arg1, arg2)."""
        code = """
class ValidationError(Exception):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)

def validate(data):
    if not data:
        raise ValidationError("name", "Name is required")
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "validate")
        assert "module.ValidationError.__init__" in func.calls

    def test_exception_handler_call(self, parser):
        """Test calls inside except block."""
        code = """
def handle_error(error):
    pass

def log_error(error):
    pass

def risky_operation():
    try:
        do_something()
    except ValueError as e:
        handle_error(e)
        log_error(e)

def do_something():
    pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "risky_operation")
        assert "module.do_something" in func.calls
        assert "module.handle_error" in func.calls
        assert "module.log_error" in func.calls


# =============================================================================
# MULTIPLE ASSIGNMENT CALLS
# =============================================================================


class TestMultipleAssignmentCalls:
    """Tests for calls in multiple assignment contexts."""

    def test_tuple_unpacking_calls(self, parser):
        """Test a, b = func1(), func2()."""
        code = """
def get_first():
    return 1

def get_second():
    return 2

def unpack():
    a, b = get_first(), get_second()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "unpack")
        assert "module.get_first" in func.calls
        assert "module.get_second" in func.calls

    def test_function_returning_tuple(self, parser):
        """Test a, b = func_returning_tuple()."""
        code = """
def get_pair():
    return (1, 2)

def use_pair():
    a, b = get_pair()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_pair")
        assert "module.get_pair" in func.calls

    def test_starred_unpacking(self, parser):
        """Test a, *rest = func()."""
        code = """
def get_many():
    return [1, 2, 3, 4, 5]

def use_starred():
    first, *rest = get_many()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_starred")
        assert "module.get_many" in func.calls


# =============================================================================
# DEFAULT ARGUMENT CALLS
# =============================================================================


class TestDefaultArgumentCalls:
    """Tests for calls in default argument values."""

    def test_function_call_as_default(self, parser):
        """Test def func(x=default_func())."""
        code = """
def get_default():
    return 42

def with_default(x=get_default()):
    return x
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        # Default arguments are evaluated at function definition time
        # The parser may or may not capture this as a call
        next(f for f in resolved if f.name == "with_default")
        # This depends on implementation - default args are tricky

    def test_class_instantiation_as_default(self, parser):
        """Test def func(config=Config())."""
        code = """
class Config:
    def __init__(self):
        pass

def process(config=Config()):
    pass
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)
        # Default is evaluated once at definition, may or may not be tracked


# =============================================================================
# COMPREHENSION VARIATIONS
# =============================================================================


class TestComprehensionCalls:
    """Tests for calls in various comprehension types."""

    def test_list_comprehension_call(self, parser):
        """Test [func(x) for x in items]."""
        code = """
def transform(x):
    return x * 2

def process_list():
    items = [1, 2, 3]
    result = [transform(x) for x in items]
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "process_list")
        assert "module.transform" in func.calls

    def test_set_comprehension_call(self, parser):
        """Test {func(x) for x in items}."""
        code = """
def normalize(x):
    return x.lower()

def unique_normalized():
    words = ["Hello", "WORLD", "hello"]
    result = {normalize(w) for w in words}
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "unique_normalized")
        assert "module.normalize" in func.calls

    def test_dict_comprehension_call(self, parser):
        """Test {key_func(x): value_func(x) for x in items}."""
        code = """
def get_key(item):
    return item.id

def get_value(item):
    return item.name

def build_dict():
    items = []
    result = {get_key(i): get_value(i) for i in items}
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "build_dict")
        assert "module.get_key" in func.calls
        assert "module.get_value" in func.calls

    def test_generator_expression_call(self, parser):
        """Test (func(x) for x in items)."""
        code = """
def expensive_transform(x):
    return x ** 2

def lazy_process():
    items = range(1000000)
    gen = (expensive_transform(x) for x in items)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "lazy_process")
        assert "module.expensive_transform" in func.calls

    def test_nested_comprehension_calls(self, parser):
        """Test [[inner_func(y) for y in outer_func(x)] for x in items]."""
        code = """
def outer_func(x):
    return range(x)

def inner_func(y):
    return y * 2

def nested_process():
    items = [1, 2, 3]
    result = [[inner_func(y) for y in outer_func(x)] for x in items]
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "nested_process")
        assert "module.outer_func" in func.calls
        assert "module.inner_func" in func.calls

    def test_comprehension_with_condition_call(self, parser):
        """Test [x for x in items if filter_func(x)]."""
        code = """
def is_valid(x):
    return x > 0

def filter_items():
    items = [-1, 0, 1, 2]
    result = [x for x in items if is_valid(x)]
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "filter_items")
        assert "module.is_valid" in func.calls


# =============================================================================
# BOUND METHOD AS VARIABLE
# =============================================================================


class TestBoundMethodCalls:
    """Tests for storing and calling bound methods."""

    def test_bound_method_call(self, parser):
        """Test m = obj.method; m()."""
        code = """
class Processor:
    def __init__(self):
        pass

    def process(self, data):
        pass

def use_bound():
    proc = Processor()
    method = proc.process
    method("data")
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        next(f for f in resolved if f.name == "use_bound")
        # proc.process access and method("data") call
        # This may or may not resolve depending on implementation

    def test_pass_method_as_callback(self, parser):
        """Test passing bound method as callback."""
        code = """
class Handler:
    def __init__(self):
        pass

    def on_event(self, event):
        pass

def register(callback):
    pass

def setup():
    handler = Handler()
    register(handler.on_event)
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "setup")
        assert "module.Handler.__init__" in func.calls
        assert "module.register" in func.calls


# =============================================================================
# IMPORT ALIAS CALLS
# =============================================================================


class TestImportAliasCalls:
    """Tests for calls using import aliases."""

    def test_module_alias_call(self, parser):
        """Test import module as m; m.func()."""
        code = """
def my_function():
    pass
"""
        # Simulating: import mymodule as m; m.my_function()
        functions = parser.parse_functions(code, "mymodule.py")
        caller = create_function_chunk(name="caller", file_path="main.py", calls=["m.my_function"])
        functions.append(caller)

        modules_dict = {"mymodule": "mymodule", "main": "main"}
        imports = {"m": "mymodule"}

        resolved = resolve_internal_calls(functions, [], imports, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        # m.my_function should resolve to mymodule.my_function
        assert "mymodule.my_function" in caller_func.calls

    def test_from_import_alias(self, parser):
        """Test from module import func as f; f()."""
        code = """
def original_func():
    pass
"""
        functions = parser.parse_functions(code, "utils.py")
        caller = create_function_chunk(name="caller", file_path="main.py", calls=["f"])
        functions.append(caller)

        modules_dict = {"utils": "utils", "main": "main"}
        imports = {"f": "utils.original_func"}

        resolved = resolve_internal_calls(functions, [], imports, modules_dict)

        caller_func = next(f for f in resolved if f.name == "caller")
        assert "utils.original_func" in caller_func.calls

    def test_relative_import_same_package(self, parser):
        """Test from .utils import func (relative import from same package)."""
        utils_code = """
def verify_image():
    pass
"""
        dataset_code = """
from .utils import verify_image

def process():
    verify_image()
"""
        utils_funcs = parser.parse_functions(utils_code, "data/utils.py")
        dataset_funcs = parser.parse_functions(dataset_code, "data/dataset.py")

        modules_dict = {
            "data.utils": "data.utils",
            "data.dataset": "data.dataset",
        }

        imports = parser.parse_imports(dataset_code, modules_dict, "data/dataset.py")
        all_functions = utils_funcs + dataset_funcs

        resolved = resolve_internal_calls(all_functions, [], imports, modules_dict)

        process_func = next(f for f in resolved if f.name == "process")
        assert "data.utils.verify_image" in process_func.calls

    def test_relative_import_parent_package(self, parser):
        """Test from ..core import func (relative import from parent package)."""
        core_code = """
def base_func():
    pass
"""
        submodule_code = """
from ..core import base_func

def call_base():
    base_func()
"""
        core_funcs = parser.parse_functions(core_code, "pkg/core.py")
        submodule_funcs = parser.parse_functions(submodule_code, "pkg/sub/module.py")

        modules_dict = {
            "pkg.core": "pkg.core",
            "pkg.sub.module": "pkg.sub.module",
        }

        imports = parser.parse_imports(submodule_code, modules_dict, "pkg/sub/module.py")
        all_functions = core_funcs + submodule_funcs

        resolved = resolve_internal_calls(all_functions, [], imports, modules_dict)

        call_base_func = next(f for f in resolved if f.name == "call_base")
        assert "pkg.core.base_func" in call_base_func.calls

    def test_relative_import_with_higher_order_function(self, parser):
        """Test function from relative import passed to map/pool.imap."""
        utils_code = """
def verify_label(item):
    pass
"""
        dataset_code = """
from .utils import verify_label

class Dataset:
    def cache(self):
        with Pool() as pool:
            results = pool.imap(func=verify_label, iterable=[])
"""
        utils_funcs = parser.parse_functions(utils_code, "data/utils.py")
        dataset_funcs = parser.parse_functions(dataset_code, "data/dataset.py")
        dataset_classes = parser.parse_classes(dataset_code, "data/dataset.py")

        modules_dict = {
            "data.utils": "data.utils",
            "data.dataset": "data.dataset",
        }

        imports = parser.parse_imports(dataset_code, modules_dict, "data/dataset.py")
        all_functions = utils_funcs + dataset_funcs

        resolved = resolve_internal_calls(all_functions, dataset_classes, imports, modules_dict)

        cache_func = next(f for f in resolved if f.name == "cache")
        assert "data.utils.verify_label" in cache_func.calls


# =============================================================================
# HIGHER ORDER FUNCTION CALLS
# =============================================================================


class TestHigherOrderFunctionCalls:
    """Tests for higher-order function patterns.

    Functions passed to map(), filter(), sorted(), etc. are implicitly called
    by those functions. We should track these as call relationships.
    """

    def test_function_as_argument(self, parser):
        """Test higher_order(some_func)."""
        code = """
def transform(x):
    return x * 2

def apply_transform(func, data):
    return func(data)

def use():
    result = apply_transform(transform, 5)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use")
        assert "module.apply_transform" in use_func.calls
        # transform is passed as arg - should be tracked as implicit call
        assert "module.transform" in use_func.calls

    def test_map_with_function(self, parser):
        """Test map(func, iterable) - func is implicitly called."""
        code = """
def double(x):
    return x * 2

def process():
    items = [1, 2, 3]
    result = list(map(double, items))
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "process")
        # double is passed to map() and will be called - track as implicit call
        assert "module.double" in func.calls

    def test_filter_with_function(self, parser):
        """Test filter(func, iterable) - func is implicitly called."""
        code = """
def is_positive(x):
    return x > 0

def filter_items():
    items = [-1, 0, 1, 2]
    result = list(filter(is_positive, items))
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "filter_items")
        # is_positive is passed to filter() and will be called
        assert "module.is_positive" in func.calls

    def test_sorted_with_key_function(self, parser):
        """Test sorted(items, key=get_key) - key func is implicitly called."""
        code = """
def get_key(item):
    return item.value

def sort_items():
    items = []
    result = sorted(items, key=get_key)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "sort_items")
        # get_key is passed as key= argument and will be called
        assert "module.get_key" in func.calls

    def test_min_max_with_key_function(self, parser):
        """Test min/max with key function."""
        code = """
def get_score(item):
    return item.score

def find_extremes():
    items = []
    best = max(items, key=get_score)
    worst = min(items, key=get_score)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "find_extremes")
        assert "module.get_score" in func.calls

    def test_reduce_with_function(self, parser):
        """Test functools.reduce(func, iterable)."""
        code = """
def combine(a, b):
    return a + b

def aggregate():
    from functools import reduce
    items = [1, 2, 3, 4]
    result = reduce(combine, items)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "aggregate")
        assert "module.combine" in func.calls

    def test_any_all_with_map(self, parser):
        """Test any(map(func, items)) pattern."""
        code = """
def is_valid(x):
    return x > 0

def check_any_valid():
    items = [1, 2, 3]
    return any(map(is_valid, items))
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "check_any_valid")
        assert "module.is_valid" in func.calls

    def test_custom_higher_order_function(self, parser):
        """Test custom function that takes callable as argument."""
        code = """
def process_item(item):
    return item * 2

def apply_to_all(func, items):
    return [func(item) for item in items]

def main():
    items = [1, 2, 3]
    result = apply_to_all(process_item, items)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        main_func = next(f for f in resolved if f.name == "main")
        assert "module.apply_to_all" in main_func.calls
        # process_item is passed and will be called by apply_to_all
        assert "module.process_item" in main_func.calls

    def test_method_reference_to_higher_order(self, parser):
        """Test passing method reference to higher-order function."""
        code = """
class Processor:
    def __init__(self):
        pass

    def transform(self, x):
        return x * 2

def process_with_method():
    proc = Processor()
    items = [1, 2, 3]
    result = list(map(proc.transform, items))
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "process_with_method")
        assert "module.Processor.__init__" in func.calls
        # proc.transform passed to map() - should be tracked
        assert "module.Processor.transform" in func.calls

    def test_function_returning_function(self, parser):
        """Test factory pattern returning function."""
        code = """
def create_multiplier(factor):
    def multiply(x):
        return x * factor
    return multiply

def use_factory():
    double = create_multiplier(2)
    result = double(5)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        use_func = next(f for f in resolved if f.name == "use_factory")
        assert "module.create_multiplier" in use_func.calls
        # double(5) would need return type tracking to resolve

    def test_partial_application(self, parser):
        """Test functools.partial with function."""
        code = """
def add(a, b):
    return a + b

def use_partial():
    from functools import partial
    add_five = partial(add, 5)
    result = add_five(10)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_partial")
        # add is passed to partial - should be tracked as it will be called
        assert "module.add" in func.calls

    def test_threading_target(self, parser):
        """Test threading.Thread(target=func)."""
        code = """
def worker():
    pass

def start_thread():
    import threading
    t = threading.Thread(target=worker)
    t.start()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "start_thread")
        # worker is passed as target= and will be called
        assert "module.worker" in func.calls

    def test_multiprocessing_target(self, parser):
        """Test multiprocessing.Process(target=func)."""
        code = """
def task():
    pass

def start_process():
    import multiprocessing
    p = multiprocessing.Process(target=task)
    p.start()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "start_process")
        # task is passed as target= and will be called
        assert "module.task" in func.calls

    def test_unittest_mock_side_effect(self, parser):
        """Test mock.side_effect = func."""
        code = """
def side_effect_func(x):
    return x * 2

def setup_mock():
    mock_obj = None  # Simulating mock
    mock_obj.side_effect = side_effect_func
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, [], {}, modules_dict)
        # Assignment of function reference - harder to track

    def test_itertools_functions(self, parser):
        """Test itertools functions with key/func arguments."""
        code = """
def get_category(item):
    return item.category

def group_items():
    from itertools import groupby
    items = []
    for key, group in groupby(items, key=get_category):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "group_items")
        assert "module.get_category" in func.calls

    def test_pool_imap_with_func(self, parser):
        """Test pool.imap(func=worker) pattern."""
        code = """
def verify_image(path):
    return True

class Dataset:
    def cache_labels(self):
        with ThreadPool(8) as pool:
            results = pool.imap(
                func=verify_image,
                iterable=self.image_paths,
            )
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "cache_labels")
        # verify_image is passed as func= argument
        assert "module.verify_image" in func.calls

    def test_executor_submit_with_function(self, parser):
        """Test executor.submit(func, *args) pattern."""
        code = """
def process_chunk(data):
    return data * 2

def parallel_process():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor() as executor:
        future = executor.submit(process_chunk, [1, 2, 3])
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "parallel_process")
        # process_chunk is the first positional arg to submit
        assert "module.process_chunk" in func.calls


# =============================================================================
# WALRUS OPERATOR CALLS
# =============================================================================


class TestWalrusOperatorCalls:
    """Tests for calls with walrus operator (:=)."""

    def test_walrus_with_call(self, parser):
        """Test if (result := func())."""
        code = """
def compute():
    return 42

def use_walrus():
    if (result := compute()) > 0:
        return result
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_walrus")
        assert "module.compute" in func.calls

    def test_walrus_in_while(self, parser):
        """Test while (line := read_line())."""
        code = """
def read_line():
    return "data"

def process_lines():
    while (line := read_line()):
        pass
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "process_lines")
        assert "module.read_line" in func.calls


# =============================================================================
# CONDITIONAL EXPRESSION CALLS
# =============================================================================


class TestConditionalExpressionCalls:
    """Tests for calls in conditional (ternary) expressions."""

    def test_ternary_with_calls(self, parser):
        """Test func_a() if cond else func_b()."""
        code = """
def get_default():
    return 0

def get_value():
    return 42

def choose(condition):
    result = get_value() if condition else get_default()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "choose")
        assert "module.get_value" in func.calls
        assert "module.get_default" in func.calls

    def test_ternary_function_selection(self, parser):
        """Test (func_a if cond else func_b)(arg)."""
        code = """
def add(x):
    return x + 1

def subtract(x):
    return x - 1

def apply(condition, value):
    result = (add if condition else subtract)(value)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        next(f for f in resolved if f.name == "apply")
        # Both functions may or may not be detected as calls


# =============================================================================
# LAMBDA CALLS
# =============================================================================


class TestLambdaCalls:
    """Tests for lambda-related calls."""

    def test_lambda_immediately_invoked(self, parser):
        """Test (lambda x: x * 2)(5)."""
        code = """
def use_lambda():
    result = (lambda x: x * 2)(5)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, [], {}, modules_dict)
        # Lambda is anonymous, no call resolution expected

    def test_lambda_calling_function(self, parser):
        """Test lambda x: func(x)."""
        code = """
def transform(x):
    return x * 2

def use_lambda():
    fn = lambda x: transform(x)
    result = fn(5)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        next(f for f in resolved if f.name == "use_lambda")
        # transform is called inside lambda, may or may not be captured


# =============================================================================
# SUBSCRIPT CALLS
# =============================================================================


class TestSubscriptCalls:
    """Tests for calls involving subscript operations."""

    def test_call_on_subscript_result(self, parser):
        """Test dict[key]() or list[0]()."""
        code = """
class Handler:
    def __call__(self):
        pass

def use_handlers():
    handlers = {"a": Handler()}
    handlers["a"]()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)
        # dict[key]() call is dynamic, hard to resolve

    def test_method_on_subscript_result(self, parser):
        """Test items[0].method()."""
        code = """
class Item:
    def __init__(self):
        pass

    def process(self):
        pass

def process_first(items):
    items[0].process()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        next(f for f in resolved if f.name == "process_first")
        # May resolve via fallback if process is unique


# =============================================================================
# YIELD AND GENERATOR CALLS
# =============================================================================


class TestGeneratorCalls:
    """Tests for generator-related calls."""

    def test_yield_from_generator(self, parser):
        """Test yield from other_generator()."""
        code = """
def inner_generator():
    yield 1
    yield 2

def outer_generator():
    yield from inner_generator()
    yield 3
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "outer_generator")
        assert "module.inner_generator" in func.calls

    def test_generator_with_calls(self, parser):
        """Test generator that calls functions."""
        code = """
def process(x):
    return x * 2

def generate_processed(items):
    for item in items:
        yield process(item)
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "generate_processed")
        assert "module.process" in func.calls


# =============================================================================
# PROPERTY CALLS (NOT ACTUALLY CALLS)
# =============================================================================


class TestPropertyAccess:
    """Tests to ensure property access is NOT treated as a call."""

    def test_property_not_a_call(self, parser):
        """Test that accessing @property doesn't create a call."""
        code = """
class MyClass:
    def __init__(self):
        self._value = 0

    @property
    def value(self):
        return self._value

def use_property():
    obj = MyClass()
    x = obj.value  # This is property access, not a method call
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        next(f for f in resolved if f.name == "use_property")
        # value is a property, accessing it shouldn't be a "call"
        # (depends on how parser handles this)


# =============================================================================
# DATACLASS CALLS
# =============================================================================


class TestDataclassCalls:
    """Tests for dataclass-related patterns."""

    def test_dataclass_instantiation(self, parser):
        """Test creating a dataclass instance."""
        code = """
from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

def create_point():
    p = Point(1, 2)
    return p
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        next(f for f in resolved if f.name == "create_point")
        # Point() should resolve to Point.__init__ (auto-generated)


# =============================================================================
# DIAMOND INHERITANCE
# =============================================================================


class TestDiamondInheritance:
    """Tests for diamond inheritance MRO scenarios."""

    def test_diamond_mro(self, parser):
        """Test diamond inheritance pattern."""
        code = """
class A:
    def method(self):
        pass

class B(A):
    pass

class C(A):
    def method(self):
        pass

class D(B, C):
    def caller(self):
        self.method()  # Should resolve to C.method per MRO
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        # MRO: D -> B -> C -> A, so C.method should be found first
        assert "module.C.method" in caller.calls

    def test_super_in_diamond(self, parser):
        """Test super() in diamond inheritance."""
        code = """
class A:
    def __init__(self):
        pass

class B(A):
    def __init__(self):
        super().__init__()

class C(A):
    def __init__(self):
        super().__init__()

class D(B, C):
    def __init__(self):
        super().__init__()  # Should call B.__init__
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        d_init = next(f for f in resolved if f.name == "__init__" and f.class_name == "D")
        # MRO: D -> B -> C -> A, so super() in D calls B.__init__
        assert "module.B.__init__" in d_init.calls


# =============================================================================
# ABSTRACT METHOD CALLS
# =============================================================================


class TestAbstractMethodCalls:
    """Tests for abstract base class patterns."""

    def test_call_to_implemented_abstract(self, parser):
        """Test calling implemented abstract method."""
        code = """
from abc import ABC, abstractmethod

class Base(ABC):
    @abstractmethod
    def process(self):
        pass

    def run(self):
        self.process()

class Concrete(Base):
    def process(self):
        pass

def use():
    obj = Concrete()
    obj.run()
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        # Base.run calls self.process()
        next(f for f in resolved if f.name == "run")
        # Should resolve to Concrete.process when called on Concrete instance
        # But at definition time, it's in Base, so may resolve to Base.process


# =============================================================================
# STRING METHOD CALLS (BUILTINS)
# =============================================================================


class TestBuiltinTypeMethods:
    """Tests to ensure builtin type methods are NOT incorrectly resolved."""

    def test_string_method_not_resolved(self, parser):
        """Test that str methods don't resolve to internal classes."""
        code = """
class MyClass:
    def upper(self):  # Same name as str.upper
        pass

def use_string():
    s = "hello"
    result = s.upper()  # Should NOT resolve to MyClass.upper
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_string")
        # s.upper() is on a string, should not resolve to MyClass.upper
        assert "module.MyClass.upper" not in func.calls

    def test_list_method_not_resolved(self, parser):
        """Test that list methods don't resolve to internal classes."""
        code = """
class MyClass:
    def append(self, item):  # Same name as list.append
        pass

def use_list():
    items = []
    items.append(1)  # Should NOT resolve to MyClass.append
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "use_list")
        assert "module.MyClass.append" not in func.calls


# =============================================================================
# SPECIAL METHOD CALLS
# =============================================================================


class TestSpecialMethodCalls:
    """Tests for dunder method invocations via builtin functions."""

    def test_len_calls_dunder_len(self, parser):
        """Test that len(obj) extracts obj.__len__ call."""
        code = """
class Container:
    def __init__(self):
        pass

    def __len__(self):
        return 0

def get_length():
    c = Container()
    return len(c)  # Implicitly calls c.__len__
"""
        functions = parser.parse_functions(code, "module.py")
        get_length = next(f for f in functions if f.name == "get_length")

        # len(c) should extract c.__len__
        assert "c.__len__" in get_length.calls

        # Now test resolution
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}
        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        get_length_resolved = next(f for f in resolved if f.name == "get_length")
        assert "module.Container.__len__" in get_length_resolved.calls

    def test_str_calls_dunder_str(self, parser):
        """Test that str(obj) extracts obj.__str__ call."""
        code = """
class MyClass:
    def __str__(self):
        return "MyClass"

def to_string():
    obj = MyClass()
    return str(obj)
"""
        functions = parser.parse_functions(code, "module.py")
        to_string = next(f for f in functions if f.name == "to_string")

        assert "obj.__str__" in to_string.calls

    def test_iter_calls_dunder_iter(self, parser):
        """Test that iter(obj) extracts obj.__iter__ call."""
        code = """
class Iterable:
    def __iter__(self):
        return iter([])

def get_iterator():
    obj = Iterable()
    return iter(obj)
"""
        functions = parser.parse_functions(code, "module.py")
        get_iterator = next(f for f in functions if f.name == "get_iterator")

        assert "obj.__iter__" in get_iterator.calls

    def test_next_calls_dunder_next(self, parser):
        """Test that next(obj) extracts obj.__next__ call."""
        code = """
class Iterator:
    def __next__(self):
        raise StopIteration

def get_next():
    it = Iterator()
    return next(it)
"""
        functions = parser.parse_functions(code, "module.py")
        get_next = next(f for f in functions if f.name == "get_next")

        assert "it.__next__" in get_next.calls

    def test_bool_calls_dunder_bool(self, parser):
        """Test that bool(obj) extracts obj.__bool__ call."""
        code = """
class Truthy:
    def __bool__(self):
        return True

def check_bool():
    obj = Truthy()
    return bool(obj)
"""
        functions = parser.parse_functions(code, "module.py")
        check_bool = next(f for f in functions if f.name == "check_bool")

        assert "obj.__bool__" in check_bool.calls

    def test_hash_calls_dunder_hash(self, parser):
        """Test that hash(obj) extracts obj.__hash__ call."""
        code = """
class Hashable:
    def __hash__(self):
        return 42

def get_hash():
    obj = Hashable()
    return hash(obj)
"""
        functions = parser.parse_functions(code, "module.py")
        get_hash = next(f for f in functions if f.name == "get_hash")

        assert "obj.__hash__" in get_hash.calls

    def test_repr_calls_dunder_repr(self, parser):
        """Test that repr(obj) extracts obj.__repr__ call."""
        code = """
class MyClass:
    def __repr__(self):
        return "MyClass()"

def get_repr():
    obj = MyClass()
    return repr(obj)
"""
        functions = parser.parse_functions(code, "module.py")
        get_repr = next(f for f in functions if f.name == "get_repr")

        assert "obj.__repr__" in get_repr.calls

    def test_len_with_self_attribute(self, parser):
        """Test that len(self.items) extracts self.items.__len__ call."""
        code = """
class Container:
    def count(self):
        return len(self.items)
"""
        functions = parser.parse_functions(code, "module.py")
        count = next(f for f in functions if f.name == "count")

        assert "self.items.__len__" in count.calls

    def test_builtin_dunder_resolution(self, parser):
        """Test full resolution of builtin dunder calls."""
        code = """
class MyList:
    def __len__(self):
        return 0

    def __iter__(self):
        return iter([])

    def __repr__(self):
        return "MyList()"

def process():
    lst = MyList()
    size = len(lst)
    items = iter(lst)
    text = repr(lst)
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)
        process_func = next(f for f in resolved if f.name == "process")

        # All dunder methods should be resolved
        assert "module.MyList.__len__" in process_func.calls
        assert "module.MyList.__iter__" in process_func.calls
        assert "module.MyList.__repr__" in process_func.calls

    def test_getitem_calls_dunder_getitem(self, parser):
        """Test that obj[key] calls __getitem__."""
        # Note: Subscript syntax is not currently tracked as it's not a call node
        code = """
class Mapping:
    def __init__(self):
        pass

    def __getitem__(self, key):
        return None

def access():
    m = Mapping()
    value = m["key"]  # Implicitly calls __getitem__
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)
        # Subscript syntax (m["key"]) is not a call node, so not tracked


# =============================================================================
# OPERATOR OVERLOAD CALLS
# =============================================================================


class TestOperatorOverloadCalls:
    """Tests for operator overloading method calls."""

    def test_add_calls_dunder_add(self, parser):
        """Test that a + b calls __add__."""
        code = """
class Vector:
    def __init__(self, x):
        self.x = x

    def __add__(self, other):
        return Vector(self.x + other.x)

def add_vectors():
    v1 = Vector(1)
    v2 = Vector(2)
    v3 = v1 + v2  # Implicitly calls __add__
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)
        # + operator calls __add__, may not be tracked


# =============================================================================
# ATTRIBUTE ASSIGNMENT CALLS
# =============================================================================


class TestAttributeAssignmentCalls:
    """Tests for calls during attribute assignment."""

    def test_setattr_method_call(self, parser):
        """Test that setting attr calls __setattr__ if defined."""
        code = """
class Tracked:
    def __init__(self):
        pass

    def __setattr__(self, name, value):
        pass

def set_attribute():
    obj = Tracked()
    obj.value = 42  # Calls __setattr__
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)
        # Assignment may call __setattr__, not typically tracked


# =============================================================================
# TYPE HINT CALLS (RARE BUT POSSIBLE)
# =============================================================================


class TestTypeHintCalls:
    """Tests for calls in type annotations (rare cases)."""

    def test_generic_type_hint(self, parser):
        """Test Generic[T] in type hints."""
        code = """
from typing import List

class Item:
    pass

def process(items: List[Item]) -> Item:
    return items[0]
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, classes, {}, modules_dict)
        # Type hints are not calls


# =============================================================================
# F-STRING CALLS
# =============================================================================


class TestFStringCalls:
    """Tests for calls inside f-strings."""

    def test_function_call_in_fstring(self, parser):
        """Test f"{func()}" pattern."""
        code = """
def get_name():
    return "World"

def greet():
    message = f"Hello, {get_name()}!"
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "greet")
        assert "module.get_name" in func.calls

    def test_method_call_in_fstring(self, parser):
        """Test f"{obj.method()}" pattern."""
        code = """
class Formatter:
    def __init__(self):
        pass

    def format_value(self):
        return "formatted"

def display():
    fmt = Formatter()
    message = f"Result: {fmt.format_value()}"
"""
        functions = parser.parse_functions(code, "module.py")
        classes = parser.parse_classes(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, classes, {}, modules_dict)

        func = next(f for f in resolved if f.name == "display")
        assert "module.Formatter.__init__" in func.calls
        assert "module.Formatter.format_value" in func.calls


# =============================================================================
# ASSERT CALLS
# =============================================================================


class TestAssertCalls:
    """Tests for calls in assert statements."""

    def test_function_call_in_assert(self, parser):
        """Test assert func()."""
        code = """
def is_valid(x):
    return x > 0

def validate(value):
    assert is_valid(value), "Value must be positive"
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        func = next(f for f in resolved if f.name == "validate")
        assert "module.is_valid" in func.calls


# =============================================================================
# GLOBAL AND NONLOCAL CALLS
# =============================================================================


class TestGlobalNonlocalCalls:
    """Tests for calls involving global/nonlocal variables."""

    def test_call_global_function(self, parser):
        """Test calling a function assigned to global variable."""
        code = """
def helper():
    pass

_global_func = helper

def use_global():
    global _global_func
    _global_func()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolve_internal_calls(functions, [], {}, modules_dict)
        # _global_func() is dynamic, hard to resolve

    def test_nonlocal_function_call(self, parser):
        """Test calling function from enclosing scope."""
        code = """
def outer():
    def inner_helper():
        pass

    def inner():
        inner_helper()

    inner()
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        outer_func = next(f for f in resolved if f.name == "outer")
        # inner() call from outer should resolve to the nested function
        assert "module.outer.inner" in outer_func.calls

    def test_nested_function_call_from_outer(self, parser):
        """Test that outer function calling nested function resolves correctly."""
        code = """
def guess_model_task(model):
    def cfg2task(cfg):
        return cfg.get("task")

    return cfg2task(model.cfg)
"""
        functions = parser.parse_functions(code, "tasks.py")
        modules_dict = {"tasks": "tasks"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        outer_func = next(f for f in resolved if f.name == "guess_model_task")
        # Should resolve to the nested function, not module-level
        assert "tasks.guess_model_task.cfg2task" in outer_func.calls

    def test_sibling_nested_function_call(self, parser):
        """Test calling sibling nested function."""
        code = """
def outer():
    def helper():
        pass

    def worker():
        helper()  # Call sibling nested function
"""
        functions = parser.parse_functions(code, "module.py")
        modules_dict = {"module": "module"}

        resolved = resolve_internal_calls(functions, [], {}, modules_dict)

        worker_func = next(f for f in resolved if f.name == "worker")
        # helper() from worker should resolve to sibling nested function
        assert "module.outer.helper" in worker_func.calls


# =============================================================================
# PYTORCH MODULE FORWARD CALLS
# =============================================================================


# =============================================================================
# SELF METHOD REFERENCES (NOT DIRECT CALLS)
# =============================================================================


class TestSelfMethodReferences:
    """Tests for detecting self.method references that are not direct calls.

    When self.method is used without () — e.g., stored in a variable, placed
    in a tuple, or passed as a callback — it should be detected as an implicit call.
    """

    def test_self_method_in_conditional_tuple(self, parser):
        """Test the cache_images pattern: self.method refs in conditional tuples."""
        code = """
class Dataset:
    def cache_images(self):
        fcn, storage = (self.cache_images_to_disk, "Disk") if self.cache == "disk" else (self.load_image, "RAM")
        pool.imap(fcn, range(self.ni))

    def cache_images_to_disk(self):
        pass

    def load_image(self):
        pass
"""
        functions = parser.parse_functions(code, "data/dataset.py")
        cache_func = next(f for f in functions if f.name == "cache_images")
        assert "self.cache_images_to_disk" in cache_func.calls
        assert "self.load_image" in cache_func.calls
        # self.cache is in a comparison, should NOT be detected
        assert "self.cache" not in cache_func.calls

    def test_self_method_not_duplicated_for_direct_calls(self, parser):
        """Test that self.method() is not duplicated as a reference."""
        code = """
class MyClass:
    def process(self):
        self.helper()
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert func.calls.count("self.helper") == 1

    def test_self_attr_assignment_lhs_not_detected(self, parser):
        """Test that self.x = ... does not add self.x as a reference."""
        code = """
class MyClass:
    def __init__(self):
        self.value = 42
        self.name = "test"
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.value" not in func.calls
        assert "self.name" not in func.calls

    def test_self_attr_subscript_not_detected(self, parser):
        """Test that self.data[i] does not add self.data as a reference."""
        code = """
class MyClass:
    def get(self, i):
        return self.data[i]
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.data" not in func.calls

    def test_self_attr_comparison_not_detected(self, parser):
        """Test that self.x == value does not add self.x as a reference."""
        code = """
class MyClass:
    def check(self):
        if self.mode == "fast":
            pass
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.mode" not in func.calls

    def test_self_attr_augmented_assignment_not_detected(self, parser):
        """Test that self.x += 1 does not add self.x as a reference."""
        code = """
class MyClass:
    def increment(self):
        self.count += 1
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.count" not in func.calls

    def test_self_method_in_list(self, parser):
        """Test self.method references stored in a list."""
        code = """
class Pipeline:
    def get_steps(self):
        return [self.preprocess, self.transform, self.postprocess]
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.preprocess" in func.calls
        assert "self.transform" in func.calls
        assert "self.postprocess" in func.calls

    def test_self_method_in_dict_value(self, parser):
        """Test self.method references as dict values."""
        code = """
class Handler:
    def get_handlers(self):
        return {"start": self.on_start, "end": self.on_end}
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.on_start" in func.calls
        assert "self.on_end" in func.calls

    def test_self_method_chain_not_detected(self, parser):
        """Test that self.items.append() does not add self.items as a reference."""
        code = """
class MyClass:
    def add(self, item):
        self.items.append(item)
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.items" not in func.calls

    def test_self_method_as_assignment_rhs(self, parser):
        """Test self.method on RHS of variable assignment."""
        code = """
class MyClass:
    def setup(self):
        callback = self.handle_event
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.handle_event" in func.calls

    def test_self_method_in_return(self, parser):
        """Test self.method in a return statement."""
        code = """
class MyClass:
    def get_callback(self):
        return self.process
"""
        functions = parser.parse_functions(code, "test.py")
        func = functions[0]
        assert "self.process" in func.calls
