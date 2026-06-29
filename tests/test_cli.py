import json
import sys
from unittest.mock import MagicMock, patch

from codiff.cli import (
    _init_claude,
    _init_codex,
    _init_gemini,
    _init_vibe,
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


class TestInitGemini:
    def _setup(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        return fake_home, repo

    def test_creates_settings_json(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_gemini(str(repo))
        config = json.loads((fake_home / ".gemini/settings.json").read_text())
        assert config["mcpServers"]["codiff"] == {"command": "codiff-mcp"}

    def test_skips_if_already_registered(self, tmp_path, monkeypatch, capsys):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        path = fake_home / ".gemini/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"mcpServers": {"codiff": {"command": "codiff-mcp"}}}))
        _init_gemini(str(repo))
        assert "skipped" in capsys.readouterr().out

    def test_merges_with_existing_servers(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        path = fake_home / ".gemini/settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
        _init_gemini(str(repo))
        config = json.loads(path.read_text())
        assert "codiff" in config["mcpServers"]
        assert "other" in config["mcpServers"]

    def test_creates_gemini_md(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_gemini(str(repo))
        content = (repo / "GEMINI.md").read_text()
        assert "codiff_diff" in content
        assert "mermaid" in content

    def test_appends_to_existing_gemini_md(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        (repo / "GEMINI.md").write_text("# Existing\n\nOther instructions.\n")
        _init_gemini(str(repo))
        content = (repo / "GEMINI.md").read_text()
        assert "Existing" in content
        assert "codiff_diff" in content

    def test_skips_gemini_md_if_marker_present(self, tmp_path, monkeypatch, capsys):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        (repo / "GEMINI.md").write_text("<!-- codiff -->\nAlready here.\n")
        _init_gemini(str(repo))
        assert "skipped" in capsys.readouterr().out


class TestInitVibe:
    def _setup(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        return fake_home

    def _load(self, path):
        import tomllib

        with open(path, "rb") as f:
            return tomllib.load(f)

    def test_creates_global_config_toml(self, tmp_path, monkeypatch):
        fake_home = self._setup(tmp_path, monkeypatch)
        _init_vibe(str(tmp_path))
        cfg = self._load(fake_home / ".vibe/config.toml")
        assert len(cfg["mcp_servers"]) == 1
        s = cfg["mcp_servers"][0]
        assert s["name"] == "codiff"
        assert s["transport"] == "stdio"
        assert s["command"] == "codiff-mcp"

    def test_skips_if_already_registered(self, tmp_path, monkeypatch, capsys):
        import tomli_w

        fake_home = self._setup(tmp_path, monkeypatch)
        config = fake_home / ".vibe/config.toml"
        config.parent.mkdir(parents=True)
        existing = {
            "mcp_servers": [{"name": "codiff", "transport": "stdio", "command": "codiff-mcp"}]
        }
        with open(config, "wb") as f:
            tomli_w.dump(existing, f)
        _init_vibe(str(tmp_path))
        assert "skipped" in capsys.readouterr().out
        cfg = self._load(config)
        assert len(cfg["mcp_servers"]) == 1

    def test_merges_with_existing_config(self, tmp_path, monkeypatch):
        import tomli_w

        fake_home = self._setup(tmp_path, monkeypatch)
        config = fake_home / ".vibe/config.toml"
        config.parent.mkdir(parents=True)
        existing = {"model": {"name": "devstral"}}
        with open(config, "wb") as f:
            tomli_w.dump(existing, f)
        _init_vibe(str(tmp_path))
        cfg = self._load(config)
        assert cfg["model"]["name"] == "devstral"
        assert any(s["name"] == "codiff" for s in cfg["mcp_servers"])

    def test_handles_corrupt_toml(self, tmp_path, monkeypatch):
        fake_home = self._setup(tmp_path, monkeypatch)
        config = fake_home / ".vibe/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("NOT VALID TOML }{{\n")
        _init_vibe(str(tmp_path))
        cfg = self._load(config)
        assert any(s["name"] == "codiff" for s in cfg["mcp_servers"])


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
        _init_codex(str(repo))
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

    def test_gemini_settings_json(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_gemini(str(repo))
        cfg = json.loads((fake_home / ".gemini/settings.json").read_text())
        assert cfg["mcpServers"]["codiff"] == {"command": "codiff-mcp"}

    def test_gemini_md(self, tmp_path, monkeypatch):
        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_gemini(str(repo))
        text = (repo / "GEMINI.md").read_text()
        assert "<!-- codiff -->" in text
        assert "codiff_diff" in text
        assert 'format="mermaid"' in text

    def test_vibe_config_toml(self, tmp_path, monkeypatch):
        import tomllib

        fake_home, repo = self._setup(tmp_path, monkeypatch)
        _init_vibe(str(repo))
        with open(fake_home / ".vibe/config.toml", "rb") as f:
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
            repo / "AGENTS.md",
            repo / "GEMINI.md",
            fake_home / ".gemini/settings.json",
            fake_home / ".vibe/config.toml",
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
            ("codex", "AGENTS.md", tmp_path),
            ("gemini", ".gemini/settings.json", fake_home),
            ("vibe", ".vibe/config.toml", fake_home),
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

    def test_dispatches_to_init_codex(self, tmp_path):
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "codex", "--repo", str(tmp_path)]
        ):
            main()
        assert (tmp_path / "AGENTS.md").exists()

    def test_dispatches_to_init_gemini(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "gemini", "--repo", str(tmp_path)]
        ):
            main()
        assert (fake_home / ".gemini/settings.json").exists()

    def test_dispatches_to_init_vibe(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        with patch.object(
            sys, "argv", ["codiff", "init", "--agent", "vibe", "--repo", str(tmp_path)]
        ):
            main()
        assert (fake_home / ".vibe/config.toml").exists()


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
