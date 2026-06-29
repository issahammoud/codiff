import json
import sys
from unittest.mock import MagicMock, patch

from codiff.cli import (
    _init_claude,
    _init_codex,
    _init_copilot,
    _init_cursor,
    _init_gemini,
    _init_vibe,
    _init_windsurf,
    _run_diff,
    _run_init,
    main,
)
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

    def test_creates_claude_md_when_missing(self, tmp_path):
        _init_claude(str(tmp_path))
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "codiff_diff" in content
        assert "mermaid" in content

    def test_appends_to_existing_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# My project\n\nExisting instructions.\n")
        _init_claude(str(tmp_path))
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "My project" in content
        assert "codiff_diff" in content

    def test_skips_claude_md_if_already_present(self, tmp_path, capsys):
        (tmp_path / "CLAUDE.md").write_text("<!-- codiff -->\nAlready here.\n")
        _init_claude(str(tmp_path))
        assert "skipped" in capsys.readouterr().out
        assert (tmp_path / "CLAUDE.md").read_text() == "<!-- codiff -->\nAlready here.\n"


class TestInitCursor:
    def test_creates_mcp_json(self, tmp_path):
        _init_cursor(str(tmp_path))
        config = json.loads((tmp_path / ".cursor/mcp.json").read_text())
        assert config["mcpServers"]["codiff"] == {"command": "codiff-mcp"}

    def test_creates_rules_file(self, tmp_path):
        _init_cursor(str(tmp_path))
        mdc = (tmp_path / ".cursor/rules/codiff.mdc").read_text()
        assert "alwaysApply: true" in mdc
        assert "codiff_diff" in mdc

    def test_skips_rules_if_exists(self, tmp_path, capsys):
        rules = tmp_path / ".cursor/rules/codiff.mdc"
        rules.parent.mkdir(parents=True)
        rules.write_text("existing content\n")
        _init_cursor(str(tmp_path))
        assert "skipped" in capsys.readouterr().out
        assert rules.read_text() == "existing content\n"

    def test_merges_existing_mcp_json(self, tmp_path):
        mcp = tmp_path / ".cursor/mcp.json"
        mcp.parent.mkdir(parents=True)
        mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        _init_cursor(str(tmp_path))
        config = json.loads(mcp.read_text())
        assert "codiff" in config["mcpServers"]
        assert "other" in config["mcpServers"]


class TestInitCopilot:
    def test_creates_vscode_mcp_json(self, tmp_path):
        _init_copilot(str(tmp_path))
        config = json.loads((tmp_path / ".vscode/mcp.json").read_text())
        assert config["servers"]["codiff"] == {"type": "stdio", "command": "codiff-mcp"}

    def test_creates_copilot_instructions(self, tmp_path):
        _init_copilot(str(tmp_path))
        content = (tmp_path / ".github/copilot-instructions.md").read_text()
        assert "codiff_diff" in content
        assert "mermaid" in content

    def test_skips_instructions_if_marker_present(self, tmp_path, capsys):
        path = tmp_path / ".github/copilot-instructions.md"
        path.parent.mkdir(parents=True)
        path.write_text("<!-- codiff -->\nAlready here.\n")
        _init_copilot(str(tmp_path))
        assert "skipped" in capsys.readouterr().out
        assert path.read_text() == "<!-- codiff -->\nAlready here.\n"

    def test_appends_to_existing_instructions(self, tmp_path):
        path = tmp_path / ".github/copilot-instructions.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Existing\n\nOther instructions.\n")
        _init_copilot(str(tmp_path))
        content = path.read_text()
        assert "Existing" in content
        assert "codiff_diff" in content


