# codiff — Claude Code instructions

## What this project is
A CLI tool that computes and displays the structural delta of a Python codebase
between two states (e.g. HEAD vs working tree). It shows what an AI coding agent
rewired in the call graph — blast radius, new/removed edges, dead code, boundary
changes — not a line diff, not an LLM summary. Every fact is derived
deterministically from a resolved call graph.

## Architecture
- **Core engine** (inherited from codebot): Tree-sitter AST parser, call
  resolver, incremental SQLite index, file watcher. Do not rewrite these.
- **New layer — graph diff**: snapshot two graph states, compute node/edge set
  difference, derive structural facts. Keep this pure and separate from rendering.
- **CLI + rendering**: rich-based terminal output. Textual/TUI is out of scope
  for now.

## Hard rules
- No LLM calls on the diff code path. No embeddings. Fully deterministic.
- Fully local and offline. No API key required to run `codiff`.
- Do not touch the core parser or graph builder — add new modules, don't fork.
- Keep graph-diff logic decoupled from rendering (separate modules, unit-testable
  independently).
- Python only. No JS, no web frontend, no new heavyweight dependencies without
  discussion.

## Code style
- Type hints everywhere.
- Descriptive names — no single-letter variables outside list comprehensions.
- New modules get a docstring explaining their responsibility.
- No commented-out code in commits.

## Testing
- Unit tests use synthetic before/after graph fixtures, not real repos.
- Every structural fact the diff can surface must have a test: added/removed node,
  new/removed edge, node inserted into a chain, dead-on-arrival function, newly
  orphaned function, new cross-module edge, signature change with un-updated caller.
- Run tests with `pytest` before proposing a PR.

## Out of scope (do not build unless explicitly asked)
- TUI, visual diagrams, sixel/image rendering, dependency matrix view.
- Team/PR-review features, GitHub App, CI integration.
- Multi-language support (Python only for now).
- LLM-generated summaries or explanations on the diff path.

## Key commands
- `codiff diff [--base <git-ref>]` — main command, defaults to HEAD as base.
- Add new subcommands only after discussion.

## When unsure
Propose before implementing. Especially for: changes to the core engine,
new dependencies, new subcommands, and anything that adds a network call.
