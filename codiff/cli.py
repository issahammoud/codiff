"""CLI entry point for codiff."""

import json
import logging
import os
import sys
from pathlib import Path

from codiff.utils.args import DEFAULT_WORKERS as _DEFAULT_WORKERS
from codiff.utils.args import build_parser
from codiff.utils.instructions import load as _load_instructions


def main():
    args = build_parser().parse_args()

    level = logging.DEBUG if (args.verbose >= 2 or getattr(args, "debug", False)) else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command == "index":
        from codiff.setup import setup_repository

        setup_repository(args.repo_path)

    elif args.command == "diff":
        _run_diff(
            repo_path=args.repo,
            base_ref=args.base,
            head_ref=args.head,
            include_tests=args.include_tests,
            include_deleted=args.include_deleted,
            fmt=args.format,
            max_workers=args.workers,
        )

    elif args.command == "init":
        _run_init(repo_path=args.repo, agent=args.agent)


# ---------------------------------------------------------------------------
# Init helpers — instructions loaded from utils/instructions.yaml
# ---------------------------------------------------------------------------

_instr = _load_instructions()
_INSTRUCTIONS_MARKER: str = _instr["marker"]
_INSTRUCTIONS_BODY: str = _instr["body"].rstrip("\n")
_INSTRUCTIONS_BLOCK: str = f"{_INSTRUCTIONS_MARKER}\n{_INSTRUCTIONS_BODY}\n<!-- /codiff -->"


def _write_mcp_config(
    path: Path,
    label: str,
    top_key: str,
    server_name: str,
    server_cfg: dict,
) -> None:
    """Write or merge an MCP server entry into a JSON config file."""
    config: dict = {}
    if path.exists():
        try:
            config = json.loads(path.read_text())
        except json.JSONDecodeError:
            config = {}
    servers: dict = config.setdefault(top_key, {})
    if server_name in servers:
        print(f"  ~ {label:<30} codiff MCP already registered, skipped")
    else:
        servers[server_name] = server_cfg
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"  + {label:<30} registered codiff-mcp server")


def _write_instructions(path: Path, label: str, block: str) -> None:
    """Create or append a codiff instructions block to a markdown file."""
    if path.exists():
        existing = path.read_text()
        if _INSTRUCTIONS_MARKER in existing:
            print(f"  ~ {label:<30} codiff instructions already present, skipped")
        else:
            path.write_text(existing.rstrip("\n") + "\n\n" + block + "\n")
            print(f"  + {label:<30} appended codiff instructions")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block + "\n")
        print(f"  + {label:<30} created with codiff instructions")


# ---------------------------------------------------------------------------
# Per-agent init functions
# ---------------------------------------------------------------------------


def _init_claude(repo_path: str) -> None:
    """Claude Code: .mcp.json + CLAUDE.md"""
    repo = Path(repo_path)
    _write_mcp_config(
        repo / ".mcp.json", ".mcp.json", "mcpServers", "codiff", {"command": "codiff-mcp"}
    )
    _write_instructions(repo / "CLAUDE.md", "CLAUDE.md", _INSTRUCTIONS_BLOCK)
    print("\n  Restart Claude Code to load the new MCP server.\n")


_CODEX_TOML_ENTRY = '[mcp_servers.codiff]\ncommand = "codiff-mcp"\n'


def _init_codex(repo_path: str) -> None:
    """OpenAI Codex CLI: .codex/config.toml + AGENTS.md"""
    import tomllib

    repo = Path(repo_path)
    config_path = repo / ".codex" / "config.toml"
    label = ".codex/config.toml"

    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                cfg = tomllib.load(f)
            if "codiff" in cfg.get("mcp_servers", {}):
                print(f"  ~ {label:<30} codiff MCP already registered, skipped")
            else:
                config_path.write_text(
                    config_path.read_text().rstrip("\n") + "\n\n" + _CODEX_TOML_ENTRY
                )
                print(f"  + {label:<30} registered codiff-mcp server")
        except Exception:
            config_path.write_text(
                config_path.read_text().rstrip("\n") + "\n\n" + _CODEX_TOML_ENTRY
            )
            print(f"  + {label:<30} registered codiff-mcp server")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_CODEX_TOML_ENTRY)
        print(f"  + {label:<30} registered codiff-mcp server")

    _write_instructions(repo / "AGENTS.md", "AGENTS.md", _INSTRUCTIONS_BLOCK)
    print()


