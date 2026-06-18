"""Render an AnalysisResult to the terminal using Rich.

Groups changes into source vs. test functions. Within each group, renders
compact tables for Added, Modified, and Removed. Issues appear at the end.

Color scheme
------------
- New functions that share a call-graph chain are assigned a distinct color.
  The same color is used consistently in both the Function column and every
  Caller / Callee reference, so you can visually trace a chain at a glance.
- New functions that belong to no chain (isolated additions) stay white.
- Existing / pre-diff functions are rendered dim (gray).
"""

from collections import defaultdict
from itertools import groupby
from pathlib import Path
from typing import Any, Callable, Optional

from rich.box import Box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from codiff.schema.diff import (
    AddedFunctionInfo,
    AnalysisResult,
    ModifiedFunctionInfo,
    RemovedFunctionInfo,
)
from codiff.utils.terminal import detect_width

console = Console(force_terminal=True)

_CALLER_MAX = 5
_CALLEE_MAX = 6

# Solid underline under column headers; dashed line at add_section() group breaks.
_BOX = Box(
    "    \n"  # top
    "    \n"  # head
    " ── \n"  # head_row  (solid ─ under header)
    "    \n"  # mid       (unused — no line between every row)
    " ╌╌ \n"  # row       (dashed ╌ at section / group breaks)
    "    \n"  # foot_row
    "    \n"  # foot
    "    \n",  # bottom
    ascii=False,
)

# Distinct colors for call-chain groups. Green/red/yellow are reserved for
# the +/~/- indicators, cyan for the header rule.
_CHAIN_COLORS = [
    "magenta",
    "cornflower_blue",
    "orange3",
    "medium_purple1",
    "spring_green2",
    "hot_pink",
    "sky_blue1",
    "gold3",
]


# ---------------------------------------------------------------------------
# Color map: connected components of new functions
# ---------------------------------------------------------------------------


def _build_color_map(result: AnalysisResult) -> dict[str, str]:
    """Assign a chain color to every function that belongs to a connected call chain.

    The graph includes both added and modified functions so that a modified
    function calling an added function shares the same color across modules.
    Single-node components get no color (white for added, yellow for modified).
    """
    all_ids = {fn.function_id for fn in result.added} | {fn.function_id for fn in result.modified}

    adj: dict[str, set[str]] = defaultdict(set)

    # Added functions: full adjacency via new_calls / new_callers
    for fn in result.added:
        for callee in fn.new_calls:
            adj[fn.function_id].add(callee)
            adj[callee].add(fn.function_id)
        for caller in fn.new_callers:
            adj[fn.function_id].add(caller)
            adj[caller].add(fn.function_id)

    # Modified functions: connect to added functions they now call
    for mod_fn in result.modified:
        for callee_id in mod_fn.calls_added_new:
            adj[mod_fn.function_id].add(callee_id)
            adj[callee_id].add(mod_fn.function_id)

    visited: set[str] = set()
    components: list[list[str]] = []
    for fid in sorted(all_ids):
        if fid in visited:
            continue
        component: list[str] = []
        queue = [fid]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            for neighbor in sorted(adj.get(node, set())):
                if neighbor in all_ids and neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)

    components.sort(key=lambda c: (-len(c), c[0]))

    color_map: dict[str, str] = {}
    color_idx = 0
    for component in components:
        if len(component) >= 2:
            color = _CHAIN_COLORS[color_idx % len(_CHAIN_COLORS)]
            color_idx += 1
            for fid in component:
                color_map[fid] = color

    return color_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lbl(function_id: str) -> str:
    """Display label: last 3 dotted parts, or the full id if short."""
    parts = function_id.split(".")
    return ".".join(parts[-3:]) if len(parts) > 3 else function_id


def _name(function_id: str) -> str:
    """Compact label: just the bare function name (last dotted segment)."""
    return function_id.split(".")[-1]


def _display_name(fn: object) -> str:
    """ClassName.method for class methods, outer.inner for nested functions, else just name."""
    class_name = getattr(fn, "class_name", None)
    function_id = getattr(fn, "function_id", "")
    name = function_id.split(".")[-1]

    if class_name:
        return f"{class_name}.{name}"

    # Detect nested: strip the module prefix (derived from file_path) and check
    # if what remains is "outer.inner" rather than a bare function name.
    file_path = getattr(fn, "file_path", "")
    module = file_path.replace("/", ".").replace(".py", "")
    if function_id.startswith(module + "."):
        remaining = function_id[len(module) + 1 :]  # e.g. "build_from_path.walk"
        parts = remaining.split(".")
        if len(parts) > 1:
            return ".".join(parts[-2:])

    return name


