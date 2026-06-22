# codiff

A structural call-graph diff tool for Python codebases, built to work with coding agents. Instead of a line diff, it shows what changed at the function level — which functions were added and where they hook into existing code, which were modified, which were removed — with color-coded call chains consistent across files.

## How it works

codiff snapshots the Python call graph at two states (e.g. HEAD vs working tree), computes the node and edge delta, and derives structural facts: added/modified/removed functions. Every fact is computed deterministically from the resolved call graph — no LLM, no embeddings, fully offline.

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
codiff diff                          # diff HEAD vs working tree (terminal output)
codiff diff --format mermaid         # output a Mermaid UML class diagram
codiff diff --format json            # output structured JSON (for editor integrations)
codiff diff --base main              # diff a specific base ref
codiff diff --head <ref>             # diff two git refs directly
codiff diff --repo /path/to/repo     # diff a different repo
codiff diff --include-tests          # include test functions (hidden by default)
```

### Output formats

| Format | Description |
|---|---|
| `terminal` | Colored terminal output with call-chain boxes (default) |
| `mermaid` | Mermaid `classDiagram` — paste into any Markdown file or PR description |
| `json` | Structured JSON — consumed by editor integrations (e.g. the VS Code extension) |

### MCP integration (Claude Code)

Run once in any Python project you want to use codiff with:

```bash
codiff init --agent claude
```

This writes `.mcp.json` into the project root, registering the `codiff-mcp` server. Restart Claude Code — the `codiff_diff` tool is then available to the agent.

The agent calls `codiff_diff` at the end of every response that modifies files. The full colored output renders directly in your terminal, identical to `codiff diff`.

## Reading the terminal output

The output shows one box per changed file. Boxes are laid out side by side when they fit the terminal width, with `────▶` arrows between adjacent connected boxes.

### Inside each box

Functions are listed with an indicator and an annotation:

| Indicator | Meaning |
|---|---|
| `+` green | Function was added |
| `~` yellow | Function was modified |
| `-` red | Function was removed |

| Annotation | Meaning |
|---|---|
| `entry point` | Nothing calls this new function — new public surface |
| `sig changed` | Parameters or return type changed |
| `calls changed` | The function now calls different things |
| `body changed` | Pure implementation change |

For added functions, `→` arrows show intra-file call relationships — a function indented under another calls it.

### Colors

Functions that form a connected call chain share a color across the entire output — across boxes, across files. All magenta names belong to one chain, all cyan to another.

- **Chain color** on the function name — part of a call chain
- **White** on the function name — added/modified but not connected to any chain
- **`~` yellow** — always marks a modified function regardless of chain membership

### Arrows between boxes

An arrow `────▶` appears between two adjacent boxes when a function in the left box calls a function in the right box. The arrow color matches the callee's chain color.
