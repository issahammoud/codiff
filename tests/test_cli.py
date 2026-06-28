import json
import sys
from unittest.mock import MagicMock, patch

from codiff.cli import _init_claude, _run_diff, _run_init, main
from codiff.schema.diff import AnalysisResult, GraphSnapshot, RemovedFunctionInfo, SummaryStats


def _fake_result(**kwargs):
    return AnalysisResult(
        summary=SummaryStats(
            added_functions=0, removed_functions=0, modified_functions=0, modules_touched=[]
        ),
        added=kwargs.get("added", []),
        modified=kwargs.get("modified", []),
        removed=kwargs.get("removed", []),
    )


def _fake_snap():
    return GraphSnapshot()


class TestInitClaude:
    def test_creates_mcp_json(self, tmp_path):
        _init_claude(str(tmp_path))
        config = json.loads((tmp_path / ".mcp.json").read_text())
        assert config["mcpServers"]["codiff"] == {"command": "codiff-mcp"}

    def test_skips_if_already_registered(self, tmp_path, capsys):
        existing = {"mcpServers": {"codiff": {"command": "codiff-mcp"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(existing))
        _init_claude(str(tmp_path))
        assert "skipped" in capsys.readouterr().out

    def test_merges_with_existing_servers(self, tmp_path):
        existing = {"mcpServers": {"other": {"command": "other"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(existing))
        _init_claude(str(tmp_path))
        config = json.loads((tmp_path / ".mcp.json").read_text())
        assert "codiff" in config["mcpServers"]
        assert "other" in config["mcpServers"]

    def test_handles_corrupt_existing_json(self, tmp_path):
        (tmp_path / ".mcp.json").write_text("NOT{JSON")
        _init_claude(str(tmp_path))
        config = json.loads((tmp_path / ".mcp.json").read_text())
        assert "codiff" in config["mcpServers"]


class TestRunInit:
    def test_creates_mcp_json(self, tmp_path, capsys):
        _run_init(str(tmp_path), "claude")
        assert (tmp_path / ".mcp.json").exists()
        assert "claude" in capsys.readouterr().out.lower()


class TestMainInit:
    def test_dispatches_to_init_claude(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "claude", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / ".mcp.json").exists()


class TestMainIndex:
    def test_dispatches_to_setup_repository(self, tmp_path):
        with patch("codiff.setup.setup_repository", return_value="abc") as mock:
            with patch.object(sys, "argv", ["codiff", "index", str(tmp_path)]):
                main()
        mock.assert_called_once_with(str(tmp_path))


class TestMainDiff:
    def test_dispatches_to_run_diff(self, tmp_path):
        with patch("codiff.cli._run_diff") as mock:
            with patch.object(sys, "argv", ["codiff", "diff", "--repo", str(tmp_path)]):
                main()
        mock.assert_called_once()

    def test_passes_all_flags(self, tmp_path):
        with patch("codiff.cli._run_diff") as mock:
            with patch.object(
                sys,
                "argv",
                [
                    "codiff",
                    "diff",
                    "--base",
                    "v1",
                    "--head",
                    "v2",
                    "--include-tests",
                    "--include-deleted",
                    "--format",
                    "mermaid",
                    "--repo",
                    str(tmp_path),
                ],
            ):
                main()
        kw = mock.call_args.kwargs
        assert kw["base_ref"] == "v1"
        assert kw["head_ref"] == "v2"
        assert kw["include_tests"] is True
        assert kw["include_deleted"] is True
        assert kw["fmt"] == "mermaid"


class TestRunDiff:
    """Cover _run_diff dispatch logic with heavy dependencies mocked."""

    def test_working_tree_path_calls_ensure_indexed(self, tmp_path):
        with (
            patch("codiff.diff.indexer.ensure_indexed") as mock_ensure,
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_incremental_head", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=_fake_result()),
            patch("codiff.export.render_terminal"),
        ):
            _run_diff(str(tmp_path), "HEAD")
            mock_ensure.assert_called_once()

    def test_two_ref_path_uses_db_cache_for_base(self, tmp_path):
        with (
            patch("codiff.diff.indexer.ensure_indexed") as mock_ensure,
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_incremental_head", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=_fake_result()),
            patch("codiff.export.render_terminal"),
        ):
            _run_diff(str(tmp_path), "v1", head_ref="v2")
        mock_ensure.assert_called_once()

    def test_fmt_json_prints_output(self, tmp_path, capsys):
        with (
            patch("codiff.diff.indexer.ensure_indexed"),
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_incremental_head", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=_fake_result()),
            patch("codiff.export.render_json", return_value='{"ok": true}'),
        ):
            _run_diff(str(tmp_path), "HEAD", fmt="json")
        assert '{"ok": true}' in capsys.readouterr().out

    def test_fmt_mermaid_prints_output(self, tmp_path, capsys):
        with (
            patch("codiff.diff.indexer.ensure_indexed"),
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_incremental_head", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=_fake_result()),
            patch("codiff.export.render_mermaid", return_value="```mermaid\n```"),
        ):
            _run_diff(str(tmp_path), "HEAD", fmt="mermaid")
        assert "mermaid" in capsys.readouterr().out

    def test_include_deleted_false_clears_removed(self, tmp_path):
        removed = [
            RemovedFunctionInfo(
                function_id="f", file_path="f.py", class_name=None, was_called_by=[]
            )
        ]
        result = _fake_result(removed=removed)
        with (
            patch("codiff.diff.indexer.ensure_indexed"),
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_incremental_head", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=result),
            patch("codiff.export.render_terminal"),
        ):
            _run_diff(str(tmp_path), "HEAD", include_deleted=False)
        assert result.removed == []
