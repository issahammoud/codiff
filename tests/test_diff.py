"""Tests for the graph-diff layer.

All tests use synthetic GraphSnapshot fixtures — no DB, no real repo, no git.
"""

from codiff.diff.analysis import analyze
from codiff.diff.differ import _node_changed, diff_snapshots
from codiff.schema.diff import GraphSnapshot, NodeInfo

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _node(
    function_id: str,
    *,
    name: str = "",
    file_path: str = "mod.py",
    class_name: str | None = None,
    parameters: list[dict] | None = None,
    return_type: str | None = None,
    calls: list[str] | None = None,
    code: str = "pass",
) -> NodeInfo:
    return NodeInfo(
        function_id=function_id,
        name=name or function_id.split(".")[-1],
        file_path=file_path,
        class_name=class_name,
        parameters=parameters or [],
        return_type=return_type,
        calls=calls or [],
        code=code,
    )


def _snapshot(*nodes: NodeInfo) -> GraphSnapshot:
    snap = GraphSnapshot()
    for n in nodes:
        snap.nodes[n.function_id] = n
    all_ids = set(snap.nodes)
    for node in snap.nodes.values():
        for callee in node.calls:
            if callee in all_ids:
                snap.edges.add((node.function_id, callee))
    return snap


# ---------------------------------------------------------------------------
# differ tests
# ---------------------------------------------------------------------------


class TestDiffSnapshots:
    def test_added_node(self):
        base = _snapshot(_node("mod.a"))
        head = _snapshot(_node("mod.a"), _node("mod.b"))
        diff = diff_snapshots(base, head)
        assert "mod.b" in diff.added_nodes
        assert "mod.a" not in diff.added_nodes
        assert not diff.removed_nodes

    def test_removed_node(self):
        base = _snapshot(_node("mod.a"), _node("mod.b"))
        head = _snapshot(_node("mod.a"))
        diff = diff_snapshots(base, head)
        assert "mod.b" in diff.removed_nodes
        assert not diff.added_nodes

    def test_modified_node_code_change(self):
        base = _snapshot(_node("mod.a", code="return 1"))
        head = _snapshot(_node("mod.a", code="return 2"))
        diff = diff_snapshots(base, head)
        assert "mod.a" in diff.modified_nodes
        assert not diff.added_nodes
        assert not diff.removed_nodes

    def test_unmodified_node_not_in_diff(self):
        node = _node("mod.a", code="return 1")
        base = _snapshot(node)
        head = _snapshot(node)
        diff = diff_snapshots(base, head)
        assert not diff.added_nodes
        assert not diff.removed_nodes
        assert not diff.modified_nodes

    def test_added_edge(self):
        base = _snapshot(_node("mod.a"), _node("mod.b"))
        head = _snapshot(_node("mod.a", calls=["mod.b"]), _node("mod.b"))
        diff = diff_snapshots(base, head)
        assert ("mod.a", "mod.b") in diff.added_edges
        assert not diff.removed_edges

    def test_removed_edge(self):
        base = _snapshot(_node("mod.a", calls=["mod.b"]), _node("mod.b"))
        head = _snapshot(_node("mod.a"), _node("mod.b"))
        diff = diff_snapshots(base, head)
        assert ("mod.a", "mod.b") in diff.removed_edges
        assert not diff.added_edges

    def test_empty_snapshots_produce_empty_diff(self):
        diff = diff_snapshots(_snapshot(), _snapshot())
        assert not diff.added_nodes
        assert not diff.removed_nodes
        assert not diff.modified_nodes
        assert not diff.added_edges
        assert not diff.removed_edges


class TestNodeChanged:
    def test_code_change_detected(self):
        base = _node("f", code="a")
        head = _node("f", code="b")
        assert _node_changed(base, head)

    def test_parameter_change_detected(self):
        base = _node("f", parameters=[{"name": "x", "type": None, "value": None}])
        head = _node("f", parameters=[{"name": "y", "type": None, "value": None}])
        assert _node_changed(base, head)

    def test_return_type_change_detected(self):
        base = _node("f", return_type="int")
        head = _node("f", return_type="str")
        assert _node_changed(base, head)

    def test_calls_change_detected(self):
        base = _node("f", calls=["mod.a"])
        head = _node("f", calls=["mod.b"])
        assert _node_changed(base, head)

    def test_file_path_change_detected(self):
        base = _node("f", file_path="old.py")
        head = _node("f", file_path="new.py")
        assert _node_changed(base, head)

    def test_no_change_returns_false(self):
        n = _node("f", code="x", return_type="int", calls=["g"])
        assert not _node_changed(n, n)


