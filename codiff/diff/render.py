"""Render an AnalysisResult to the terminal using Rich.

Each section is only printed when it has content. When a list exceeds
ROLLUP_THRESHOLD items, we show a capped subset plus a "… and N more" line
rather than dumping everything.
"""

from collections import defaultdict
from typing import Optional

from rich.console import Console

from codiff.diff.analysis import (
    AnalysisResult,
)

console = Console()

# Max items shown in each list before rolling up
_EDGE_MAX = 15
_CALLER_MAX = 8
_GENERIC_MAX = 10


def render(result: AnalysisResult, base_ref: str = "HEAD") -> None:
    """Print the full structural diff report."""
    console.print()
    console.rule(
        f"[bold cyan]codiff[/bold cyan]  [dim]{base_ref}[/dim] [dim]→[/dim] working tree",
        style="cyan",
    )

    _render_summary(result)
    _render_wiring(result)
    _render_blast(result)
    _render_liveness(result)
    _render_boundaries(result)
    _render_signatures(result)
    _render_flags(result)
    console.print()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lbl(function_id: str) -> str:
    """Display label: last 3 dotted parts, or the full id if short."""
    parts = function_id.split(".")
    return ".".join(parts[-3:]) if len(parts) > 3 else function_id


def _section(title: str) -> None:
    console.print()
    console.rule(f"[bold]{title}[/bold]", style="dim")


def _truncated_list(items: list, max_items: int) -> tuple[list, Optional[int]]:
    """Return (shown_items, remaining_count_or_None)."""
    if len(items) <= max_items:
        return items, None
    return items[:max_items], len(items) - max_items


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_summary(result: AnalysisResult) -> None:
    s = result.summary
    total_changes = s.added_functions + s.removed_functions + s.modified_functions
    if total_changes == 0 and s.added_edges == 0 and s.removed_edges == 0:
        console.print("\n  [dim]No structural changes detected.[/dim]")
        return

    _section("Summary")

    func_parts: list[str] = []
    if s.added_functions:
        func_parts.append(f"[green]+{s.added_functions}[/green]")
    if s.removed_functions:
        func_parts.append(f"[red]-{s.removed_functions}[/red]")
    if s.modified_functions:
        func_parts.append(f"[yellow]~{s.modified_functions}[/yellow]")
    funcs = ("  " + " / ".join(func_parts) + " functions") if func_parts else "  0 functions"

    edge_parts: list[str] = []
    if s.added_edges:
        edge_parts.append(f"[green]+{s.added_edges}[/green]")
    if s.removed_edges:
        edge_parts.append(f"[red]-{s.removed_edges}[/red]")
    edges = ("  " + " / ".join(edge_parts) + " edges") if edge_parts else ""

    n = len(s.modules_touched)
    mods = f"  {n} module{'s' if n != 1 else ''} touched"

    line = "   |   ".join(filter(None, [funcs, edges, mods]))
    console.print(line)

    for mod in s.modules_touched:
        console.print(f"    [dim]{mod}[/dim]")


def _render_wiring(result: AnalysisResult) -> None:
    w = result.wiring
    if not w.new_edges and not w.removed_edges and not w.chain_insertions:
        return

    _section("Wiring")

    if w.new_edges:
        shown, rest = _truncated_list(w.new_edges, _EDGE_MAX)
        console.print(f"  [green]New edges ({len(w.new_edges)})[/green]")
        for caller, callee in shown:
            console.print(f"    [green]+[/green] {_lbl(caller)} → {_lbl(callee)}")
        if rest:
            console.print(f"    [dim]… and {rest} more[/dim]")

    if w.removed_edges:
        shown, rest = _truncated_list(w.removed_edges, _EDGE_MAX)
        console.print(f"  [red]Removed edges ({len(w.removed_edges)})[/red]")
        for caller, callee in shown:
            console.print(f"    [red]-[/red] {_lbl(caller)} → {_lbl(callee)}")
        if rest:
            console.print(f"    [dim]… and {rest} more[/dim]")

    if w.chain_insertions:
        console.print(f"  [yellow]Chain insertions ({len(w.chain_insertions)})[/yellow]")
        shown, rest = _truncated_list(w.chain_insertions, _GENERIC_MAX)
        for ins in shown:
            console.print(
                f"    [yellow]+[/yellow] [bold]{_lbl(ins.new_node_id)}[/bold] inserted between"
                f" {_lbl(ins.predecessor_id)} → {_lbl(ins.successor_id)}"
            )
        if rest:
            console.print(f"    [dim]… and {rest} more[/dim]")


