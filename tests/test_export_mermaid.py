"""Tests for the Mermaid diagram exporter.

All tests use synthetic AnalysisResult fixtures — no DB, no real repo, no git.
render_mermaid() is a pure function (AnalysisResult → str), so assertions are
simply substring checks on the returned Mermaid source.
"""

from codiff.export.mermaid import render_mermaid
from codiff.schema.diff import (
    AddedFunctionInfo,
    AnalysisResult,
    ModifiedFunctionInfo,
    RemovedFunctionInfo,
    SummaryStats,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _summary(**kwargs) -> SummaryStats:
    return SummaryStats(
        added_functions=kwargs.get("added", 0),
        removed_functions=kwargs.get("removed", 0),
        modified_functions=kwargs.get("modified", 0),
        modules_touched=kwargs.get("modules", []),
    )


def _added(
    function_id: str,
    *,
    file_path: str = "src/mod.py",
    class_name: str | None = None,
    new_calls: list[str] | None = None,
    new_callers: list[str] | None = None,
    existing_callers: list[str] | None = None,
    existing_calls: list[str] | None = None,
    is_entry_point: bool = True,
) -> AddedFunctionInfo:
    return AddedFunctionInfo(
        function_id=function_id,
        file_path=file_path,
        class_name=class_name,
        new_calls=new_calls or [],
        new_callers=new_callers or [],
        existing_callers=existing_callers or [],
        existing_calls=existing_calls or [],
        is_entry_point=is_entry_point,
    )


def _modified(
    function_id: str,
    *,
    file_path: str = "src/mod.py",
    class_name: str | None = None,
    signature_changed: bool = False,
    calls_added_new: list[str] | None = None,
    calls_added_existing: list[str] | None = None,
    calls_removed: list[str] | None = None,
    callers: list[str] | None = None,
) -> ModifiedFunctionInfo:
    return ModifiedFunctionInfo(
        function_id=function_id,
        file_path=file_path,
        class_name=class_name,
        signature_changed=signature_changed,
        old_params=[],
        new_params=[],
        old_return_type=None,
        new_return_type=None,
        calls_added_new=calls_added_new or [],
        calls_added_existing=calls_added_existing or [],
        calls_removed=calls_removed or [],
        callers=callers or [],
    )


def _removed(
    function_id: str,
    *,
    file_path: str = "src/mod.py",
    class_name: str | None = None,
    was_called_by: list[str] | None = None,
) -> RemovedFunctionInfo:
    return RemovedFunctionInfo(
        function_id=function_id,
        file_path=file_path,
        class_name=class_name,
        was_called_by=was_called_by or [],
    )


def _result(
    added=None,
    modified=None,
    removed=None,
    class_parents=None,
) -> AnalysisResult:
    return AnalysisResult(
        summary=_summary(),
        added=added or [],
        modified=modified or [],
        removed=removed or [],
        class_parents=class_parents or {},
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


class TestBasicStructure:
    def test_empty_result_returns_empty_string(self):
        out = render_mermaid(_result())
        assert out == ""

    def test_added_functions_produce_diagram(self):
        out = render_mermaid(_result(added=[_added("src.mod.foo")]))
        assert "```mermaid" in out
        assert "classDiagram" in out
        assert "```" in out

    def test_two_diagrams_when_both_connected_and_isolated(self):
        # connected: a calls b across files; isolated: c in separate file with no edges
        a = _added("src.a.fa", file_path="src/a.py", new_calls=["src.b.fb"], is_entry_point=False)
        b = _added("src.b.fb", file_path="src/b.py", new_callers=["src.a.fa"], is_entry_point=False)
        c = _added("src.c.fc", file_path="src/c.py")
        out = render_mermaid(_result(added=[a, b, c]))
        assert out.count("```mermaid") == 2

    def test_only_isolated_modules_single_diagram(self):
        out = render_mermaid(_result(added=[_added("src.mod.foo")]))
        assert out.count("```mermaid") == 1


# ---------------------------------------------------------------------------
# Method line formatting
# ---------------------------------------------------------------------------


class TestMethodLines:
    def test_added_method_uses_plus(self):
        out = render_mermaid(_result(added=[_added("src.mod.my_func")]))
        assert "+ my_func()" in out

    def test_modified_body_changed(self):
        out = render_mermaid(_result(modified=[_modified("src.mod.my_func")]))
        assert "~ my_func()" in out

    def test_modified_signature_changed(self):
        out = render_mermaid(_result(modified=[_modified("src.mod.f", signature_changed=True)]))
        assert "~ f()  sig" in out

    def test_modified_calls_added(self):
        out = render_mermaid(
            _result(modified=[_modified("src.mod.f", calls_added_new=["src.mod.g", "src.mod.h"])])
        )
        assert "calls +2" in out

    def test_modified_calls_removed(self):
        out = render_mermaid(
            _result(modified=[_modified("src.mod.f", calls_removed=["src.mod.g"])])
        )
        assert "calls −1" in out

    def test_removed_method_uses_minus(self):
        out = render_mermaid(_result(removed=[_removed("src.mod.gone")]))
        assert "- gone()" in out

    def test_method_truncation(self):
        # More than 12 methods → "... N more"
        fns = [_added(f"src.mod.f{i}") for i in range(15)]
        out = render_mermaid(_result(added=fns))
        assert "... 3 more" in out

    def test_no_truncation_at_exactly_12(self):
        fns = [_added(f"src.mod.f{i}") for i in range(12)]
        out = render_mermaid(_result(added=fns))
        assert "more" not in out


# ---------------------------------------------------------------------------
# Class box styling
# ---------------------------------------------------------------------------


class TestStyling:
    def test_added_only_gets_green_style(self):
        out = render_mermaid(_result(added=[_added("src.mod.foo")]))
        assert "fill:#f0fdf4" in out

    def test_modified_only_gets_yellow_style(self):
        out = render_mermaid(_result(modified=[_modified("src.mod.foo")]))
        assert "fill:#fefce8" in out

    def test_removed_only_gets_red_style(self):
        out = render_mermaid(_result(removed=[_removed("src.mod.foo")]))
        assert "fill:#fff1f2" in out

    def test_mixed_added_and_modified_gets_yellow(self):
        out = render_mermaid(
            _result(
                added=[_added("src.mod.new")],
                modified=[_modified("src.mod.old")],
            )
        )
        assert "fill:#fefce8" in out

    def test_pure_added_gets_green_not_yellow(self):
        out = render_mermaid(_result(added=[_added("src.mod.foo")]))
        assert "fill:#f0fdf4" in out
        assert "fill:#fefce8" not in out


# ---------------------------------------------------------------------------
# Named classes and stereotype subtitle
# ---------------------------------------------------------------------------


class TestNamedClasses:
    def test_class_name_as_box_title(self):
        fn = _added("src.mod.MyClass.method", class_name="MyClass")
        out = render_mermaid(_result(added=[fn]))
        assert '"MyClass"' in out

    def test_file_path_as_stereotype(self):
        fn = _added("src.mod.MyClass.method", file_path="src/mod.py", class_name="MyClass")
        out = render_mermaid(_result(added=[fn]))
        assert "<<src/mod.py>>" in out

    def test_standalone_uses_file_path_as_title(self):
        fn = _added("src.mod.standalone_func", file_path="src/mod.py", class_name=None)
        out = render_mermaid(_result(added=[fn]))
        assert '"src/mod.py"' in out

    def test_method_name_only_inside_class_box(self):
        fn = _added("src.mod.MyClass.do_thing", class_name="MyClass")
        out = render_mermaid(_result(added=[fn]))
        assert "+ do_thing()" in out
        assert "MyClass.do_thing" not in out


# ---------------------------------------------------------------------------
# Relationships — call edges
# ---------------------------------------------------------------------------


class TestCallEdges:
    def test_cross_file_call_produces_edge(self):
        a = _added("src.a.fa", file_path="src/a.py", new_calls=["src.b.fb"], is_entry_point=False)
        b = _added("src.b.fb", file_path="src/b.py", new_callers=["src.a.fa"], is_entry_point=False)
        out = render_mermaid(_result(added=[a, b]))
        assert ": calls" in out

    def test_same_file_call_no_cross_edge(self):
        a = _added(
            "src.mod.fa", file_path="src/mod.py", new_calls=["src.mod.fb"], is_entry_point=False
        )
        b = _added(
            "src.mod.fb", file_path="src/mod.py", new_callers=["src.mod.fa"], is_entry_point=False
        )
        out = render_mermaid(_result(added=[a, b]))
        # No cross-file edge — both in same file
        assert ": calls" not in out

    def test_removed_call_produces_dashed_edge(self):
        # Caller (ClassA) has an active new call to ClassB AND a removed call to ClassC.
        # Active call makes the files connected → diagram 1 gets the edge section.
        # ClassA, ClassB, ClassC are distinct boxes so their IDs don't collide.
        caller = _added(
            "src.a.ClassA.run",
            file_path="src/a.py",
            class_name="ClassA",
            new_calls=["src.b.ClassB.do"],
            is_entry_point=False,
        )
        callee = _added(
            "src.b.ClassB.do",
            file_path="src/b.py",
            class_name="ClassB",
            new_callers=["src.a.ClassA.run"],
            is_entry_point=False,
        )
        old_dep = _modified(
            "src.a.ClassA.old",
            file_path="src/a.py",
            class_name="ClassA",
            calls_removed=["src.c.ClassC.gone"],
        )
        removed_callee = _modified("src.c.ClassC.gone", file_path="src/c.py", class_name="ClassC")
        out = render_mermaid(_result(added=[caller, callee], modified=[old_dep, removed_callee]))
        assert "..>" in out and ": removed" in out


# ---------------------------------------------------------------------------
# Relationships — inheritance
# ---------------------------------------------------------------------------


class TestInheritanceEdges:
    def test_inheritance_uses_uml_arrow(self):
        # child.method calls parent.method → cross-file call makes both connected
        child = _added(
            "src.mod.Child.method",
            file_path="src/mod.py",
            class_name="Child",
            new_calls=["src.other.Parent.method"],
            is_entry_point=False,
        )
        parent = _added(
            "src.other.Parent.method",
            file_path="src/other.py",
            class_name="Parent",
            new_callers=["src.mod.Child.method"],
            is_entry_point=False,
        )
        class_parents = {"src.mod.Child": ["Parent"]}
        out = render_mermaid(_result(added=[child, parent], class_parents=class_parents))
        assert "--|>" in out

    def test_inheritance_not_also_shown_as_call(self):
        # Same setup: cross-file call puts both in connected diagram
        child = _added(
            "src.mod.Child.method",
            file_path="src/mod.py",
            class_name="Child",
            new_calls=["src.other.Parent.method"],
            is_entry_point=False,
        )
        parent = _added(
            "src.other.Parent.method",
            file_path="src/other.py",
            class_name="Parent",
            new_callers=["src.mod.Child.method"],
            is_entry_point=False,
        )
        class_parents = {"src.mod.Child": ["Parent"]}
        out = render_mermaid(_result(added=[child, parent], class_parents=class_parents))
        # Inheritance arrow present, call arrow suppressed for the same pair
        assert "--|>" in out
        assert ": calls" not in out

    def test_no_inheritance_arrow_for_unknown_parent(self):
        child = _added("src.mod.Child.method", file_path="src/mod.py", class_name="Child")
        class_parents = {"src.mod.Child": ["UnknownParent"]}
        out = render_mermaid(_result(added=[child], class_parents=class_parents))
        assert "--|>" not in out


# ---------------------------------------------------------------------------
# Connected vs isolated split
# ---------------------------------------------------------------------------


class TestConnectedIsolatedSplit:
    def test_isolated_module_in_second_diagram(self):
        a = _added("src.a.fa", file_path="src/a.py", new_calls=["src.b.fb"], is_entry_point=False)
        b = _added("src.b.fb", file_path="src/b.py", new_callers=["src.a.fa"], is_entry_point=False)
        iso = _added("src.iso.fi", file_path="src/iso.py")
        out = render_mermaid(_result(added=[a, b, iso]))
        diagrams = out.split("```mermaid")
        assert len(diagrams) == 3  # ["", d1, d2_with_closing]
        assert "src/iso.py" in diagrams[2]
        assert "src/iso.py" not in diagrams[1]

    def test_isolated_modules_grouped_by_folder(self):
        fa = _added("pkg.sub.fa", file_path="pkg/sub/a.py")
        fb = _added("pkg.sub.fb", file_path="pkg/sub/b.py")
        out = render_mermaid(_result(added=[fa, fb]))
        # Both isolated and same top-2 folders → same namespace
        assert out.count("namespace") == 1

    def test_isolated_modules_different_folders_different_namespaces(self):
        fa = _added("pkg.a.fa", file_path="pkg/a/mod.py")
        fb = _added("other.b.fb", file_path="other/b/mod.py")
        out = render_mermaid(_result(added=[fa, fb]))
        assert out.count("namespace") == 2

    def test_connected_modules_in_first_diagram_have_elk(self):
        a = _added("src.a.fa", file_path="src/a.py", new_calls=["src.b.fb"], is_entry_point=False)
        b = _added("src.b.fb", file_path="src/b.py", new_callers=["src.a.fa"], is_entry_point=False)
        out = render_mermaid(_result(added=[a, b]))
        first = out.split("```mermaid")[1]
        assert "elk" in first

    def test_isolated_diagram_uses_dagre(self):
        out = render_mermaid(_result(added=[_added("src.mod.foo")]))
        # Only one diagram (isolated) — should not have elk layout
        assert "'layout': 'elk'" not in out


# ---------------------------------------------------------------------------
# Deleted boxes (only shown when result.removed is non-empty)
# ---------------------------------------------------------------------------


class TestDeletedBoxes:
    def test_removed_functions_produce_deleted_box(self):
        out = render_mermaid(_result(removed=[_removed("src.mod.old_func")]))
        assert "(deleted)" in out

    def test_deleted_box_has_red_style(self):
        out = render_mermaid(_result(removed=[_removed("src.mod.old_func")]))
        assert "fill:#fff1f2" in out

    def test_no_deleted_box_when_removed_empty(self):
        out = render_mermaid(_result(added=[_added("src.mod.new_func")]))
        assert "(deleted)" not in out

    def test_mixed_class_has_main_and_deleted_box(self):
        fn_added = _added("src.mod.MyClass.new_m", class_name="MyClass")
        fn_removed = _removed("src.mod.MyClass.old_m", class_name="MyClass")
        out = render_mermaid(_result(added=[fn_added], removed=[fn_removed]))
        assert '"MyClass"' in out
        assert '"MyClass (deleted)"' in out


# ---------------------------------------------------------------------------
# Short IDs and diagram size
# ---------------------------------------------------------------------------


class TestShortIds:
    def test_node_ids_are_short(self):
        out = render_mermaid(_result(added=[_added("src.mod.foo")]))
        # Short IDs like n0, n1 — not long sanitised paths
        assert "cls__" not in out
        assert "mod__" not in out

    def test_no_ghost_boxes_from_undefined_ids(self):
        # A removed function's ID should not appear in edges if its box wasn't emitted
        a = _modified("src.a.fa", file_path="src/a.py", calls_removed=["src.b.gone"])
        out = render_mermaid(_result(modified=[a]))
        # "gone" not in result.added/modified so no box defined for it;
        # edge should be filtered out entirely
        lines = [ln for ln in out.splitlines() if "gone" in ln]
        assert lines == []
