"""End-to-end integration tests: TypeScript parser → resolver → call edges.

Each test parses real TypeScript source, runs the resolver, and asserts that
specific edges appear in the resolved calls.  If any edge is missing, the test
name tells you exactly which connection was lost.
"""

from codiff.parsers.typescript_parser import TypeScriptParser, TypeScriptXParser
from codiff.resolvers.typescript_resolver import TypeScriptCallResolver


def _resolve(funcs, classes, imports, modules_dict):
    resolver = TypeScriptCallResolver(funcs, classes, imports, modules_dict)
    return resolver.resolve_all_calls()


# ---------------------------------------------------------------------------
# Within-file edges
# ---------------------------------------------------------------------------


class TestWithinFileEdges:
    def test_this_method_call_creates_edge(self):
        """this.validate() inside a method → edge to Service.validate."""
        parser = TypeScriptParser()
        code = """
class Service {
    process(): void { this.validate(); }
    validate(): void {}
}
"""
        modules_dict = {"src.service": "src.service"}
        funcs, classes, imports, _ = parser.parse_code(code, "src/service.ts", modules_dict)
        resolved = _resolve(funcs, classes, imports, modules_dict)

        process = next(f for f in resolved if f.name == "process")
        assert "src.service.Service.validate" in process.calls

    def test_this_call_chain_within_class(self):
        """createUser calls both this.validate and this.save."""
        parser = TypeScriptParser()
        code = """
class UserService {
    createUser(data: any): void {
        this.validate(data);
        this.save(data);
    }
    validate(data: any): void {}
    save(data: any): void {}
}
"""
        modules_dict = {"src.user_service": "src.user_service"}
        funcs, classes, imports, _ = parser.parse_code(code, "src/user_service.ts", modules_dict)
        resolved = _resolve(funcs, classes, imports, modules_dict)

        create = next(f for f in resolved if f.name == "createUser")
        assert "src.user_service.UserService.validate" in create.calls
        assert "src.user_service.UserService.save" in create.calls

    def test_module_level_function_call(self):
        """Top-level caller() → helper() in the same file."""
        parser = TypeScriptParser()
        code = """
function caller(): void { helper(); }
function helper(): void {}
"""
        modules_dict = {"src.utils": "src.utils"}
        funcs, classes, imports, _ = parser.parse_code(code, "src/utils.ts", modules_dict)
        resolved = _resolve(funcs, classes, imports, modules_dict)

        caller = next(f for f in resolved if f.name == "caller")
        assert "src.utils.helper" in caller.calls

    def test_new_expression_resolves_to_constructor(self):
        """new Repo() in a method → Repo.constructor edge."""
        parser = TypeScriptParser()
        code = """
class Factory {
    build(): void { const r = new Repo(); }
}
class Repo {
    constructor() {}
}
"""
        modules_dict = {"src.factory": "src.factory"}
        funcs, classes, imports, _ = parser.parse_code(code, "src/factory.ts", modules_dict)
        resolved = _resolve(funcs, classes, imports, modules_dict)

        build = next(f for f in resolved if f.name == "build")
        assert "src.factory.Repo.constructor" in build.calls

    def test_super_call_resolves_to_parent_constructor(self):
        """super() in child constructor → parent constructor edge."""
        parser = TypeScriptParser()
        code = """
class Animal {
    constructor(name: string) {}
}
class Dog extends Animal {
    constructor(name: string) { super(name); }
}
"""
        modules_dict = {"src.animals": "src.animals"}
        funcs, classes, imports, _ = parser.parse_code(code, "src/animals.ts", modules_dict)
        resolved = _resolve(funcs, classes, imports, modules_dict)

        dog_ctor = next(f for f in resolved if f.name == "constructor" and f.class_name == "Dog")
        assert "src.animals.Animal.constructor" in dog_ctor.calls

    def test_var_types_method_call(self):
        """const obj = new MyClass(); obj.doWork() → MyClass.doWork edge."""
        parser = TypeScriptParser()
        code = """
function run(): void {
    const worker = new Worker();
    worker.doWork();
}
class Worker {
    doWork(): void {}
}
"""
        modules_dict = {"src.run": "src.run"}
        funcs, classes, imports, _ = parser.parse_code(code, "src/run.ts", modules_dict)
        resolved = _resolve(funcs, classes, imports, modules_dict)

        run = next(f for f in resolved if f.name == "run")
        assert "src.run.Worker.doWork" in run.calls


# ---------------------------------------------------------------------------
# Cross-file edges (same extension)
# ---------------------------------------------------------------------------