def _fn_label(fid: str, *, is_new: bool, color_map: dict[str, str]) -> str:
    """Render a function name with the appropriate color.

    - New + in a chain  → chain color
    - New + isolated    → white (no markup)
    - Existing          → dim gray
    """
    if not is_new:
        return f"[dim]{_name(fid)}[/dim]"
    color = color_map.get(fid)
    if color:
        return f"[{color}]{_name(fid)}[/{color}]"
    return _name(fid)


def _is_test(file_path: str) -> bool:
    return any(part.startswith("test") for part in Path(file_path).parts)


def _partition(items: list, *, test: bool) -> list:
    return [i for i in items if _is_test(i.file_path) == test]


def _truncate(items: list[str], max_items: int) -> tuple[list[str], int]:
    if len(items) <= max_items:
        return items, 0
    return items[:max_items], len(items) - max_items


def _fmt_sig(params: list[dict], return_type: Optional[str]) -> str:
    parts: list[str] = []
    for p in params:
        part = p.get("name") or "?"
        if p.get("type"):
            part += f": {p['type']}"
        if p.get("value") is not None:
            part += f" = {p['value']}"
        parts.append(part)
    sig = "(" + ", ".join(parts) + ")"
    if return_type:
        sig += f" → {return_type}"
    return sig


def _make_table(*columns: tuple[str, dict]) -> Table:
    t = Table(
        box=_BOX,
        show_header=True,
        header_style="dim",
        border_style="grey50",
        padding=(0, 1),
        show_edge=False,
    )
    for name, kwargs in columns:
        t.add_column(name, **kwargs)
    return t


def _group_key(fn: object) -> tuple[str, str]:
    """Sort/group key: (file_path, class_name) for section breaks."""
    return (fn.file_path, fn.class_name or "")  # type: ignore[attr-defined]


def _order_group(
    fns: list[AddedFunctionInfo],
    color_map: dict[str, str] | None = None,
) -> list[AddedFunctionInfo]:
    """Order functions within a (file, class) group for readability.

    Entry points (nothing calls them within the group) come first.
    DFS follows new_calls within the group so callee rows flow directly
    after their caller — but only across same-color connections, so chains
    stay visually consistent with the color coding.
    """
    if len(fns) <= 1:
        return fns

    by_id = {fn.function_id: fn for fn in fns}
    g_ids = set(by_id)
    cm = color_map or {}

    starts = [fn for fn in fns if fn.is_entry_point or not (set(fn.new_callers) & g_ids)]
    starts.sort(key=lambda fn: (0 if fn.is_entry_point else 1, _name(fn.function_id)))

    ordered: list[AddedFunctionInfo] = []
    visited: set[str] = set()
    for start in starts:
        stack = [start.function_id]
        while stack:
            fid = stack.pop()
            if fid in visited:
                continue
            visited.add(fid)
            ordered.append(by_id[fid])
            my_color = cm.get(fid)
            for callee_id in reversed(sorted(by_id[fid].new_calls)):
                if callee_id not in g_ids or callee_id in visited:
                    continue
                # Only follow same-color edges so chains stay contiguous
                if my_color is not None and cm.get(callee_id) == my_color:
                    stack.append(callee_id)

    # Append anything not yet reached (cross-chain callees, isolated nodes)
    for fn in sorted(fns, key=lambda fn: _name(fn.function_id)):
        if fn.function_id not in visited:
            ordered.append(fn)

    return ordered


