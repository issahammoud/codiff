"""Render an AnalysisResult to the terminal using Rich.

Groups changes into source vs. test functions and renders a UML-style
box layout: one panel per file, side by side when they fit the terminal,
with arrows between connected panels.

Color scheme
------------
- New functions that share a call-graph chain are assigned a distinct color.
  The same color is used consistently in both the function name and every
  Caller / Callee reference, so you can visually trace a chain at a glance.
- New functions that belong to no chain (isolated additions) stay white.
- Existing / pre-diff functions are rendered dim (gray).
"""

from collections import defaultdict
from pathlib import Path
from typing import Optional

from rich.box import Box
from rich.console import Console, Group
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

# Dashed box for class sub-panels inside a file panel.
# Rich Box format: exactly 4 chars per row = (left, fill, mid-divider, right)
_CLASS_BOX = Box(
    "╭╌╌╮\n"  # top
    "│  │\n"  # head
    "├╌╌┤\n"  # head_row (separator under title)
    "│  │\n"  # mid
    "├╌╌┤\n"  # row
    "├╌╌┤\n"  # foot_row
    "│  │\n"  # foot
    "╰╌╌╯\n",  # bottom
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


def _is_test(file_path: str) -> bool:
    return any(part.startswith("test") for part in Path(file_path).parts)


def _partition(items: list, *, test: bool) -> list:
    return [i for i in items if _is_test(i.file_path) == test]


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

    _render_group(
        "Source", src_added, src_modified, src_removed, color_map, term_w, result.class_parents
    )
    if include_tests:
        _render_group(
            "Tests", tst_added, tst_modified, tst_removed, color_map, term_w, result.class_parents
        )
    console.print()


def _render_group(
    title: str,
    added: list[AddedFunctionInfo],
    modified: list[ModifiedFunctionInfo],
    removed: list[RemovedFunctionInfo],
    color_map: dict[str, str],
    term_w: int,
    class_parents: dict[str, list[str]] | None = None,
) -> None:
    if not added and not modified and not removed:
        return
    console.print()
    console.rule(f"[bold]{title}[/bold]", style="dim blue")
    console.print()
    _render_uml(added, modified, removed, color_map, term_w, class_parents)


# ---------------------------------------------------------------------------
# UML layout
# ---------------------------------------------------------------------------


def _render_uml(
    added: list[AddedFunctionInfo],
    modified: list[ModifiedFunctionInfo],
    removed: list[RemovedFunctionInfo],
    color_map: dict[str, str],
    term_w: int,
    class_parents: dict[str, list[str]] | None = None,
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

    # Detect cross-file inheritance from ClassChunk.superclasses (via class_parents).
    # class_parents: class_id → list of superclass names (e.g. "PreChunkBuilder")
    # We map each class_id to its file via the changed functions, then check if any
    # superclass name matches a class in a different file.
    inherit: set[tuple[str, str]] = set()
    if class_parents:
        # Build: class_name → file_path from changed functions
        class_name_to_file: dict[str, str] = {}
        for fn in list(added) + list(modified) + list(removed):
            if fn.class_name:
                class_name_to_file[fn.class_name] = fn.file_path

        # Build: class_id → file_path (class_id = "module.path.ClassName")
        class_id_to_file: dict[str, str] = {}
        for fn in list(added) + list(modified) + list(removed):
            if fn.class_name:
                # Derive class_id prefix from function_id
                parts = fn.function_id.split(".")
                if len(parts) >= 2:
                    class_id = ".".join(parts[:-1])  # drop the method name
                    class_id_to_file[class_id] = fn.file_path

        for class_id, parents in class_parents.items():
            child_file = class_id_to_file.get(class_id)
            if not child_file:
                continue
            for parent_name in parents:
                # Strip generic type params (e.g. "BaseModel[T]" → "BaseModel")
                parent_name = parent_name.split("[")[0].strip()
                parent_file = class_name_to_file.get(parent_name)
                if parent_file and parent_file != child_file:
                    inherit.add((child_file, parent_file))

    # Count how many cross-file call arrows point INTO each file
    in_count: dict[str, int] = defaultdict(int)
    for _, to_f in cross:
        in_count[to_f] += 1

    # Files deferred to the end: has removals AND nobody calls into it.
    # This covers both removed-only files and mixed files that happen to
    # not be depended upon — their deletions are easier to read last.
    deferred = {fp for fp in files if files[fp]["removed"] and in_count[fp] == 0}
    primary = {fp for fp in files if fp not in deferred}

    # Sort primary files so connected pairs are adjacent (maximises arrows).
    placed: set[str] = set()
    all_fps: list[str] = []
    for from_fp, to_fp in sorted(cross):
        for fp in (from_fp, to_fp):
            if fp in primary and fp not in placed:
                all_fps.append(fp)
                placed.add(fp)
    for fp in sorted(primary):
        if fp not in placed:
            all_fps.append(fp)

    # Deferred files (have removals, not called by anyone) come last
    all_fps.extend(sorted(deferred))

    panels = {fp: _build_file_panel(fp, files[fp], color_map, class_parents) for fp in all_fps}
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
                grid.add_column(vertical="middle", width=12)  # centred arrow column
        row_cells: list = []
        for i, fp in enumerate(row_fps):
            row_cells.append(panels[fp])
            if i < len(row_fps) - 1:
                next_fp = row_fps[i + 1]
                fwd = cross.get((fp, next_fp), [])
                rev = cross.get((next_fp, fp), [])
                callee_ids = fwd + rev
                has_calls = bool(callee_ids)
                has_inherits = (fp, next_fp) in inherit or (next_fp, fp) in inherit
                if has_calls or has_inherits:
                    row_cells.append(
                        _arrow_cell(
                            callee_ids,
                            color_map,
                            reverse=bool(rev and not fwd),
                            has_calls=has_calls,
                            has_inherits=has_inherits,
                            inherit_reverse=(next_fp, fp) in inherit
                            and (fp, next_fp) not in inherit,
                        )
                    )
                else:
                    row_cells.append(Text(""))
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
    class_parents: dict[str, list[str]] | None = None,
) -> Panel:
    """Build a Rich Panel listing the changed functions in one file.

    Methods belonging to the same class are wrapped in a dashed sub-panel
    for visual grouping. Standalone functions (no class) appear directly.
    """
    # ── Order added functions (entry-points first, then DFS by chain) ────────
    added_ordered: list[AddedFunctionInfo] = []
    fn_by_group: dict[tuple[str, str], list[AddedFunctionInfo]] = defaultdict(list)
    for fn in funcs["added"]:
        fn_by_group[_group_key(fn)].append(fn)
    for grp_key in _topo_order_groups(funcs["added"]):
        added_ordered.extend(_order_group(fn_by_group[grp_key], color_map))

    # Compute indent depth within each class group (depth rule: same-color caller)
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
                broken = any(
                    depth[added_ordered[k].function_id] == 0
                    and color_map.get(added_ordered[k].function_id) != my_color
                    for k in range(j + 1, i)
                )
                if not broken:
                    parent_depth = depth[prev.function_id]
                break
        depth[fn.function_id] = (parent_depth + 1) if parent_depth is not None else 0

    # ── Bucket all functions by class ─────────────────────────────────────────
    # class_name → {"added": [...], "modified": [...], "removed": [...]}
    # Preserve the topo ordering for added; sort modified and removed.
    class_buckets: dict[str | None, dict] = {}
    for fn in added_ordered:
        cn = fn.class_name
        class_buckets.setdefault(cn, {"added": [], "modified": [], "removed": []})["added"].append(
            fn
        )
    for fn in sorted(funcs["modified"], key=lambda f: _display_name(f)):
        cn = fn.class_name
        class_buckets.setdefault(cn, {"added": [], "modified": [], "removed": []})[
            "modified"
        ].append(fn)
    for fn in sorted(funcs["removed"], key=lambda f: _display_name(f)):
        cn = fn.class_name
        class_buckets.setdefault(cn, {"added": [], "modified": [], "removed": []})[
            "removed"
        ].append(fn)

    # Order: standalone (None) first, then classes in the order they appeared
    ordered_classes: list[str | None] = []
    if None in class_buckets:
        ordered_classes.append(None)
    seen: set[str | None] = {None}
    for fn in added_ordered:
        if fn.class_name not in seen:
            ordered_classes.append(fn.class_name)
            seen.add(fn.class_name)
    for cn in class_buckets:
        if cn not in seen:
            ordered_classes.append(cn)
            seen.add(cn)

    # ── Detect intra-file relationships ──────────────────────────────────────
    # class_name → list of (rel_type, target_class_name) for classes in this file
    all_class_names = {cn for cn in class_buckets if cn is not None}
    file_module = file_path.replace("/", ".").replace(".py", "")

    # Inheritance: use class_parents (ClassChunk.superclasses)
    intra_inherit: dict[str, list[str]] = defaultdict(list)
    if class_parents:
        for cn in all_class_names:
            cid = f"{file_module}.{cn}"
            for parent in class_parents.get(cid, []):
                parent = parent.split("[")[0].strip()  # strip generics
                if parent in all_class_names and parent != cn:
                    intra_inherit[cn].append(parent)

    # Calls: scan callee function_ids for other class names in this file
    def _is_class_like(name: str) -> bool:
        stripped = name.lstrip("_")
        return bool(stripped) and stripped[0].isupper()

    intra_calls: dict[str, list[str]] = defaultdict(list)
    for cn in all_class_names:
        seen_callees: set[str] = set()
        all_fns_in_class = (
            class_buckets[cn]["added"]
            + class_buckets[cn]["modified"]
            + class_buckets[cn]["removed"]
        )
        for fn in all_fns_in_class:
            fn_calls = (
                list(getattr(fn, "new_calls", []))
                + list(getattr(fn, "existing_calls", []))
                + list(getattr(fn, "calls_added_new", []))
                + list(getattr(fn, "calls_added_existing", []))
            )
            for call_id in fn_calls:
                parts_c = call_id.split(".")
                if len(parts_c) >= 2:
                    potential = parts_c[-2]
                    if (
                        potential in all_class_names
                        and potential != cn
                        and potential not in seen_callees
                        and _is_class_like(potential)
                    ):
                        seen_callees.add(potential)
                        intra_calls[cn].append(potential)

    # Merge into per-class relation list: [(rel_type, target), ...]
    class_relations: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for cn, parents in intra_inherit.items():
        for p in sorted(set(parents)):
            class_relations[cn].append(("inherits", p))
    for cn, callees in intra_calls.items():
        for c in sorted(set(callees)):
            # Skip if already listed as inheritance (avoids duplication
            # when a class both inherits and calls methods of the parent)
            if not any(r == "inherits" and t == c for r, t in class_relations[cn]):
                class_relations[cn].append(("calls", c))

    # ── Build content per class ───────────────────────────────────────────────
    def _build_class_text(
        bucket: dict,
        use_short_name: bool = False,
        relations: list[tuple[str, str]] | None = None,
    ) -> "Text | Panel | Group":
        """Render the methods in one class bucket, optionally prefixed with
        a dim relationship section listing intra-file calls/inheritance."""

        def label(fn: object) -> str:
            return _name(getattr(fn, "function_id", "")) if use_short_name else _display_name(fn)  # type: ignore[arg-type]

        # ── Added + modified ──────────────────────────────────────────────
        text = Text(justify="left")
        first = True

        # Relationship section at the top of each class box
        if relations:
            for rel_type, target in relations:
                if not first:
                    text.append("\n")
                first = False
                text.append(f"{rel_type} ", style="dim")
                text.append(target, style="dim italic")
            text.append("\n")
            text.append("──────────", style="dim")

        for fn in bucket["added"]:
            d = 0 if use_short_name else depth.get(fn.function_id, 0)
            if not first:
                text.append("\n" if use_short_name else ("\n\n" if d == 0 else "\n"))
            first = False
            chain_color = color_map.get(fn.function_id)
            name_style = f"bold {chain_color}" if chain_color else "bold"
            if d == 0:
                text.append("+ ", style="bold green")
            else:
                text.append("  " * d)
                text.append("→ ", style="dim green")
            text.append(label(fn), style=name_style)
            if fn.is_entry_point:
                text.append("  entry point", style="dim")

        for fn in bucket["modified"]:
            if not first:
                text.append("\n")
            first = False
            text.append("~ ", style="bold yellow")
            chain_color = color_map.get(fn.function_id)
            text.append(label(fn), style=chain_color if chain_color else "default")
            if fn.signature_changed:
                text.append("  sig changed", style="dim")
            elif fn.calls_added_new or fn.calls_added_existing or fn.calls_removed:
                text.append("  calls changed", style="dim")
            else:
                text.append("  body changed", style="dim")

        # ── Removed → red "deleted" sub-panel ────────────────────────────
        if not bucket["removed"]:
            return text

        removed_text = Text(justify="left")
        for i, fn in enumerate(bucket["removed"]):
            if i:
                removed_text.append("\n")
            removed_text.append("- ", style="bold red")
            chain_color = color_map.get(fn.function_id)
            removed_text.append(label(fn), style=chain_color if chain_color else "default")

        deleted_panel = Panel(
            removed_text,
            title="[bold red]deleted[/bold red]",
            box=_CLASS_BOX,
            border_style="red",
            padding=(0, 1),
            expand=True,
        )

        # If there were added/modified entries above, stack with spacing
        if not first:
            return Group(text, deleted_panel)
        return deleted_panel

    # ── Assemble renderables ──────────────────────────────────────────────────
    parts: list = []
    for cn in ordered_classes:
        bucket = class_buckets[cn]
        if cn is None:
            # Standalone functions — no sub-box, use full display name
            parts.append(_build_class_text(bucket, use_short_name=False))
        else:
            # Class methods — dashed sub-panel.
            panel = Panel(
                _build_class_text(bucket, use_short_name=True, relations=class_relations.get(cn)),
                title=f"[dim italic]{cn}[/dim italic]",
                box=_CLASS_BOX,
                border_style="grey50",
                padding=(0, 1),
                expand=True,
            )
            parts.append(panel)

    content = Group(*parts) if len(parts) > 1 else (parts[0] if parts else Text())

    return Panel(
        content,
        title=f"[dim]{file_path}[/dim]",
        border_style="grey50",
        padding=(1, 1),  # top=1 gives the breathing room previously added via Text("")
        expand=False,
    )


def _arrow_cell(
    callee_ids: list[str],
    color_map: dict[str, str],
    reverse: bool = False,
    has_calls: bool = True,
    has_inherits: bool = False,
    inherit_reverse: bool = False,
) -> Text:
    """Arrow(s) between two connected panels, each labeled with its type.

    All arrows share the same shape (────▶ / ◀────); the label above
    distinguishes the relationship type so new types can be added easily.
    """
    cell = Text(justify="center")
    first = True

    if has_calls:
        call_color = "dim green"
        for fid in callee_ids:
            c = color_map.get(fid)
            if c:
                call_color = c
                break
        if not first:
            cell.append("\n")
        first = False
        cell.append("calls\n", style="dim")
        cell.append("◀────" if reverse else "────▶", style=call_color)

    if has_inherits:
        if not first:
            cell.append("\n")
        cell.append("inherits\n", style="dim")
        cell.append("◀────" if inherit_reverse else "────▶", style="dim blue")

    return cell