def _render_blast(result: AnalysisResult) -> None:
    b = result.blast
    if not b.upstream_callers and not b.downstream_callees:
        return

    _section("Blast Radius")
    console.print(
        "  [dim]Callers / callees of changed functions that were NOT themselves modified.[/dim]"
    )

    if b.upstream_callers:
        console.print()
        console.print("  [bold]Upstream callers[/bold]")
        for fid, callers in sorted(b.upstream_callers.items()):
            shown, rest = _truncated_list(callers, _CALLER_MAX)
            caller_str = ", ".join(_lbl(c) for c in shown)
            if rest:
                caller_str += f", [dim]… +{rest} more[/dim]"
            console.print(f"    [yellow]{_lbl(fid)}[/yellow] ← {caller_str}")

    if b.downstream_callees:
        console.print()
        console.print("  [bold]Downstream callees[/bold]")
        for fid, callees in sorted(b.downstream_callees.items()):
            shown, rest = _truncated_list(callees, _CALLER_MAX)
            callee_str = ", ".join(_lbl(c) for c in shown)
            if rest:
                callee_str += f", [dim]… +{rest} more[/dim]"
            console.print(f"    [yellow]{_lbl(fid)}[/yellow] → {callee_str}")


def _render_liveness(result: AnalysisResult) -> None:
    lv = result.liveness
    if not lv.dead_on_arrival and not lv.newly_orphaned:
        return

    _section("Liveness")

    if lv.dead_on_arrival:
        console.print("  [red]Dead on arrival[/red] [dim](new functions nothing calls)[/dim]")
        shown, rest = _truncated_list(lv.dead_on_arrival, _GENERIC_MAX)
        for fid in shown:
            console.print(f"    [red]•[/red] {_lbl(fid)}")
        if rest:
            console.print(f"    [dim]… and {rest} more[/dim]")

    if lv.newly_orphaned:
        console.print("  [red]Newly orphaned[/red] [dim](had callers, now has none)[/dim]")
        shown, rest = _truncated_list(lv.newly_orphaned, _GENERIC_MAX)
        for fid in shown:
            console.print(f"    [red]•[/red] {_lbl(fid)}")
        if rest:
            console.print(f"    [dim]… and {rest} more[/dim]")


def _render_boundaries(result: AnalysisResult) -> None:
    edges = result.boundaries.new_cross_module_edges
    if not edges:
        return

    _section("Boundaries")
    console.print("  [bold]New cross-module edges[/bold]")

    # Group by (caller_module, callee_module) for rollup
    by_pair: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for caller_id, callee_id in edges:
        # Module = file_path prefix (directory portion of the function id)
        caller_mod = ".".join(caller_id.split(".")[:-1]) if "." in caller_id else caller_id
        callee_mod = ".".join(callee_id.split(".")[:-1]) if "." in callee_id else callee_id
        by_pair[(caller_mod, callee_mod)].append((caller_id, callee_id))

    shown_pairs, rest = _truncated_list(sorted(by_pair.items()), _GENERIC_MAX)
    for (caller_mod, callee_mod), pair_edges in shown_pairs:
        n = len(pair_edges)
        ex_caller, ex_callee = pair_edges[0]
        extra = f" [dim](e.g. {_lbl(ex_caller)} → {_lbl(ex_callee)})[/dim]" if n == 1 else ""
        console.print(
            f"    [cyan]{caller_mod}[/cyan] → [cyan]{callee_mod}[/cyan]"
            f"  [dim]{n} edge{'s' if n > 1 else ''}[/dim]{extra}"
        )
    if rest:
        console.print(f"    [dim]… and {rest} more module pairs[/dim]")


def _render_signatures(result: AnalysisResult) -> None:
    changes = result.signatures.changes
    if not changes:
        return

    _section("Signatures")
    shown, rest = _truncated_list(changes, _GENERIC_MAX)

    for ch in shown:
        console.print(f"  [bold yellow]{_lbl(ch.function_id)}[/bold yellow]")
        old_sig = _fmt_sig(ch.old_params, ch.old_return_type)
        new_sig = _fmt_sig(ch.new_params, ch.new_return_type)
        console.print(f"    [red]was[/red] {old_sig}")
        console.print(f"    [green]now[/green] {new_sig}")
        if ch.unreconciled_callers:
            n = len(ch.unreconciled_callers)
            shown_c, rest_c = _truncated_list(ch.unreconciled_callers, 4)
            callers_str = ", ".join(_lbl(c) for c in shown_c)
            if rest_c:
                callers_str += f", [dim]… +{rest_c} more[/dim]"
            console.print(
                f"    [dim]{n} caller{'s' if n > 1 else ''} not updated:[/dim] {callers_str}"
            )

    if rest:
        console.print(f"\n  [dim]… and {rest} more signature changes[/dim]")


def _render_flags(result: AnalysisResult) -> None:
    if not result.flags:
        return

    _section("Flags")
    for flag in result.flags:
        console.print(f"  [bold red]⚠[/bold red]  {flag}")


def _fmt_sig(params: list[dict], return_type: Optional[str]) -> str:
    """Format a signature as (param: type = default, ...) → return_type."""
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