def _add_grouped_rows(
    t: Table,
    fns: list[Any],
    has_class: bool,
    indicator: str,
    extra_cells: Callable[[Any], list[str]],
) -> None:
    """Add rows to *t* grouped by (file, class).

    File, class, and indicator are printed only once per group at the center
    row. A dashed section separator is inserted between groups.
    """
    groups = [(key, list(grp)) for key, grp in groupby(fns, key=_group_key)]
    for g_idx, ((file_path, class_name), group_fns) in enumerate(groups):
        if g_idx > 0:
            t.add_section()
        mid = (len(group_fns) - 1) // 2
        for i, fn in enumerate(group_fns):
            show = i == mid
            row = [indicator if show else "", file_path if show else ""]
            if has_class:
                row.append((class_name or "[dim]—[/dim]") if show else "")
            row.extend(extra_cells(fn))
            t.add_row(*row)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _render_summary(
    added: list,
    modified: list,
    removed: list,
) -> None:
    total = len(added) + len(modified) + len(removed)
    if total == 0:
        console.print("\n  [dim]No structural changes detected.[/dim]")
        return
    parts: list[str] = []
    if added:
        parts.append(f"[green]+{len(added)} added[/green]")
    if modified:
        parts.append(f"[yellow]~{len(modified)} modified[/yellow]")
    if removed:
        parts.append(f"[red]-{len(removed)} removed[/red]")
    modules = {fn.file_path for fn in added + modified + removed}
    n = len(modules)
    parts.append(f"[dim]{n} module{'s' if n != 1 else ''}[/dim]")
    console.print("  " + "  ·  ".join(parts))


# ---------------------------------------------------------------------------
# Group renderer
# ---------------------------------------------------------------------------


def render(
    result: AnalysisResult,
    base_ref: str = "HEAD",
    head_ref: str = "working tree",
    include_tests: bool = False,
) -> None:
    """Print the full structural diff report."""
    term_w = detect_width()
    console.print()
    console.rule(
        f"[bold cyan]codiff[/bold cyan]  [dim]{base_ref}[/dim] [dim]→[/dim] {head_ref}",
        style="cyan",
    )

    src_added = _partition(result.added, test=False)
    src_modified = _partition(result.modified, test=False)
    src_removed = _partition(result.removed, test=False)

    if include_tests:
        tst_added = _partition(result.added, test=True)
        tst_modified = _partition(result.modified, test=True)
        tst_removed = _partition(result.removed, test=True)
    else:
        tst_added = tst_modified = tst_removed = []

    visible_added = src_added + tst_added
    visible_modified = src_modified + tst_modified
    visible_removed = src_removed + tst_removed

    _render_summary(visible_added, visible_modified, visible_removed)

    color_map = _build_color_map(result)

    _render_group("Source", src_added, src_modified, src_removed, color_map, term_w)
    if include_tests:
        _render_group("Tests", tst_added, tst_modified, tst_removed, color_map, term_w)
    console.print()


def _render_group(
    title: str,
    added: list[AddedFunctionInfo],
    modified: list[ModifiedFunctionInfo],
    removed: list[RemovedFunctionInfo],
    color_map: dict[str, str],
    term_w: int,
) -> None:
    if not added and not modified and not removed:
        return
    console.print()
    console.rule(f"[bold]{title}[/bold]", style="dim blue")
    console.print()
    _render_uml(added, modified, removed, color_map, term_w)


# ---------------------------------------------------------------------------
# UML layout
# ---------------------------------------------------------------------------


