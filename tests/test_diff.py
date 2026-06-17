"""Tests for the graph-diff layer.

All tests use synthetic GraphSnapshot fixtures — no DB, no real repo, no git.
"""

from codiff.diff.analysis import HIGH_FAN_IN_THRESHOLD, analyze
from codiff.diff.differ import _node_changed, diff_snapshots
from codiff.diff.snapshot import GraphSnapshot, NodeInfo

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
    # Populate edges
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
# analysis tests
# ---------------------------------------------------------------------------


class TestSummary:
    def test_counts_are_correct(self):
        base = _snapshot(_node("mod.a"), _node("mod.b"))
        head = _snapshot(
            _node("mod.a", code="changed"),  # modified
            _node("mod.c"),  # added
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        s = result.summary
        assert s.added_functions == 1
        assert s.removed_functions == 1
        assert s.modified_functions == 1

    def test_modules_touched(self):
        base = _snapshot(_node("mod.a", file_path="a.py"))
        head = _snapshot(
            _node("mod.a", file_path="a.py", code="changed"), _node("mod.b", file_path="b.py")
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "a.py" in result.summary.modules_touched
        assert "b.py" in result.summary.modules_touched

    def test_edge_counts_in_summary(self):
        base = _snapshot(_node("mod.a", calls=["mod.b"]), _node("mod.b"))
        head = _snapshot(_node("mod.a"), _node("mod.b", calls=["mod.a"]))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert result.summary.added_edges == 1
        assert result.summary.removed_edges == 1


class TestChainInsertion:
    def test_chain_insertion_detected(self):
        # base: A → C
        base = _snapshot(
            _node("mod.A", calls=["mod.C"]),
            _node("mod.C"),
        )
        # head: A → N → C  (N is new)
        head = _snapshot(
            _node("mod.A", calls=["mod.N"]),
            _node("mod.N", calls=["mod.C"]),  # new node
            _node("mod.C"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        insertions = result.wiring.chain_insertions
        assert len(insertions) == 1
        ins = insertions[0]
        assert ins.new_node_id == "mod.N"
        assert ins.predecessor_id == "mod.A"
        assert ins.successor_id == "mod.C"

    def test_no_false_chain_insertion(self):
        # New node added but no existing A→B edge is split
        base = _snapshot(_node("mod.A"), _node("mod.B"))
        head = _snapshot(_node("mod.A"), _node("mod.B"), _node("mod.N"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert not result.wiring.chain_insertions


class TestBlastRadius:
    def test_upstream_callers_of_modified_node(self):
        # X calls A; A is modified; Y is unrelated
        base = _snapshot(
            _node("mod.X", calls=["mod.A"]),
            _node("mod.A"),
            _node("mod.Y"),
        )
        head = _snapshot(
            _node("mod.X", calls=["mod.A"]),
            _node("mod.A", code="changed"),
            _node("mod.Y"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.X" in result.blast.upstream_callers.get("mod.A", [])
        assert "mod.Y" not in result.blast.upstream_callers.get("mod.A", [])

    def test_downstream_callees_of_modified_node(self):
        base = _snapshot(
            _node("mod.A", calls=["mod.B"]),
            _node("mod.B"),
        )
        head = _snapshot(
            _node("mod.A", calls=["mod.B"], code="changed"),
            _node("mod.B"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.B" in result.blast.downstream_callees.get("mod.A", [])

    def test_removed_node_not_in_blast(self):
        base = _snapshot(_node("mod.A"), _node("mod.B"))
        head = _snapshot(_node("mod.A"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.B" not in result.blast.upstream_callers
        assert "mod.B" not in result.blast.downstream_callees


class TestLiveness:
    def test_dead_on_arrival(self):
        base = _snapshot()
        head = _snapshot(_node("mod.new_fn"))  # nothing calls it
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.new_fn" in result.liveness.dead_on_arrival

    def test_called_new_fn_not_dead_on_arrival(self):
        base = _snapshot(_node("mod.caller"))
        head = _snapshot(
            _node("mod.caller", calls=["mod.new_fn"]),
            _node("mod.new_fn"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.new_fn" not in result.liveness.dead_on_arrival

    def test_newly_orphaned(self):
        # mod.B had a caller in base; that caller is removed in head
        base = _snapshot(
            _node("mod.A", calls=["mod.B"]),
            _node("mod.B"),
        )
        head = _snapshot(
            _node("mod.A"),  # no longer calls B
            _node("mod.B"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.B" in result.liveness.newly_orphaned

    def test_not_orphaned_if_still_has_caller(self):
        base = _snapshot(
            _node("mod.A", calls=["mod.B"]),
            _node("mod.C", calls=["mod.B"]),
            _node("mod.B"),
        )
        head = _snapshot(
            _node("mod.A"),  # drops call to B
            _node("mod.C", calls=["mod.B"]),
            _node("mod.B"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert "mod.B" not in result.liveness.newly_orphaned


class TestBoundaries:
    def test_new_cross_module_edge_detected(self):
        base = _snapshot(
            _node("a.fn", file_path="a.py"),
            _node("b.fn", file_path="b.py"),
        )
        head = _snapshot(
            _node("a.fn", file_path="a.py", calls=["b.fn"]),
            _node("b.fn", file_path="b.py"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert ("a.fn", "b.fn") in result.boundaries.new_cross_module_edges

    def test_intra_module_edge_not_in_boundaries(self):
        base = _snapshot(
            _node("a.fn1", file_path="a.py"),
            _node("a.fn2", file_path="a.py"),
        )
        head = _snapshot(
            _node("a.fn1", file_path="a.py", calls=["a.fn2"]),
            _node("a.fn2", file_path="a.py"),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert not result.boundaries.new_cross_module_edges


class TestSignatureChanges:
    def test_parameter_change_reported(self):
        base = _snapshot(
            _node("mod.A", calls=["mod.fn"]),
            _node("mod.fn", parameters=[{"name": "x", "type": "int", "value": None}]),
        )
        head = _snapshot(
            _node("mod.A", calls=["mod.fn"]),
            _node("mod.fn", parameters=[{"name": "x", "type": "str", "value": None}]),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert len(result.signatures.changes) == 1
        ch = result.signatures.changes[0]
        assert ch.function_id == "mod.fn"

    def test_unreconciled_callers_identified(self):
        base = _snapshot(
            _node("mod.caller", calls=["mod.fn"]),
            _node("mod.fn", parameters=[{"name": "x", "type": "int", "value": None}]),
        )
        head = _snapshot(
            _node("mod.caller", calls=["mod.fn"]),  # caller NOT updated
            _node("mod.fn", parameters=[{"name": "x", "type": "str", "value": None}]),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        ch = result.signatures.changes[0]
        assert "mod.caller" in ch.unreconciled_callers

    def test_return_type_change_reported(self):
        base = _snapshot(_node("mod.fn", return_type="int"))
        head = _snapshot(_node("mod.fn", return_type="str"))
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert any(ch.function_id == "mod.fn" for ch in result.signatures.changes)

    def test_code_only_change_not_in_signatures(self):
        base = _snapshot(
            _node("mod.fn", code="return 1", parameters=[], return_type=None),
        )
        head = _snapshot(
            _node("mod.fn", code="return 2", parameters=[], return_type=None),
        )
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert not result.signatures.changes


class TestFlags:
    def test_high_fan_in_flagged(self):
        callers = [_node(f"mod.c{i}", calls=["mod.fn"]) for i in range(HIGH_FAN_IN_THRESHOLD)]
        base = _snapshot(_node("mod.fn"), *callers)
        head = _snapshot(_node("mod.fn", code="changed"), *callers)
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert any("mod.fn" in f for f in result.flags)

    def test_low_fan_in_not_flagged(self):
        callers = [_node(f"mod.c{i}", calls=["mod.fn"]) for i in range(HIGH_FAN_IN_THRESHOLD - 1)]
        base = _snapshot(_node("mod.fn"), *callers)
        head = _snapshot(_node("mod.fn", code="changed"), *callers)
        diff = diff_snapshots(base, head)
        result = analyze(diff, base, head)
        assert not result.flags
