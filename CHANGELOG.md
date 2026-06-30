# Changelog

## [0.1.0] - 2026-06-30

First public release.

### Core

- Structural call-graph diff: computes added, modified, and removed functions between any two git refs by diffing resolved call graphs, not line diffs
- SQLite-backed index (`.codiff.db`) for fast incremental re-parsing — only changed files are re-parsed on subsequent runs; stale callers (functions whose callees were renamed or deleted) are detected and re-parsed automatically
- Multiprocessing parser and resolver for large codebases

### Language support

- Python — functions, methods, nested functions, class hierarchies
- TypeScript / TSX — functions, arrow functions, class methods, JSX calls, Zustand store methods

### Output formats

- **Terminal** — UML-style file boxes with chain-consistent colors, intra-file call depth, cross-file arrows, class relationship annotations
- **Mermaid** — two `classDiagram` blocks (connected modules with ELK layout + isolated modules grouped by directory); renders natively in GitHub PR descriptions
- **JSON** — structured output for editor integrations

### Agent integration

- MCP server (`codiff-mcp`) exposing a single `codiff_diff` tool
- `codiff init --agent <agent>` writes MCP config and project instructions for Claude Code, OpenAI Codex CLI, Gemini CLI, and Mistral Vibe