def _render_uml(
    added: list[AddedFunctionInfo],
    modified: list[ModifiedFunctionInfo],
    removed: list[RemovedFunctionInfo],
    color_map: dict[str, str],
    term_w: int,
) -> None:
    """Render file boxes side-by-side with arrows for cross-file relationships."""
    # Group changes by file
    files: dict[str, dict] = {}
    for a_fn in added:
        files.setdefault(a_fn.file_path, {"added": [], "modified": [], "removed": []})
        files[a_fn.file_path]["added"].append(a_fn)
    for m_fn in modified:
        files.setdefault(m_fn.file_path, {"added": [], "modified": [], "removed": []})
        files[m_fn.file_path]["modified"].append(m_fn)
    for r_fn in removed:
        files.setdefault(r_fn.file_path, {"added": [], "modified": [], "removed": []})
        files[r_fn.file_path]["removed"].append(r_fn)

    # Map function_id → file_path for changed functions only
    fn_to_file: dict[str, str] = {}
    for a_fn in added:
        fn_to_file[a_fn.function_id] = a_fn.file_path
    for m_fn in modified:
        fn_to_file[m_fn.function_id] = m_fn.file_path
    for r_fn in removed:
        fn_to_file[r_fn.function_id] = r_fn.file_path

    # Detect cross-file call relationships between changed files.
    # (from_file, to_file) → list of callee function_ids (used for chain color)
    cross: dict[tuple[str, str], list[str]] = defaultdict(list)

    for a_fn in added:
        for caller_id in a_fn.existing_callers + a_fn.new_callers:
            caller_file = fn_to_file.get(caller_id)
            if caller_file and caller_file != a_fn.file_path:
                cross[(caller_file, a_fn.file_path)].append(a_fn.function_id)

    for m_fn in modified:
        for callee_id in m_fn.calls_added_new + m_fn.calls_added_existing:
            callee_file = fn_to_file.get(callee_id)
            if callee_file and callee_file != m_fn.file_path:
                cross[(m_fn.file_path, callee_file)].append(callee_id)

    # Sort files: upstream (more outgoing) first, downstream last
    out_count: dict[str, int] = defaultdict(int)
    in_count: dict[str, int] = defaultdict(int)
    for from_f, to_f in cross:
        out_count[from_f] += 1
        in_count[to_f] += 1
    # Sort files so connected pairs are adjacent, maximising chance of arrows.
    placed: set[str] = set()
    all_fps: list[str] = []
    for from_fp, to_fp in sorted(cross):
        for fp in (from_fp, to_fp):
            if fp in files and fp not in placed:
                all_fps.append(fp)
                placed.add(fp)
    for fp in sorted(files):
        if fp not in placed:
            all_fps.append(fp)

    panels = {fp: _build_file_panel(fp, files[fp], color_map) for fp in all_fps}
    widths = {fp: _panel_min_width(fp, files[fp], color_map) for fp in all_fps}

    # Greedy row packing — fit as many panels per row as the terminal allows.
    # Arrows show automatically when two adjacent same-row panels are connected.
    _ARROW_W = 14

    rows: list[list[str]] = []
    current_row: list[str] = []
    current_w = 0
    for fp in all_fps:
        slot_w = widths[fp] + 4 + (_ARROW_W if current_row else 0)
        if current_row and current_w + slot_w > term_w:
            rows.append(current_row)
            current_row = [fp]
            current_w = widths[fp] + 4
        else:
            current_row.append(fp)
            current_w += slot_w
    if current_row:
        rows.append(current_row)

    for row_fps in rows:
        grid = Table.grid(padding=(0, 2))
        for i, fp in enumerate(row_fps):
            grid.add_column(vertical="top", min_width=widths[fp])
            if i < len(row_fps) - 1:
                grid.add_column(vertical="top", width=10)
        row_cells: list = []
        for i, fp in enumerate(row_fps):
            row_cells.append(panels[fp])
            if i < len(row_fps) - 1:
                next_fp = row_fps[i + 1]
                fwd = cross.get((fp, next_fp), [])
                rev = cross.get((next_fp, fp), [])
                callee_ids = fwd + rev
                row_cells.append(
                    _arrow_cell(callee_ids, color_map, reverse=bool(rev and not fwd))
                    if callee_ids
                    else Text("")
                )
        grid.add_row(*row_cells)
        console.print(grid)


def _panel_min_width(file_path: str, funcs: dict, color_map: dict[str, str]) -> int:
    """Minimum panel width: widest row (indicator + indent + name + annotation) + borders."""
    depth: dict[str, int] = {}
    added_sorted: list[AddedFunctionInfo] = []
    fn_by_grp: dict[tuple[str, str], list[AddedFunctionInfo]] = defaultdict(list)
    for fn in funcs["added"]:
        fn_by_grp[_group_key(fn)].append(fn)
    for grp_key in _topo_order_groups(funcs["added"]):
        added_sorted.extend(_order_group(fn_by_grp[grp_key], color_map))
    for i, fn in enumerate(added_sorted):
        my_color = color_map.get(fn.function_id)
        parent_depth2: Optional[int] = None
        for j in range(i - 1, -1, -1):
            prev = added_sorted[j]
            if (
                prev.function_id in fn.new_callers
                and my_color is not None
                and color_map.get(prev.function_id) == my_color
            ):
                broken = any(
                    depth[added_sorted[k].function_id] == 0
                    and color_map.get(added_sorted[k].function_id) != my_color
                    for k in range(j + 1, i)
                )
                if not broken:
                    parent_depth2 = depth[prev.function_id]
                break
        depth[fn.function_id] = (parent_depth2 + 1) if parent_depth2 is not None else 0

    rows = []
    for fn in added_sorted:
        d = depth[fn.function_id]
        indent = "  " * d + ("→ " if d > 0 else "+ ")
        label = indent + _display_name(fn)
        if fn.is_entry_point:
            label += "  entry point"
        rows.append(len(label))
    for fn in funcs["modified"]:
        label = f"~ {_display_name(fn)}"
        if fn.signature_changed:
            label += "  sig changed"
        elif fn.calls_added_new or fn.calls_added_existing or fn.calls_removed:
            label += "  calls changed"
        else:
            label += "  body changed"
        rows.append(len(label))
    for fn in funcs["removed"]:
        rows.append(len(f"- {_display_name(fn)}"))
    rows.append(len(file_path))
    # +4 for panel border (2) + padding (2)
    return max(rows, default=20) + 4