class TestInitCodex:
    def test_creates_toml_config(self, tmp_path):
        _init_codex(str(tmp_path))
        content = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.codiff]" in content
        assert 'command = "codiff-mcp"' in content

    def test_skips_toml_if_already_registered(self, tmp_path, capsys):
        config = tmp_path / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[mcp_servers.codiff]\ncommand = "codiff-mcp"\n')
        _init_codex(str(tmp_path))
        assert "skipped" in capsys.readouterr().out
        assert config.read_text().count("[mcp_servers.codiff]") == 1

    def test_merges_with_existing_toml(self, tmp_path):
        config = tmp_path / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[mcp_servers.other]\ncommand = "other"\n')
        _init_codex(str(tmp_path))
        content = config.read_text()
        assert "[mcp_servers.codiff]" in content
        assert "[mcp_servers.other]" in content

    def test_creates_agents_md(self, tmp_path):
        _init_codex(str(tmp_path))
        content = (tmp_path / "AGENTS.md").read_text()
        assert "codiff_diff" in content
        assert "mermaid" in content

    def test_appends_to_existing_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Existing\n\nOther instructions.\n")
        _init_codex(str(tmp_path))
        content = (tmp_path / "AGENTS.md").read_text()
        assert "Existing" in content
        assert "codiff_diff" in content

    def test_skips_agents_md_if_marker_present(self, tmp_path, capsys):
        (tmp_path / "AGENTS.md").write_text("<!-- codiff -->\nAlready here.\n")
        _init_codex(str(tmp_path))
        assert "skipped" in capsys.readouterr().out