def _init_gemini(repo_path: str) -> None:
    """Gemini CLI: global ~/.gemini/settings.json + GEMINI.md"""
    repo = Path(repo_path)
    _write_mcp_config(
        Path.home() / ".gemini" / "settings.json",
        "~/.gemini/settings.json",
        "mcpServers",
        "codiff",
        {"command": "codiff-mcp"},
    )
    _write_instructions(repo / "GEMINI.md", "GEMINI.md", _INSTRUCTIONS_BLOCK)
    print("\n  Restart Gemini CLI to load the new MCP server.\n")


_VIBE_SERVER_ENTRY: dict = {"name": "codiff", "transport": "stdio", "command": "codiff-mcp"}


def _init_vibe(repo_path: str) -> None:
    """Mistral Vibe: global ~/.vibe/config.toml with [[mcp_servers]] array entry.

    Vibe has no separate instructions file — MCP registration is sufficient.
    Project-level .vibe/config.toml exists but requires explicit directory trust;
    the global file is used by default and works without any extra setup.

    Vibe writes its config via tomli_w.dump() (the whole dict), so we must
    round-trip the same way — reading with tomllib and writing with tomli_w —
    to avoid TOML "cannot mutate immutable namespace" conflicts when the existing
    file uses inline-array syntax for mcp_servers.
    """
    import tomllib

    import tomli_w

    config_path = Path.home() / ".vibe" / "config.toml"
    label = "~/.vibe/config.toml"

    cfg: dict = {}
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            cfg = {}

    servers: list = cfg.get("mcp_servers", []) if isinstance(cfg.get("mcp_servers"), list) else []
    if any(isinstance(s, dict) and s.get("name") == "codiff" for s in servers):
        print(f"  ~ {label:<30} codiff MCP already registered, skipped")
        print()
        return

    cfg["mcp_servers"] = servers + [_VIBE_SERVER_ENTRY]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(cfg, f)
    print(f"  + {label:<30} registered codiff-mcp server")

    print("\n  Restart Vibe to load the new MCP server.\n")


_INIT_AGENTS = {
    "claude": _init_claude,
    "codex": _init_codex,
    "gemini": _init_gemini,
    "vibe": _init_vibe,
}


def _run_init(repo_path: str, agent: str) -> None:
    repo_path = os.path.abspath(repo_path)
    print(f"\nConfiguring codiff for {agent} in {repo_path}\n")
    _INIT_AGENTS[agent](repo_path)


def _run_diff(
    repo_path: str,
    base_ref: str,
    head_ref: str | None = None,
    include_tests: bool = False,
    include_deleted: bool = False,
    fmt: str = "terminal",
    max_workers: int = _DEFAULT_WORKERS,
) -> None:
    from codiff.diff.engine import compute_diff
    from codiff.export import render_json, render_mermaid, render_terminal

    result = compute_diff(
        repo_path,
        base_ref=base_ref,
        head_ref=head_ref,
        include_tests=include_tests,
        include_deleted=include_deleted,
        max_workers=max_workers,
    )
    head_label = head_ref or "working tree"
    if fmt == "json":
        print(render_json(result, base_ref=base_ref, head_ref=head_label))
    elif fmt == "mermaid":
        print(render_mermaid(result))
    else:
        render_terminal(result, base_ref=base_ref, head_ref=head_label, include_tests=include_tests)


if __name__ == "__main__":
    main()
