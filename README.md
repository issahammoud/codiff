# codiff

A structural call-graph diff tool for Python codebases, built to work with coding agents. Instead of a line diff, it shows what changed at the function level — which functions were added and where they hook into existing code, which were modified, which were removed — organized by file and class, with color-coded call chains.

## How it works

codiff snapshots the Python call graph at two states (e.g. HEAD vs working tree), computes the node and edge delta, and derives structural facts: added/modified/removed functions, signature changes, orphaned functions, and high fan-in edits. Every fact is computed deterministically from the resolved call graph — no LLM, no embeddings, fully offline.

## Requirements

- Python 3.11+
- Git

## Installation

```bash
pip install codiff          # CLI only
pip install "codiff[mcp]"   # CLI + MCP server for coding agents
```

## Usage

### CLI

```bash
codiff diff                        # diff HEAD vs working tree
codiff diff --base main            # diff a specific base ref
codiff diff --repo /path/to/repo   # diff a different repo
```

### MCP integration (Claude Code)

Run once in any Python project you want to use codiff with:

```bash
codiff init --agent claude
```

This writes `.mcp.json` into the project root, registering the `codiff-mcp` server. Restart Claude Code — the `codiff_diff` tool is then available to the agent.

The agent calls `codiff_diff` automatically before committing. The full colored output renders directly in your terminal, identical to `codiff diff`.

## Reading the output

The output is grouped into **Source** and **Tests**, each with Added, Modified, and Removed tables.

**Added — `← Caller / → Callee` column**

| Value | Meaning |
|---|---|
| `entry point` | Nothing calls this function — new public surface |
| `← caller` | An existing function that now calls this new function (the hook-in point) |
| `→ callee` | A function this new function calls |

White names = existing code. Gray names = also new in this diff.

**Modified — `Changes` column**

| Value | Meaning |
|---|---|
| `body changed` | Implementation changed, same signature and calls |
| `+ callee` | Now calls callee |
| `- callee` | No longer calls callee |
| `was (...) → now (...)` | Signature changed |

**Chain colors**

Functions that form a connected call chain share a color throughout the output — in both the Function column and every Caller/Callee reference. All cyan names belong to one chain, all magenta to another. Gray = pre-existing code. White = new but isolated.

**Ordering**

Within each file/class block, entry points appear first, then their callees in depth-first order so each chain reads top-to-bottom.

**Issues** (bottom of output) flags: signature changes with callers not yet updated, newly orphaned functions, high fan-in edits.