class TestInitWindsurf:
    def _setup(self, tmp_path, monkeypatch):
        """Return (fake_home, repo) and redirect HOME so Path.home() is sandboxed."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        return fake_home, repo

    def test_creates_mcp_config(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_windsurf(str(repo))
        mcp = fake_home / ".codeium/windsurf/mcp_config.json"
        config = json.loads(mcp.read_text())
        assert config["mcpServers"]["codiff"] == {"command": "codiff-mcp"}

    def test_creates_windsurfrules(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_windsurf(str(repo))
        content = (repo / ".windsurfrules").read_text()
        assert "codiff_diff" in content

    def test_skips_windsurfrules_if_marker_present(self, tmp_path, monkeypatch, capsys):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        (repo / ".windsurfrules").write_text("<!-- codiff -->\nAlready here.\n")
        _init_windsurf(str(repo))
        assert "skipped" in capsys.readouterr().out

    def test_merges_existing_mcp_config(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        mcp = fake_home / ".codeium/windsurf/mcp_config.json"
        mcp.parent.mkdir(parents=True)
        mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        _init_windsurf(str(repo))
        config = json.loads(mcp.read_text())
        assert "codiff" in config["mcpServers"]
        assert "other" in config["mcpServers"]


class TestInitGemini:
    def test_creates_gemini_md(self, tmp_path):
        _init_gemini(str(tmp_path))
        content = (tmp_path / "GEMINI.md").read_text()
        assert "codiff_diff" in content
        assert "mermaid" in content

    def test_appends_to_existing_gemini_md(self, tmp_path):
        (tmp_path / "GEMINI.md").write_text("# Existing\n\nOther instructions.\n")
        _init_gemini(str(tmp_path))
        content = (tmp_path / "GEMINI.md").read_text()
        assert "Existing" in content
        assert "codiff_diff" in content

    def test_skips_if_marker_present(self, tmp_path, capsys):
        (tmp_path / "GEMINI.md").write_text("<!-- codiff -->\nAlready here.\n")
        _init_gemini(str(tmp_path))
        assert "skipped" in capsys.readouterr().out


class TestInitVibe:
    def test_creates_config_toml(self, tmp_path):
        _init_vibe(str(tmp_path))
        content = (tmp_path / ".vibe/config.toml").read_text()
        assert "[[mcp_servers]]" in content
        assert 'name = "codiff"' in content
        assert 'transport = "stdio"' in content
        assert 'command = "codiff-mcp"' in content

    def test_skips_if_already_registered(self, tmp_path, capsys):
        config = tmp_path / ".vibe/config.toml"
        config.parent.mkdir(parents=True)
        original = '[[mcp_servers]]\nname = "codiff"\ntransport = "stdio"\ncommand = "codiff-mcp"\nargs = []\n'
        config.write_text(original)
        _init_vibe(str(tmp_path))
        assert "skipped" in capsys.readouterr().out
        assert config.read_text() == original

    def test_appends_to_existing_config(self, tmp_path):
        config = tmp_path / ".vibe/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[model]\nname = "devstral"\n')
        _init_vibe(str(tmp_path))
        content = config.read_text()
        assert 'name = "devstral"' in content
        assert "[[mcp_servers]]" in content

    def test_handles_corrupt_toml(self, tmp_path):
        config = tmp_path / ".vibe/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("NOT VALID TOML }{{\n")
        _init_vibe(str(tmp_path))
        content = config.read_text()
        assert "[[mcp_servers]]" in content


class TestInitFileInspection:
    """Verify exact file content and idempotency for every agent init command."""

    def _setup(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        return fake_home, repo

    def _run_all(self, repo, monkeypatch):
        _init_claude(str(repo))
        _init_cursor(str(repo))
        _init_copilot(str(repo))
        _init_codex(str(repo))
        _init_windsurf(str(repo))
        _init_gemini(str(repo))
        _init_vibe(str(repo))

    # ── per-agent file content ─────────────────────────────────────────────

    def test_claude_mcp_json(self, tmp_path):
        _init_claude(str(tmp_path))
        cfg = json.loads((tmp_path / ".mcp.json").read_text())
        assert cfg == {"mcpServers": {"codiff": {"command": "codiff-mcp"}}}

    def test_claude_md_content(self, tmp_path):
        _init_claude(str(tmp_path))
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "<!-- codiff -->" in text
        assert "<!-- /codiff -->" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_cursor_mcp_json(self, tmp_path):
        _init_cursor(str(tmp_path))
        cfg = json.loads((tmp_path / ".cursor/mcp.json").read_text())
        assert cfg == {"mcpServers": {"codiff": {"command": "codiff-mcp"}}}

    def test_cursor_rules_mdc(self, tmp_path):
        _init_cursor(str(tmp_path))
        text = (tmp_path / ".cursor/rules/codiff.mdc").read_text()
        assert "alwaysApply: true" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_copilot_vscode_mcp_json(self, tmp_path):
        _init_copilot(str(tmp_path))
        cfg = json.loads((tmp_path / ".vscode/mcp.json").read_text())
        assert cfg == {"servers": {"codiff": {"type": "stdio", "command": "codiff-mcp"}}}

    def test_copilot_instructions_md(self, tmp_path):
        _init_copilot(str(tmp_path))
        text = (tmp_path / ".github/copilot-instructions.md").read_text()
        assert "<!-- codiff -->" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_codex_toml_config(self, tmp_path):
        _init_codex(str(tmp_path))
        text = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.codiff]" in text
        assert 'command = "codiff-mcp"' in text

    def test_codex_agents_md(self, tmp_path):
        _init_codex(str(tmp_path))
        text = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- codiff -->" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_codex_idempotent(self, tmp_path):
        _init_codex(str(tmp_path))
        _init_codex(str(tmp_path))
        assert (tmp_path / ".codex" / "config.toml").read_text().count("[mcp_servers.codiff]") == 1
        assert (tmp_path / "AGENTS.md").read_text().count("<!-- codiff -->") == 1

    def test_windsurf_global_mcp(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_windsurf(str(repo))
        cfg = json.loads((fake_home / ".codeium/windsurf/mcp_config.json").read_text())
        assert cfg == {"mcpServers": {"codiff": {"command": "codiff-mcp"}}}

    def test_windsurf_rules(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_windsurf(str(repo))
        text = (repo / ".windsurfrules").read_text()
        assert "<!-- codiff -->" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_gemini_md(self, tmp_path):
        _init_gemini(str(tmp_path))
        text = (tmp_path / "GEMINI.md").read_text()
        assert "<!-- codiff -->" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_vibe_config_toml(self, tmp_path):
        import tomllib

        _init_vibe(str(tmp_path))
        with open(tmp_path / ".vibe/config.toml", "rb") as f:
            cfg = tomllib.load(f)
        assert len(cfg["mcp_servers"]) == 1
        server = cfg["mcp_servers"][0]
        assert server["name"] == "codiff"
        assert server["transport"] == "stdio"
        assert server["command"] == "codiff-mcp"

    # ── idempotency ────────────────────────────────────────────────────────

    def test_idempotency_all_agents(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)

        self._run_all(repo, monkeypatch)

        tracked = {
            repo / ".mcp.json",
            repo / "CLAUDE.md",
            repo / ".cursor/mcp.json",
            repo / ".cursor/rules/codiff.mdc",
            repo / ".vscode/mcp.json",
            repo / ".github/copilot-instructions.md",
            repo / "AGENTS.md",
            repo / ".windsurfrules",
            repo / "GEMINI.md",
            repo / ".vibe/config.toml",
            fake_home / ".codeium/windsurf/mcp_config.json",
        }
        snapshots = {p: p.read_text() for p in tracked}

        self._run_all(repo, monkeypatch)

        for p, before in snapshots.items():
            assert p.read_text() == before, f"{p.name} changed on second run"


class TestRunInit:
    def test_creates_mcp_json(self, tmp_path, capsys):
        _run_init(str(tmp_path), "claude")
        assert (tmp_path / ".mcp.json").exists()
        assert "claude" in capsys.readouterr().out.lower()

    def test_all_agents_dispatch(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        for agent, expected_file, base in [
            ("claude", ".mcp.json", tmp_path),
            ("cursor", ".cursor/mcp.json", tmp_path),
            ("copilot", ".vscode/mcp.json", tmp_path),
            ("codex", "AGENTS.md", tmp_path),
            ("windsurf", ".codeium/windsurf/mcp_config.json", fake_home),
            ("gemini", "GEMINI.md", tmp_path),
            ("vibe", ".vibe/config.toml", tmp_path),
        ]:
            _run_init(str(tmp_path), agent)
            assert (base / expected_file).exists(), f"missing {expected_file} for {agent}"


class TestMainInit:
    def test_dispatches_to_init_claude(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "claude", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / ".mcp.json").exists()

    def test_dispatches_to_init_cursor(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "cursor", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / ".cursor/mcp.json").exists()

    def test_dispatches_to_init_copilot(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "copilot", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / ".vscode/mcp.json").exists()

    def test_dispatches_to_init_codex(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "codex", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / "AGENTS.md").exists()

    def test_dispatches_to_init_windsurf(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "windsurf", "--repo", str(tmp_path)]
        ):
            main()
        assert (fake_home / ".codeium/windsurf/mcp_config.json").exists()

    def test_dispatches_to_init_gemini(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "gemini", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / "GEMINI.md").exists()

    def test_dispatches_to_init_vibe(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "vibe", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / ".vibe/config.toml").exists()


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
            patch("codiff.diff.indexer.ensure_indexed", return_value="abc" * 13) as mock_ensure,
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_snapshot_incremental", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=_fake_result()),
            patch("codiff.export.render_terminal"),
        ):
            _run_diff(str(tmp_path), "HEAD")
        mock_ensure.assert_called_once()
        assert mock_ensure.call_args.args[1] == "HEAD"

    def test_two_ref_path_anchors_db_at_head(self, tmp_path):
        with (
            patch("codiff.diff.indexer.ensure_indexed", return_value="abc" * 13) as mock_ensure,
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch(
                "codiff.diff.snapshot.build_snapshot_incremental", return_value=_fake_snap()
            ) as mock_build,
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=_fake_result()),
            patch("codiff.export.render_terminal"),
        ):
            _run_diff(str(tmp_path), "v1", head_ref="v2")
        assert mock_ensure.call_args.args[1] == "HEAD"
        assert mock_build.call_count == 2  # once for base, once for head

    def test_fmt_json_prints_output(self, tmp_path, capsys):
        with (
            patch("codiff.diff.indexer.ensure_indexed"),
            patch("codiff.db.get_db_path", return_value=":memory:"),
            patch("codiff.diff.snapshot.load_from_db", return_value=_fake_snap()),
            patch("codiff.diff.snapshot.build_snapshot_incremental", return_value=_fake_snap()),
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
            patch("codiff.diff.snapshot.build_snapshot_incremental", return_value=_fake_snap()),
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
            patch("codiff.diff.snapshot.build_snapshot_incremental", return_value=_fake_snap()),
            patch("codiff.diff.differ.diff_snapshots", return_value=MagicMock()),
            patch("codiff.diff.analysis.analyze", return_value=result),
            patch("codiff.export.render_terminal"),
        ):
            _run_diff(str(tmp_path), "HEAD", include_deleted=False)
        assert result.removed == []
