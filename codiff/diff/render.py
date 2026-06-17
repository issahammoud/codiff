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
from rich.table import Table

from codiff.diff.analysis import (
    AddedFunctionInfo,
    AnalysisResult,
    IssueItem,
    ModifiedFunctionInfo,
    RemovedFunctionInfo,
)

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
    """Assign a color to each connected component of >= 2 new functions.

    Components are identified via undirected BFS over mutual call edges among
    added functions. Single-node components (isolated additions) are not
    assigned a color — they fall back to plain white in the render.
    """
    added_ids = {fn.function_id for fn in result.added}

    adj: dict[str, set[str]] = defaultdict(set)
    for fn in result.added:
        for callee in fn.new_calls:
            adj[fn.function_id].add(callee)
            adj[callee].add(fn.function_id)
        for caller in fn.new_callers:
            adj[fn.function_id].add(caller)
            adj[caller].add(fn.function_id)

    visited: set[str] = set()
    components: list[list[str]] = []
    for fid in sorted(added_ids):
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
                if neighbor not in visited:
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
        part = p.get("name", "?")
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


def _order_group(fns: list[AddedFunctionInfo]) -> list[AddedFunctionInfo]:
    """Order functions within a (file, class) group for readability.

    Entry points (nothing calls them within the group) come first.
    BFS then follows new_calls within the group so callee rows flow
    directly after their caller. Unreached functions are appended last.
    """
    if len(fns) <= 1:
        return fns

    by_id = {fn.function_id: fn for fn in fns}
    g_ids = set(by_id)

    # Local starts: true entry points first, then functions whose callers
    # are all outside this group (so they're locally uncalled).
    starts = [fn for fn in fns if fn.is_entry_point or not (set(fn.new_callers) & g_ids)]
    starts.sort(key=lambda fn: (0 if fn.is_entry_point else 1, _name(fn.function_id)))

    # DFS so each chain stays contiguous (BFS would interleave them).
    # Children are pushed in reverse sorted order so the first callee
    # is processed next, maintaining alphabetical order within siblings.
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
            for callee_id in reversed(sorted(by_id[fid].new_calls)):
                if callee_id in g_ids and callee_id not in visited:
                    stack.append(callee_id)

    # Safety: append anything not reached (e.g. cycles)
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


def _render_summary(result: AnalysisResult) -> None:
    s = result.summary
    total = s.added_functions + s.removed_functions + s.modified_functions
    if total == 0:
        console.print("\n  [dim]No structural changes detected.[/dim]")
        return
    parts: list[str] = []
    if s.added_functions:
        parts.append(f"[green]+{s.added_functions} added[/green]")
    if s.modified_functions:
        parts.append(f"[yellow]~{s.modified_functions} modified[/yellow]")
    if s.removed_functions:
        parts.append(f"[red]-{s.removed_functions} removed[/red]")
    n = len(s.modules_touched)
    parts.append(f"[dim]{n} module{'s' if n != 1 else ''}[/dim]")
    console.print("  " + "  ·  ".join(parts))


# ---------------------------------------------------------------------------
# Group renderer
# ---------------------------------------------------------------------------


def render(result: AnalysisResult, base_ref: str = "HEAD") -> None:
    """Print the full structural diff report."""
    console.print()
    console.rule(
        f"[bold cyan]codiff[/bold cyan]  [dim]{base_ref}[/dim] [dim]→[/dim] working tree",
        style="cyan",
    )
    _render_summary(result)

    color_map = _build_color_map(result)

    src_added = _partition(result.added, test=False)
    tst_added = _partition(result.added, test=True)
    src_modified = _partition(result.modified, test=False)
    tst_modified = _partition(result.modified, test=True)
    src_removed = _partition(result.removed, test=False)
    tst_removed = _partition(result.removed, test=True)

    _render_group("Source", src_added, src_modified, src_removed, color_map)
    _render_group("Tests", tst_added, tst_modified, tst_removed, color_map)
    _render_issues(result.issues)
    console.print()


def _render_group(
    title: str,
    added: list[AddedFunctionInfo],
    modified: list[ModifiedFunctionInfo],
    removed: list[RemovedFunctionInfo],
    color_map: dict[str, str],
) -> None:
    if not added and not modified and not removed:
        return
    console.print()
    console.rule(f"[bold]{title}[/bold]", style="dim blue")

    if added:
        console.print("\n [dim]Added[/dim]")
        console.print(_added_table(added, color_map))

    if modified:
        console.print("\n [dim]Modified[/dim]")
        console.print(_modified_table(modified, color_map))

    if removed:
        console.print("\n [dim]Removed[/dim]")
        console.print(_removed_table(removed))


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------


def _added_table(functions: list[AddedFunctionInfo], color_map: dict[str, str]) -> Table:
    # Sort by group, then order within each group: entry points first, then BFS.
    grouped = sorted(functions, key=_group_key)
    fns: list[AddedFunctionInfo] = []
    for _, grp in groupby(grouped, key=_group_key):
        fns.extend(_order_group(list(grp)))
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


def render_to_string(result: AnalysisResult, base_ref: str = "HEAD") -> str:
    """Return the rendered diff report as a plain-text string (no ANSI colors)."""
    from io import StringIO

    buf = StringIO()
    cap = Console(file=buf, no_color=True, force_terminal=False, width=120)
    global console
    _old = console
    console = cap
    try:
        render(result, base_ref)
    finally:
        console = _old
    return buf.getvalue()


def _render_issues(issues: list[IssueItem]) -> None:
    if not issues:
        return
    console.print()
    console.rule("[bold]Issues[/bold]", style="dim")
    console.print()
    for issue in issues:
        console.print(
            f"  [bold red]⚠[/bold red]  [yellow]{_lbl(issue.function_id)}[/yellow]"
            f" — {issue.message}"
        )