def _topo_order_groups(
    added_fns: list[AddedFunctionInfo],
) -> list[tuple[str, str]]:
    """Return (file, class) groups in topological call order.

    Groups are sorted so that if class A calls any new function in class B,
    A comes before B. This keeps same-chain functions adjacent in the panel.
    Cycles (rare) fall back to alphabetical order.
    """
    # Map function_id → its group key
    fn_to_group: dict[str, tuple[str, str]] = {fn.function_id: _group_key(fn) for fn in added_fns}

    # Collect groups preserving first-seen order
    groups: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for fn in added_fns:
        g = _group_key(fn)
        if g not in seen:
            groups.append(g)
            seen.add(g)

    # Build directed edges: group A → group B when fn in A calls fn in B
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {g: set() for g in groups}
    for fn in added_fns:
        my_g = _group_key(fn)
        for callee_id in fn.new_calls:
            callee_g = fn_to_group.get(callee_id)
            if callee_g and callee_g != my_g and callee_g in seen:
                edges[my_g].add(callee_g)

    # Kahn's topological sort
    in_deg: dict[tuple[str, str], int] = {g: 0 for g in groups}
    for g, callees in edges.items():
        for cg in callees:
            in_deg[cg] += 1

    queue = sorted([g for g in groups if in_deg[g] == 0], key=lambda g: g[1])
    result: list[tuple[str, str]] = []
    while queue:
        g = queue.pop(0)
        result.append(g)
        for cg in sorted(edges[g], key=lambda g: g[1]):
            in_deg[cg] -= 1
            if in_deg[cg] == 0:
                queue.append(cg)

    # Cycles or isolated groups not yet placed — append alphabetically
    placed = set(result)
    result.extend(sorted((g for g in groups if g not in placed), key=lambda g: g[1]))
    return result


