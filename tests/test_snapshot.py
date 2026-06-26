import pytest

from codiff.diff.snapshot import _build_edges, build_from_path, build_from_ref, load_from_db
from codiff.schema.diff import GraphSnapshot, NodeInfo


def _node(fid, *, calls=None):
    return NodeInfo(
        function_id=fid,
        name=fid.split(".")[-1],
        file_path="f.py",
        class_name=None,
        parameters=[],
        return_type=None,
        calls=calls or [],
        code="pass",
    )


@pytest.fixture
def mini_repo(tmp_path):
    (tmp_path / "app.py").write_text("def foo():\n    pass\n\ndef bar():\n    foo()\n")
    return tmp_path


class TestBuildEdges:
    def test_adds_edge_for_known_callee(self):
        snap = GraphSnapshot()
        snap.nodes["a"] = _node("a", calls=["b"])
        snap.nodes["b"] = _node("b")
        _build_edges(snap)
        assert ("a", "b") in snap.edges

    def test_ignores_external_calls(self):
        snap = GraphSnapshot()
        snap.nodes["a"] = _node("a", calls=["os.path.join"])
        _build_edges(snap)
        assert not snap.edges

    def test_unknown_callee_produces_no_edge(self):
        snap = GraphSnapshot()
        snap.nodes["a"] = _node("a", calls=["missing.fn"])
        _build_edges(snap)
        assert not snap.edges


class TestBuildFromPath:
    def test_returns_graph_snapshot(self, mini_repo):
        snap = build_from_path(str(mini_repo))
        assert isinstance(snap, GraphSnapshot)

    def test_finds_functions(self, mini_repo):
        snap = build_from_path(str(mini_repo))
        names = {n.name for n in snap.nodes.values()}
        assert "foo" in names
        assert "bar" in names

    def test_empty_dir_gives_empty_snapshot(self, tmp_path):
        snap = build_from_path(str(tmp_path))
        assert len(snap.nodes) == 0


class TestLoadFromDb:
    def test_loads_functions(self, mini_repo):
        from codiff.setup import setup_repository

        setup_repository(str(mini_repo))
        snap = load_from_db(str(mini_repo / ".codiff.db"))
        assert isinstance(snap, GraphSnapshot)
        names = {n.name for n in snap.nodes.values()}
        assert "foo" in names

    def test_builds_edges(self, mini_repo):
        from codiff.setup import setup_repository

        setup_repository(str(mini_repo))
        snap = load_from_db(str(mini_repo / ".codiff.db"))
        bar_id = next(n.function_id for n in snap.nodes.values() if n.name == "bar")
        foo_id = next(n.function_id for n in snap.nodes.values() if n.name == "foo")
        assert (bar_id, foo_id) in snap.edges


class TestBuildFromRef:
    def test_returns_graph_snapshot(self, git_repo):
        snap = build_from_ref(str(git_repo), "HEAD")
        assert isinstance(snap, GraphSnapshot)

    def test_finds_committed_functions(self, git_repo):
        snap = build_from_ref(str(git_repo), "HEAD")
        names = {n.name for n in snap.nodes.values()}
        assert "hello" in names
        assert "world" in names
