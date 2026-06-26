"""Tests for the terminal (Rich) exporter.

Strategy: replace the module-level console with Console(record=True) so
render() output can be captured as plain text and asserted on.

Pure helper functions (_name, _display_name, _build_color_map, etc.) are
tested directly without any console involvement.
"""

import pytest
from rich.console import Console

import codiff.export.terminal as term
from codiff.export.terminal import (
    _build_color_map,
    _display_name,
    _name,
    _order_group,
    _partition,
)
from codiff.schema.diff import (
    AddedFunctionInfo,
    AnalysisResult,
    ModifiedFunctionInfo,
    RemovedFunctionInfo,
    SummaryStats,
)

# ---------------------------------------------------------------------------
# Helpers shared with mermaid tests — duplicated here for isolation
# ---------------------------------------------------------------------------


def _summary() -> SummaryStats:
    return SummaryStats(
        added_functions=0, removed_functions=0, modified_functions=0, modules_touched=[]
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


def _result(added=None, modified=None, removed=None, class_parents=None) -> AnalysisResult:
    return AnalysisResult(
        summary=_summary(),
        added=added or [],
        modified=modified or [],
        removed=removed or [],
        class_parents=class_parents or {},
    )


# ---------------------------------------------------------------------------
# Console capture fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def recording_console():
    """Replace the module-level console with a recording one for each test."""
    rec = Console(record=True, width=120, force_terminal=False, highlight=False)
    original = term.console
    term.console = rec
    yield rec
    term.console = original


def _render(result: AnalysisResult, **kwargs) -> str:
    """Render result and return plain text."""
    term.render(result, **kwargs)
    return term.console.export_text()


# ---------------------------------------------------------------------------
# Pure helper: _name
# ---------------------------------------------------------------------------


class TestName:
    def test_returns_last_segment(self):
        assert _name("pkg.mod.ClassName.method") == "method"

    def test_bare_name(self):
        assert _name("foo") == "foo"

    def test_single_dot(self):
        assert _name("mod.func") == "func"


# ---------------------------------------------------------------------------
# Pure helper: _display_name
# ---------------------------------------------------------------------------


class TestDisplayName:
    def test_class_method_shows_class_dot_name(self):
        fn = _added("pkg.mod.MyClass.do_thing", class_name="MyClass")
        assert _display_name(fn) == "MyClass.do_thing"

    def test_standalone_returns_bare_name(self):
        fn = _added("src.mod.my_func", file_path="src/mod.py")
        assert _display_name(fn) == "my_func"

    def test_nested_function_shows_outer_dot_inner(self):
        fn = _added("src.mod.outer.inner", file_path="src/mod.py")
        assert _display_name(fn) == "outer.inner"


# ---------------------------------------------------------------------------
# Pure helper: _partition
# ---------------------------------------------------------------------------


class TestPartition:
    def test_separates_test_from_source(self):
        src_fn = _added("src.mod.f", file_path="src/mod.py")
        tst_fn = _added("tests.test_mod.f", file_path="tests/test_mod.py")
        src = _partition([src_fn, tst_fn], test=False)
        tst = _partition([src_fn, tst_fn], test=True)
        assert src == [src_fn]
        assert tst == [tst_fn]

    def test_empty_list(self):
        assert _partition([], test=False) == []


# ---------------------------------------------------------------------------
# Pure helper: _build_color_map
# ---------------------------------------------------------------------------


class TestBuildColorMap:
    def test_single_function_gets_no_color(self):
        result = _result(added=[_added("mod.fa")])
        cm = _build_color_map(result)
        assert cm == {}

    def test_connected_pair_gets_same_color(self):
        fa = _added("mod.fa", new_calls=["mod.fb"], is_entry_point=False)
        fb = _added("mod.fb", new_callers=["mod.fa"], is_entry_point=False)
        cm = _build_color_map(_result(added=[fa, fb]))
        assert cm.get("mod.fa") is not None
        assert cm["mod.fa"] == cm["mod.fb"]

    def test_two_disconnected_chains_get_different_colors(self):
        fa = _added("mod.fa", new_calls=["mod.fb"], is_entry_point=False)
        fb = _added("mod.fb", new_callers=["mod.fa"], is_entry_point=False)
        fc = _added("mod.fc", new_calls=["mod.fd"], is_entry_point=False)
        fd = _added("mod.fd", new_callers=["mod.fc"], is_entry_point=False)
        cm = _build_color_map(_result(added=[fa, fb, fc, fd]))
        assert cm["mod.fa"] != cm["mod.fc"]

    def test_modified_connected_to_added_shares_color(self):
        fa = _added("mod.fa", new_callers=["mod.fm"], is_entry_point=False)
        fm = _modified("mod.fm", calls_added_new=["mod.fa"])
        cm = _build_color_map(_result(added=[fa], modified=[fm]))
        assert cm.get("mod.fa") is not None
        assert cm["mod.fa"] == cm["mod.fm"]


# ---------------------------------------------------------------------------
# Pure helper: _order_group
# ---------------------------------------------------------------------------


class TestOrderGroup:
    def test_single_function_unchanged(self):
        fn = _added("mod.f")
        assert _order_group([fn]) == [fn]

    def test_entry_point_comes_first(self):
        fa = _added("mod.fa", is_entry_point=True)
        fb = _added("mod.fb", is_entry_point=False)
        ordered = _order_group([fb, fa])
        assert ordered[0] == fa

    def test_callee_follows_caller_in_same_chain(self):
        caller = _added("mod.caller", new_calls=["mod.callee"], is_entry_point=True)
        callee = _added("mod.callee", new_callers=["mod.caller"], is_entry_point=False)
        cm = _build_color_map(_result(added=[caller, callee]))
        ordered = _order_group([callee, caller], cm)
        assert ordered.index(caller) < ordered.index(callee)


# ---------------------------------------------------------------------------
# render() — summary line
# ---------------------------------------------------------------------------


class TestSummaryLine:
    def test_no_changes_message(self):
        text = _render(_result())
        assert "No structural changes" in text

    def test_added_count_in_summary(self):
        text = _render(_result(added=[_added("mod.f")]))
        assert "+1 added" in text

    def test_modified_count_in_summary(self):
        text = _render(_result(modified=[_modified("mod.f")]))
        assert "~1 modified" in text

    def test_removed_count_in_summary(self):
        text = _render(_result(removed=[_removed("mod.f")]))
        assert "-1 removed" in text

    def test_module_count_in_summary(self):
        fa = _added("mod.fa", file_path="src/a.py")
        fb = _added("mod.fb", file_path="src/b.py")
        text = _render(_result(added=[fa, fb]))
        assert "2 modules" in text

    def test_single_module_singular(self):
        text = _render(_result(added=[_added("mod.f")]))
        assert "1 module" in text
        assert "modules" not in text

    def test_header_shows_refs(self):
        text = _render(_result(), base_ref="main", head_ref="feat/x")
        assert "main" in text
        assert "feat/x" in text


# ---------------------------------------------------------------------------
# render() — added functions
# ---------------------------------------------------------------------------


class TestRenderAdded:
    def test_added_function_name_appears(self):
        text = _render(_result(added=[_added("src.mod.my_func")]))
        assert "my_func" in text

    def test_entry_point_label_shown(self):
        fn = _added("src.mod.entry", is_entry_point=True)
        text = _render(_result(added=[fn]))
        assert "entry point" in text

    def test_plus_indicator_shown(self):
        text = _render(_result(added=[_added("src.mod.f")]))
        assert "+" in text

    def test_file_path_shown_in_panel(self):
        text = _render(_result(added=[_added("src.mod.f", file_path="src/mymodule.py")]))
        assert "src/mymodule.py" in text


# ---------------------------------------------------------------------------
# render() — modified functions
# ---------------------------------------------------------------------------


class TestRenderModified:
    def test_modified_function_name_appears(self):
        text = _render(_result(modified=[_modified("src.mod.f")]))
        assert "f" in text

    def test_tilde_indicator_shown(self):
        text = _render(_result(modified=[_modified("src.mod.f")]))
        assert "~" in text

    def test_sig_changed_annotation(self):
        text = _render(_result(modified=[_modified("src.mod.f", signature_changed=True)]))
        assert "sig changed" in text

    def test_body_changed_annotation(self):
        text = _render(_result(modified=[_modified("src.mod.f")]))
        assert "body changed" in text

    def test_calls_changed_annotation(self):
        text = _render(_result(modified=[_modified("src.mod.f", calls_added_new=["src.mod.g"])]))
        assert "calls changed" in text


# ---------------------------------------------------------------------------
# render() — removed functions
# ---------------------------------------------------------------------------


class TestRenderRemoved:
    def test_removed_function_appears_in_deleted_box(self):
        text = _render(_result(removed=[_removed("src.mod.old_func")]))
        assert "old_func" in text
        assert "deleted" in text

    def test_minus_indicator_shown(self):
        text = _render(_result(removed=[_removed("src.mod.gone")]))
        assert "-" in text


# ---------------------------------------------------------------------------
# render() — class grouping
# ---------------------------------------------------------------------------


class TestClassGrouping:
    def test_class_name_appears_as_box_title(self):
        fn = _added("src.mod.MyClass.method", class_name="MyClass")
        text = _render(_result(added=[fn]))
        assert "MyClass" in text

    def test_method_name_shown_without_class_prefix(self):
        fn = _added("src.mod.MyClass.do_thing", class_name="MyClass")
        text = _render(_result(added=[fn]))
        assert "do_thing" in text
        assert "MyClass.do_thing" not in text

    def test_standalone_and_class_both_shown(self):
        standalone = _added("src.mod.free_func")
        method = _added("src.mod.MyClass.method", class_name="MyClass")
        text = _render(_result(added=[standalone, method]))
        assert "free_func" in text
        assert "MyClass" in text


# ---------------------------------------------------------------------------
# render() — test filtering
# ---------------------------------------------------------------------------


class TestTestFiltering:
    def test_test_functions_hidden_by_default(self):
        src_fn = _added("src.mod.f", file_path="src/mod.py")
        tst_fn = _added("tests.test_mod.f", file_path="tests/test_mod.py")
        text = _render(_result(added=[src_fn, tst_fn]), include_tests=False)
        assert "test_mod" not in text

    def test_test_functions_shown_with_flag(self):
        tst_fn = _added("tests.test_mod.f", file_path="tests/test_mod.py")
        text = _render(_result(added=[tst_fn]), include_tests=True)
        assert "test_mod" in text

    def test_tests_section_label_only_with_flag(self):
        tst_fn = _added("tests.test_mod.f", file_path="tests/test_mod.py")
        text_without = _render(_result(added=[tst_fn]), include_tests=False)
        text_with = _render(_result(added=[tst_fn]), include_tests=True)
        assert "Tests" not in text_without
        assert "Tests" in text_with


# ---------------------------------------------------------------------------
# render() — intra-file class relationships
# ---------------------------------------------------------------------------


class TestIntraFileRelationships:
    def test_calls_relationship_shown(self):
        caller = _added(
            "src.mod.CallerClass.run",
            class_name="CallerClass",
            new_calls=["src.mod.CalleeClass.do"],
            is_entry_point=False,
        )
        callee = _added(
            "src.mod.CalleeClass.do",
            class_name="CalleeClass",
            new_callers=["src.mod.CallerClass.run"],
            is_entry_point=False,
        )
        text = _render(_result(added=[caller, callee]))
        assert "calls" in text
        assert "CalleeClass" in text