def _build_file_panel(
    file_path: str,
    funcs: dict,
    color_map: dict[str, str],
) -> Panel:
    """Build a Rich Panel listing the changed functions in one file."""
    content = Text()

    # Added: entry points first, then rest in DFS order from _order_group
    added_ordered: list[AddedFunctionInfo] = []
    fn_by_group: dict[tuple[str, str], list[AddedFunctionInfo]] = defaultdict(list)
    for fn in funcs["added"]:
        fn_by_group[_group_key(fn)].append(fn)
    for grp_key in _topo_order_groups(funcs["added"]):
        added_ordered.extend(_order_group(fn_by_group[grp_key], color_map))

    # Depth rule: use the most recent same-color caller in the list, BUT only
    # if there is no "chain break" between that caller and the current function.
    # A chain break is a depth-0 function of a DIFFERENT color appearing in
    # between — it signals a new independent chain starting, so the current
    # function must also start at depth 0.
    # This lets siblings share the same depth as their parent's other children
    # (e.g. _render_modified_trees appears at depth 2 after build_subtree at
    # depth 3, correctly de-indented as a sibling), while still preventing
    # isolated functions from creating false parent-child appearances.
    depth: dict[str, int] = {}
    for i, fn in enumerate(added_ordered):
        my_color = color_map.get(fn.function_id)
        parent_depth: Optional[int] = None
        for j in range(i - 1, -1, -1):
            prev = added_ordered[j]
            if (
                prev.function_id in fn.new_callers
                and my_color is not None
                and color_map.get(prev.function_id) == my_color
            ):
                # Check for a chain break between j and i
                broken = any(
                    depth[added_ordered[k].function_id] == 0
                    and color_map.get(added_ordered[k].function_id) != my_color
                    for k in range(j + 1, i)
                )
                if not broken:
                    parent_depth = depth[prev.function_id]
                break
        depth[fn.function_id] = (parent_depth + 1) if parent_depth is not None else 0

    first = True
    for fn in added_ordered:
        d = depth[fn.function_id]
        # Blank line between independent chains (new entry point, not the first)
        if not first:
            content.append("\n\n" if d == 0 else "\n")
        first = False
        chain_color = color_map.get(fn.function_id)
        name_style = f"bold {chain_color}" if chain_color else "bold"
        if d == 0:
            content.append("+ ", style="bold green")
        else:
            content.append("  " * d, style="")
            content.append("→ ", style="dim green")
        content.append(_display_name(fn), style=name_style)
        if fn.is_entry_point:
            content.append("  entry point", style="dim")

    for fn in sorted(funcs["modified"], key=lambda f: _display_name(f)):
        if not first:
            content.append("\n")
        first = False
        content.append("~ ", style="bold yellow")
        chain_color = color_map.get(fn.function_id)
        content.append(_display_name(fn), style=chain_color if chain_color else "default")
        if fn.signature_changed:
            content.append("  sig changed", style="dim")
        elif fn.calls_added_new or fn.calls_added_existing or fn.calls_removed:
            content.append("  calls changed", style="dim")
        else:
            content.append("  body changed", style="dim")

    for fn in sorted(funcs["removed"], key=lambda f: _display_name(f)):
        if not first:
            content.append("\n")
        first = False
        content.append("- ", style="bold red")
        chain_color = color_map.get(fn.function_id)
        content.append(_display_name(fn), style=chain_color if chain_color else "default")

    return Panel(
        content,
        title=f"[dim]{file_path}[/dim]",
        border_style="grey50",
        padding=(0, 1),
        expand=False,
    )


def _arrow_cell(
    callee_ids: list[str],
    color_map: dict[str, str],
    reverse: bool = False,
) -> Text:
    """Arrow between two connected panels, colored with the callee's chain color."""
    # Use the chain color of the first recognized callee; fall back to dim green
    color = "dim green"
    for fid in callee_ids:
        c = color_map.get(fid)
        if c:
            color = c
            break
    arrow = Text(justify="center")
    arrow.append("◀────" if reverse else "────▶", style=color)
    return arrow


# ---------------------------------------------------------------------------
# Table builders (kept for --table fallback)
# ---------------------------------------------------------------------------


def _added_table(functions: list[AddedFunctionInfo], color_map: dict[str, str]) -> Table:
    # Sort by group, then order within each group: entry points first, then BFS.
    grouped = sorted(functions, key=_group_key)
    fns: list[AddedFunctionInfo] = []
    for _, grp in groupby(grouped, key=_group_key):
        fns.extend(_order_group(list(grp), color_map))
    has_class = any(fn.class_name for fn in fns)
    cols: list[tuple[str, dict]] = [
        ("", {"width": 1, "style": "green bold"}),
        ("File", {"style": "dim", "no_wrap": True}),
    ]
    if has_class:
        cols.append(("Class", {"style": "dim", "no_wrap": True, "justify": "center"}))
    cols.append(("Function", {"no_wrap": True}))
    cols.append(("← Caller / → Callee", {}))
    t = _make_table(*cols)
    _add_grouped_rows(
        t,
        fns,
        has_class,
        "+",
        lambda fn: [
            _fn_label(fn.function_id, is_new=True, color_map=color_map),
            _connections_cell(fn, color_map),
        ],
    )
    return t


def _connections_cell(fn: AddedFunctionInfo, color_map: dict[str, str]) -> str:
    lines: list[str] = []

    if fn.is_entry_point:
        lines.append("[dim]entry point[/dim]")
    else:
        new_set = set(fn.new_callers)
        all_callers = fn.existing_callers + fn.new_callers
        shown, rest = _truncate(all_callers, _CALLER_MAX)
        parts = [_fn_label(c, is_new=c in new_set, color_map=color_map) for c in shown]
        if rest:
            parts.append(f"[dim]+{rest}…[/dim]")
        lines.append("[dim]←[/dim] " + ", ".join(parts))

    all_calls = fn.existing_calls + fn.new_calls
    if all_calls:
        new_set = set(fn.new_calls)
        shown, rest = _truncate(all_calls, _CALLEE_MAX)
        parts = [_fn_label(c, is_new=c in new_set, color_map=color_map) for c in shown]
        if rest:
            parts.append(f"[dim]+{rest}…[/dim]")
        lines.append("[dim]→[/dim] " + ", ".join(parts))

    return "\n".join(lines)


