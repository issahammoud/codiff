# codiff

A structural call-graph diff tool for multi-language codebases, built to work with coding agents. Instead of a line diff, it shows what changed at the function level, which functions were added and where they hook into existing code, which were modified, which were removed, with call relationships mapped across files.

Here's the output of `codiff diff --base main --format mermaid` run on codiff's own codebase:

```mermaid
%%{init: {'layout': 'elk', 'elk': {'direction': 'RIGHT'}, 'maxTextSize': 999999, 'theme': 'base', 'themeVariables': {'background': '#ffffff', 'clusterBkg': '#f8fafc', 'clusterBorder': '#94a3b8', 'primaryColor': '#f8fafc', 'primaryBorderColor': '#94a3b8', 'primaryTextColor': '#1e293b', 'lineColor': '#64748b', 'fontSize': '13px', 'fontFamily': 'ui-monospace, SFMono-Regular, Menlo, monospace'}}}%%
classDiagram
    direction LR

    class n5["codiff/cli.py"] {
        ~ _run_diff()  sig
        ~ main()
    }

    class n7["codiff/diff/indexer.py"] {
        + _incremental_update_db()
        ~ _full_index()  sig
        ~ ensure_indexed()  sig
    }

    class n3["PythonParser"] {
        <<codiff/languages/python/parser.py>>
        ~ build_package_exports()  calls +1−1
    }

    class n8["codiff/diff/snapshot.py"] {
        + _chunk_to_node_info()
        + _git_changed_files()
        + _parse_and_expand_stale()
        + build_snapshot_incremental()
        ~ build_from_path()  sig
        ~ build_from_ref()  sig
        ~ load_from_db()  calls +1−1
    }
    class n0["_ClassStub"] {
        <<codiff/diff/snapshot.py>>
        + __init__()
    }
    class n1["_NodeStub"] {
        <<codiff/diff/snapshot.py>>
        + __init__()
    }

    class n2["LanguageParser"] {
        <<codiff/languages/parser.py>>
        ~ build_modules_dict()  calls +1−1
    }

    class n6["codiff/db/operations.py"] {
        + _insert_call_edges()
        + _insert_classes()
        + _insert_functions()
        + _session()
        + get_indexed_sha()
        + load_snapshot()
        + update_sha()
        + write_full_snapshot()
        + write_incremental()
    }

    style n5 fill:#fefce8,color:#854d0e,stroke:#f59e0b,stroke-width:3px
    style n7 fill:#fefce8,color:#854d0e,stroke:#f59e0b,stroke-width:3px
    style n3 fill:#fefce8,color:#854d0e,stroke:#f59e0b,stroke-width:3px
    style n8 fill:#fefce8,color:#854d0e,stroke:#f59e0b,stroke-width:3px
    style n0 fill:#f0fdf4,color:#166534,stroke:#22c55e,stroke-width:3px
    style n1 fill:#f0fdf4,color:#166534,stroke:#22c55e,stroke-width:3px
    style n2 fill:#fefce8,color:#854d0e,stroke:#f59e0b,stroke-width:3px
    style n6 fill:#f0fdf4,color:#166534,stroke:#22c55e,stroke-width:3px

    %% Relationships
    n3 --|> n2
    n5 --> n8 : calls
    n7 --> n0 : calls
    n7 --> n1 : calls
    n7 --> n6 : calls
    n7 --> n8 : calls
    n8 --> n0 : calls
    n8 --> n1 : calls
    n8 --> n6 : calls
```

Each box is a file or class. **Green** = only additions, **yellow** = at least one modification. Inside each box: `+` added function, `~` modified. Annotations: `sig` = signature changed, `calls +N−N` = now calls N more / N fewer functions. Arrows show which files call into which.

## Supported languages

| Language | Extensions |
|---|---|
| Python | `.py` |
| TypeScript | `.ts`, `.tsx` |

## How it works

codiff maintains a SQLite call-graph index (`.codiff.db`) at the repo root. On first run it does a full parse; on subsequent runs it re-parses only the files changed since the last indexed commit, then detects stale callers (functions whose callees were renamed or deleted) and re-parses those too. Both the base and head snapshots are built incrementally from this index, so diffs stay fast even on large codebases.

The graph delta — added, modified, removed functions — is computed deterministically from the resolved call graph. No LLM, no embeddings, fully offline.

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

Run once in any project you want to use codiff with:

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

Both diagrams use color-coded class boxes:
- **Green** — only additions in this file/class
- **Yellow** — at least one modification
- **Red** — only deletions (requires `--include-deleted`)

Inside each box, functions are listed with a prefix and optional annotation:

| Prefix | Meaning |
|---|---|
| `+` | Function was added |
| `~` | Function was modified |
| `-` | Function was removed |

| Annotation | Meaning |
|---|---|
| `sig` | Parameters or return type changed |
| `calls +N−N` | Call list changed — N new callees, N dropped |

When a class appears inside a file box, its file path is shown as a `«stereotype»` subtitle below the class name.
