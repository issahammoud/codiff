# codiff

A structural call-graph diff tool for Python codebases, built to work with coding agents. Instead of a line diff, it shows what changed at the function level — which functions were added and where they hook into existing code, which were modified, which were removed — with color-coded call chains consistent across files.

## How it works

codiff snapshots the Python call graph at two states (e.g. HEAD vs working tree), computes the node and edge delta, and derives structural facts: added/modified/removed functions. Every fact is computed deterministically from the resolved call graph — no LLM, no embeddings, fully offline.

## Requirements

- Python 3.11+
- Git

## Installation

```bash
pip install git+https://github.com/issahammoud/codiff.git
```

## Usage

### CLI

```bash
codiff diff                          # diff HEAD vs working tree (terminal output)
codiff diff --format mermaid         # output a Mermaid class diagram
codiff diff --format json            # output structured JSON (for editor integrations)
codiff diff --base main              # diff a specific base ref
codiff diff --head <ref>             # diff two git refs directly
codiff diff --repo /path/to/repo     # diff a different repo
codiff diff --include-tests          # include test functions (hidden by default)
codiff diff --include-deleted        # include deleted functions (hidden by default)
codiff diff --workers 8              # set parallel worker count (default: cpu_count // 2)
codiff diff --debug                  # print timing breakdown for each processing step
```

### Output formats

| Format | Description |
|---|---|
| `terminal` | Colored terminal output with UML-style boxes (default) |
| `mermaid` | Two Mermaid `classDiagram` blocks — paste into any Markdown file or PR description |
| `json` | Structured JSON — consumed by editor integrations (e.g. the VS Code extension) |

### MCP integration (Claude Code)

Run once in any Python project you want to use codiff with:

```bash
codiff init --agent claude
```

This writes `.mcp.json` into the project root, registering the `codiff-mcp` server. Restart Claude Code — the `codiff_diff` tool is then available to the agent.

When creating a pull request, call `codiff_diff(base_ref="main", head_ref="HEAD", format="mermaid")` to get a Mermaid diagram to embed in the PR description. GitHub renders it natively — no plugin needed.

## Reading the terminal output

The output shows one box per changed file. Boxes are laid out side by side when they fit the terminal width, with labeled arrows between adjacent connected boxes.

### Inside each file box

Methods belonging to the same class are grouped into a **dashed sub-box** (╭╌╌╌ `ClassName` ╌╌╌╮). Standalone functions appear directly in the file box. Deleted functions (only shown with `--include-deleted`) are collected into a red **╭╌╌╌ deleted ╌╌╌╮** sub-box.

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

### Intra-file class relationships

When two changed classes in the same file are related, each class box shows a dim annotation before its methods:

```
╭╌╌╌ PageAwarePreChunker ╌╌╌╮
│ calls  PageAwarePreChunkBuilder  │
│ ──────────────                   │
│ + __init__                       │
╰╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╯
```

Relationship types: `calls` (method calls to another class) and `inherits` (superclass relationship detected from class definitions).

### Colors

Functions that form a connected call chain share a color across the entire output — across boxes, across files. All magenta names belong to one chain, all cyan to another.

- **Chain color** on the function name — part of a call chain
- **White** on the function name — added/modified but not connected to any chain
- **`~` yellow** — always marks a modified function regardless of chain membership

### Arrows between file boxes

Labeled arrows appear between adjacent file boxes when there is a cross-file relationship:

| Label | Meaning |
|---|---|
| `calls ────▶` | A function in the left file calls a function in the right file |
| `inherits ────▶` | A class in the left file inherits from a class in the right file |

## Reading the Mermaid output

The Mermaid format produces **two diagrams**:

1. **Connected modules** — files that have call or inheritance relationships with other changed files. Rendered with ELK layout left-to-right, with `calls` and `inherits` arrows between class boxes.

2. **Isolated modules** — files with no cross-file relationships (e.g. migration files, config). Grouped by their top two folder levels into namespace clusters, rendered with Dagre left-to-right.

Both diagrams use tinted color-coded class boxes:
- **Green** — only additions
- **Yellow** — modifications
- **Red** — only deletions (requires `--include-deleted`)
- **Tinted chain color** — all classes in the same connected call chain share a color

The class box title shows the file path as a `«stereotype»` subtitle below the class name.