def _modified_table(functions: list[ModifiedFunctionInfo], color_map: dict[str, str]) -> Table:
    fns = sorted(functions, key=lambda f: (*_group_key(f), _name(f.function_id)))
    has_class = any(fn.class_name for fn in fns)
    cols: list[tuple[str, dict]] = [
        ("", {"width": 1, "style": "yellow bold"}),
        ("File", {"style": "dim", "no_wrap": True}),
    ]
    if has_class:
        cols.append(("Class", {"style": "dim", "no_wrap": True, "justify": "center"}))
    cols.append(("Function", {"no_wrap": True}))
    cols.append(("Changes", {}))
    t = _make_table(*cols)
    _add_grouped_rows(
        t,
        fns,
        has_class,
        "~",
        lambda fn: [_name(fn.function_id), _changes_cell(fn, color_map)],
    )
    return t


def _changes_cell(fn: ModifiedFunctionInfo, color_map: dict[str, str]) -> str:
    lines: list[str] = []

    if fn.signature_changed:
        old_sig = _fmt_sig(fn.old_params, fn.old_return_type)
        new_sig = _fmt_sig(fn.new_params, fn.new_return_type)
        lines.append(f"[dim]was[/dim] {old_sig}  [dim]→[/dim]  [dim]now[/dim] {new_sig}")

    added_calls = fn.calls_added_new + fn.calls_added_existing
    if added_calls:
        new_set = set(fn.calls_added_new)
        shown, rest = _truncate(added_calls, _CALLEE_MAX)
        parts = [_fn_label(c, is_new=c in new_set, color_map=color_map) for c in shown]
        if rest:
            parts.append(f"[dim]+{rest}…[/dim]")
        lines.append("[green]+[/green] " + ", ".join(parts))

    if fn.calls_removed:
        shown, rest = _truncate(fn.calls_removed, _CALLEE_MAX)
        parts = [f"[dim]{_name(c)}[/dim]" for c in shown]
        if rest:
            parts.append(f"[dim]+{rest}…[/dim]")
        lines.append("[red]-[/red] " + ", ".join(parts))

    if not lines:
        lines.append("[dim]body changed[/dim]")

    return "\n".join(lines)


def _removed_table(functions: list[RemovedFunctionInfo]) -> Table:
    fns = sorted(functions, key=lambda f: (*_group_key(f), _name(f.function_id)))
    has_class = any(fn.class_name for fn in fns)
    cols: list[tuple[str, dict]] = [
        ("", {"width": 1, "style": "red bold"}),
        ("File", {"style": "dim", "no_wrap": True}),
    ]
    if has_class:
        cols.append(("Class", {"style": "dim", "no_wrap": True, "justify": "center"}))
    cols.append(("Function", {"no_wrap": True}))
    cols.append(("Was Called By", {}))
    t = _make_table(*cols)
    _add_grouped_rows(t, fns, has_class, "-", _removed_extra_cells)
    return t


def _removed_extra_cells(fn: RemovedFunctionInfo) -> list[str]:
    shown, rest = _truncate(fn.was_called_by, _CALLER_MAX)
    parts = [f"[dim]{_name(c)}[/dim]" for c in shown]
    if rest:
        parts.append(f"[dim]+{rest}…[/dim]")
    return [_name(fn.function_id), ", ".join(parts) if parts else "[dim]—[/dim]"]


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


def render_to_string(
    result: AnalysisResult,
    base_ref: str = "HEAD",
    head_ref: str = "working tree",
    include_tests: bool = False,
) -> str:
    """Return the rendered diff report as a plain-text string (no ANSI colors)."""
    from io import StringIO

    buf = StringIO()
    cap = Console(file=buf, no_color=True, force_terminal=False, width=120)
    global console
    _old = console
    console = cap
    try:
        render(result, base_ref, head_ref, include_tests)
    finally:
        console = _old
    return buf.getvalue()