class TestCrossFileEdges:
    def _build_modules_dict(self, *rel_paths):
        """Build a minimal modules_dict for the given relative paths."""
        md = {}
        for rel in rel_paths:
            import os

            base = os.path.splitext(rel)[0].replace("/", ".")
            # Register full key and stem (mirrors build_modules_dict logic)
            md[base] = base
            md[base.split(".")[-1]] = base
        return md

    def test_imported_named_class_instantiation(self):
        """import { UserService } from './service'; new UserService() → constructor."""
        parser = TypeScriptParser()

        service_code = """
export class UserService {
    constructor() {}
    getUser(): void {}
}
"""
        controller_code = """
import { UserService } from "./service";
class Controller {
    run(): void {
        const svc = new UserService();
        svc.getUser();
    }
}
"""
        modules_dict = self._build_modules_dict("src/service.ts", "src/controller.ts")
        sf, sc, si, _ = parser.parse_code(service_code, "src/service.ts", modules_dict)
        cf, cc, ci, _ = parser.parse_code(controller_code, "src/controller.ts", modules_dict)

        resolved = _resolve(sf + cf, sc + cc, {**si, **ci}, modules_dict)

        run = next(f for f in resolved if f.name == "run")
        assert "src.service.UserService.constructor" in run.calls
        assert "src.service.UserService.getUser" in run.calls

    def test_imported_function_direct_call(self):
        """import { helper } from './utils'; helper() → utils.helper edge."""
        parser = TypeScriptParser()

        utils_code = "export function helper(): void {}"
        app_code = """
import { helper } from "./utils";
function main(): void { helper(); }
"""
        modules_dict = self._build_modules_dict("src/utils.ts", "src/app.ts")
        uf, uc, ui, _ = parser.parse_code(utils_code, "src/utils.ts", modules_dict)
        af, ac, ai, _ = parser.parse_code(app_code, "src/app.ts", modules_dict)

        resolved = _resolve(uf + af, uc + ac, {**ui, **ai}, modules_dict)

        main = next(f for f in resolved if f.name == "main")
        assert "src.utils.helper" in main.calls

    def test_at_alias_import(self):
        """import { X } from '@/stores/documentStore' resolves via path alias."""
        parser = TypeScriptParser()

        store_code = "export function useDocumentStore(): void {}"
        view_code = """
import { useDocumentStore } from "@/stores/documentStore";
function setup(): void { useDocumentStore(); }
"""
        # build_modules_dict registers sub-paths, so "stores.documentStore" is present
        modules_dict = {
            "stores.documentStore": "src.stores.documentStore",
            "src.stores.documentStore": "src.stores.documentStore",
            "src.views.Home": "src.views.Home",
        }
        sf, sc, si, _ = parser.parse_code(store_code, "src/stores/documentStore.ts", modules_dict)
        vf, vc, vi, _ = parser.parse_code(store_code, "src/stores/documentStore.ts", modules_dict)
        # Parse the view with alias import
        vf2, vc2, vi2, _ = parser.parse_code(view_code, "src/views/Home.ts", modules_dict)

        resolved = _resolve(sf + vf2, sc + vc2, {**si, **vi2}, modules_dict)

        setup = next(f for f in resolved if f.name == "setup")
        assert "src.stores.documentStore.useDocumentStore" in setup.calls

    def test_parent_dir_import(self):
        """import { X } from '../service' (../ traversal) is resolved."""
        parser = TypeScriptParser()

        service_code = "export function compute(): void {}"
        component_code = """
import { compute } from "../service";
function render(): void { compute(); }
"""
        modules_dict = self._build_modules_dict("src/service.ts", "src/components/button.ts")
        sf, sc, si, _ = parser.parse_code(service_code, "src/service.ts", modules_dict)
        cf, cc, ci, _ = parser.parse_code(component_code, "src/components/button.ts", modules_dict)

        resolved = _resolve(sf + cf, sc + cc, {**si, **ci}, modules_dict)

        render = next(f for f in resolved if f.name == "render")
        assert "src.service.compute" in render.calls


# ---------------------------------------------------------------------------
# Cross-extension edges (.tsx component calling a .ts service)
# ---------------------------------------------------------------------------


class TestCrossExtensionEdges:
    def test_tsx_component_calls_ts_service_method(self):
        """A .tsx component that instantiates a .ts class sees the class's methods."""
        ts_parser = TypeScriptParser()
        tsx_parser = TypeScriptXParser()

        service_code = """
export class ApiService {
    fetchData(): void {}
}
"""
        component_code = """
import { ApiService } from "./api_service";
class Dashboard {
    load(): void {
        const api = new ApiService();
        api.fetchData();
    }
}
"""
        import os

        def mod_key(rel):
            return os.path.splitext(rel)[0].replace("/", ".")

        modules_dict = {
            mod_key("src/api_service.ts"): mod_key("src/api_service.ts"),
            "api_service": mod_key("src/api_service.ts"),
            mod_key("src/Dashboard.tsx"): mod_key("src/Dashboard.tsx"),
        }

        sf, sc, si, _ = ts_parser.parse_code(service_code, "src/api_service.ts", modules_dict)
        cf, cc, ci, _ = tsx_parser.parse_code(component_code, "src/Dashboard.tsx", modules_dict)

        # Combine — mirrors what code_parser.py does when grouping by resolver class
        resolved = _resolve(sf + cf, sc + cc, {**si, **ci}, modules_dict)

        load = next(f for f in resolved if f.name == "load")
        assert "src.api_service.ApiService.fetchData" in load.calls