# ---------------------------------------------------------------------------
# analysis tests — summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_counts_are_correct(self):
        base = _snapshot(_node("mod.a"), _node("mod.b"))
        head = _snapshot(
            _node("mod.a", code="changed"),
            _node("mod.c"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert result.summary.added_functions == 1
        assert result.summary.removed_functions == 1
        assert result.summary.modified_functions == 1

    def test_modules_touched(self):
        base = _snapshot(_node("mod.a", file_path="a.py"))
        head = _snapshot(
            _node("mod.a", file_path="a.py", code="changed"),
            _node("mod.b", file_path="b.py"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "a.py" in result.summary.modules_touched
        assert "b.py" in result.summary.modules_touched


# ---------------------------------------------------------------------------
# analysis tests — added functions
# ---------------------------------------------------------------------------


class TestAdded:
    def test_new_function_with_existing_caller(self):
        base = _snapshot(_node("mod.caller"))
        head = _snapshot(
            _node("mod.caller", calls=["mod.new_fn"]),
            _node("mod.new_fn"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert len(result.added) == 1
        fn = result.added[0]
        assert fn.function_id == "mod.new_fn"
        assert "mod.caller" in fn.existing_callers
        assert not fn.new_callers
        assert not fn.is_entry_point

    def test_new_function_with_no_callers_is_entry_point(self):
        base = _snapshot()
        head = _snapshot(_node("mod.new_fn"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.added[0]
        assert fn.is_entry_point
        assert not fn.existing_callers
        assert not fn.new_callers

    def test_new_function_calling_new_vs_existing(self):
        # mod.existing is in both; mod.also_new is added together with mod.new_fn
        base = _snapshot(_node("mod.existing"))
        head = _snapshot(
            _node("mod.existing"),
            _node("mod.new_fn", calls=["mod.existing", "mod.also_new"]),
            _node("mod.also_new"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = next(f for f in result.added if f.function_id == "mod.new_fn")
        assert "mod.existing" in fn.existing_calls
        assert "mod.also_new" in fn.new_calls

    def test_new_function_called_by_new_caller(self):
        # Both mod.caller and mod.new_fn are new; caller → new_fn
        base = _snapshot()
        head = _snapshot(
            _node("mod.caller", calls=["mod.new_fn"]),
            _node("mod.new_fn"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = next(f for f in result.added if f.function_id == "mod.new_fn")
        assert "mod.caller" in fn.new_callers
        assert not fn.existing_callers
        assert not fn.is_entry_point


# ---------------------------------------------------------------------------
# analysis tests — modified functions
# ---------------------------------------------------------------------------


class TestModified:
    def test_calls_added_to_existing_function(self):
        base = _snapshot(_node("mod.fn"), _node("mod.target"))
        head = _snapshot(
            _node("mod.fn", calls=["mod.target"]),
            _node("mod.target"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert len(result.modified) == 1
        fn = result.modified[0]
        assert "mod.target" in fn.calls_added_existing
        assert not fn.calls_added_new

    def test_calls_added_new_function(self):
        base = _snapshot(_node("mod.fn"))
        head = _snapshot(
            _node("mod.fn", calls=["mod.new_target"]),
            _node("mod.new_target"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.modified[0]
        assert "mod.new_target" in fn.calls_added_new
        assert not fn.calls_added_existing

    def test_calls_removed(self):
        base = _snapshot(
            _node("mod.fn", calls=["mod.target"]),
            _node("mod.target"),
        )
        head = _snapshot(
            _node("mod.fn", code="changed"),
            _node("mod.target"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.modified[0]
        assert "mod.target" in fn.calls_removed

    def test_signature_changed_detected(self):
        base = _snapshot(_node("mod.fn", parameters=[{"name": "x", "type": "int", "value": None}]))
        head = _snapshot(_node("mod.fn", parameters=[{"name": "x", "type": "str", "value": None}]))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.modified[0]
        assert fn.signature_changed
        assert fn.old_params[0]["type"] == "int"
        assert fn.new_params[0]["type"] == "str"

    def test_body_only_change_no_signature_no_calls(self):
        base = _snapshot(_node("mod.fn", code="return 1"))
        head = _snapshot(_node("mod.fn", code="return 2"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.modified[0]
        assert not fn.signature_changed
        assert not fn.calls_added_new
        assert not fn.calls_added_existing
        assert not fn.calls_removed

    def test_callers_in_head_populated(self):
        base = _snapshot(
            _node("mod.caller", calls=["mod.fn"]),
            _node("mod.fn"),
        )
        head = _snapshot(
            _node("mod.caller", calls=["mod.fn"]),
            _node("mod.fn", code="changed"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.modified[0]
        assert "mod.caller" in fn.callers


# ---------------------------------------------------------------------------
# analysis tests — removed functions
# ---------------------------------------------------------------------------


class TestRemoved:
    def test_removed_function_listed(self):
        base = _snapshot(_node("mod.a"), _node("mod.b"))
        head = _snapshot(_node("mod.a"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert len(result.removed) == 1
        assert result.removed[0].function_id == "mod.b"

    def test_was_called_by_populated(self):
        base = _snapshot(
            _node("mod.caller", calls=["mod.fn"]),
            _node("mod.fn"),
        )
        head = _snapshot(_node("mod.caller"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        fn = result.removed[0]
        assert "mod.caller" in fn.was_called_by

    def test_removed_function_file_path_correct(self):
        base = _snapshot(_node("mod.fn", file_path="src/mod.py"))
        head = _snapshot()
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert result.removed[0].file_path == "src/mod.py"
