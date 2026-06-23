"""Generate a genuine UML class diagram from an AnalysisResult.

Uses Mermaid's native classDiagram syntax:

  namespace ns_file_stem {          ← one sanitised namespace per file
      class hdr__path["📄 path/to/file.py"] {   ← dark header showing real path
          <<file>>
      }
      class ClassName["ClassName"] {            ← Python class
          + added_method()
          ~ modified_method() sig changed
          - removed_method()
      }
      class standalone["«standalone»"] {        ← module-level functions
          <<module>>
          + func()
      }
  }

Styles applied via  `style ID fill:...`  (separate from class definition so
that Mermaid's parser accepts both a display-name AND colouring — combining
them on one line is not supported in all Mermaid versions).

Visibility prefix reused for change type (happy UML coincidence):
  +  added     (public)
  ~  modified  (package)
  -  removed   (private)

Relationships: solid --> for active calls, dashed ..> for removed calls.
"""

import re
from collections import defaultdict
from pathlib import Path

from codiff.schema.diff import (
    AddedFunctionInfo,
    AnalysisResult,
    ModifiedFunctionInfo,
    RemovedFunctionInfo,
)

# ── Style constants ───────────────────────────────────────────────────────────

# White fill + vivid colored border — clean modern design system style
_S = {
    "added": "fill:#ffffff,color:#166534,stroke:#22c55e,stroke-width:2px",
    "modified": "fill:#ffffff,color:#92400e,stroke:#f59e0b,stroke-width:2px",
    "removed": "fill:#ffffff,color:#991b1b,stroke:#f87171,stroke-width:2px",
}

# Chain palette: white fill, each chain gets a distinct vivid border
_CHAIN_STYLES = [
    "fill:#ffffff,color:#0e7490,stroke:#06b6d4,stroke-width:2.5px",  # cyan
    "fill:#ffffff,color:#6d28d9,stroke:#8b5cf6,stroke-width:2.5px",  # violet
    "fill:#ffffff,color:#c2410c,stroke:#f97316,stroke-width:2.5px",  # orange
    "fill:#ffffff,color:#15803d,stroke:#22c55e,stroke-width:2.5px",  # green
    "fill:#ffffff,color:#be185d,stroke:#ec4899,stroke-width:2.5px",  # pink
    "fill:#ffffff,color:#1d4ed8,stroke:#60a5fa,stroke-width:2.5px",  # blue
    "fill:#ffffff,color:#b91c1c,stroke:#f87171,stroke-width:2.5px",  # red
    "fill:#ffffff,color:#0f766e,stroke:#14b8a6,stroke-width:2.5px",  # teal
]

_INIT = (
    "%%{init: {"
    "'theme': 'base', "
    "'themeVariables': {"
    "'background': '#ffffff', "
    "'clusterBkg': '#f8fafc', "
    "'clusterBorder': '#cbd5e1', "
    "'primaryColor': '#ffffff', "
    "'primaryBorderColor': '#e2e8f0', "
    "'primaryTextColor': '#1e293b', "
    "'lineColor': '#64748b', "
    "'fontSize': '13px', "
    "'fontFamily': 'ui-monospace, SFMono-Regular, Menlo, monospace'"
    "}}}%%"
)


# ── ID / name helpers ─────────────────────────────────────────────────────────


def _san(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def _ns_id(file_path: str) -> str:
    # Backtick-quoted names with slashes break GitHub's Mermaid renderer;
    # use sanitised underscored names instead.
    return _san(file_path.removesuffix(".py"))


def _class_id(file_path: str, class_name: str) -> str:
    return _san(f"cls__{file_path}__{class_name}")


def _mod_id(file_path: str) -> str:
    return _san(f"mod__{file_path}")


def _is_test(file_path: str) -> bool:
    return any(part.startswith("test") for part in Path(file_path).parts)


# ── Member label helpers ──────────────────────────────────────────────────────


def _method(fid: str) -> str:
    return fid.split(".")[-1]


def _added_line(fn: AddedFunctionInfo) -> str:
    name = _method(fn.function_id)
    if fn.is_entry_point:
        return f"+ {name}()  entry point"
    n = len(fn.new_callers) + len(fn.existing_callers)
    return f"+ {name}()  ← {n} caller{'s' if n != 1 else ''}" if n else f"+ {name}()"


def _modified_line(fn: ModifiedFunctionInfo) -> str:
    name = _method(fn.function_id)
    if fn.signature_changed:
        return f"~ {name}()  sig changed"
    added = len(fn.calls_added_new) + len(fn.calls_added_existing)
    removed = len(fn.calls_removed)
    if added or removed:
        parts = ([f"+{added}"] if added else []) + ([f"−{removed}"] if removed else [])
        return f"~ {name}()  calls {'  '.join(parts)}"
    return f"~ {name}()  body changed"


def _removed_line(fn: RemovedFunctionInfo) -> str:
    name = _method(fn.function_id)
    n = len(fn.was_called_by)
    return f"- {name}()  ← {n} caller{'s' if n != 1 else ''}" if n else f"- {name}()"


def _topo_sort(nodes: list[str], edges: set[tuple[str, str]]) -> list[str]:
    """Kahn's algorithm: nodes with no incoming edges (callers) come first."""
    from collections import deque

    node_set = set(nodes)
    in_deg = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for src, dst in edges:
        if src in node_set and dst in node_set:
            adj[src].append(dst)
            in_deg[dst] += 1
    queue = deque(sorted(n for n, d in in_deg.items() if d == 0))
    result: list[str] = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for nb in sorted(adj[node]):
            in_deg[nb] -= 1
            if in_deg[nb] == 0:
                queue.append(nb)
    # append anything left (cycles)
    result.extend(sorted(n for n in nodes if n not in result))
    return result


def _class_style(fids: list[str], chain_map: dict[str, int], change_type: str) -> str:
    """Chain style if all functions share one chain, otherwise change-type style."""
    indices = {chain_map[f] for f in fids if f in chain_map}
    if len(indices) == 1:
        return _CHAIN_STYLES[indices.pop() % len(_CHAIN_STYLES)]
    return _S[change_type]


def _dominant(added: list, modified: list, removed: list) -> str:
    if removed and not added and not modified:
        return "removed"
    if added and not modified and not removed:
        return "added"
    return "modified"


def _build_chain_map(result: AnalysisResult) -> dict[str, int]:
    """BFS over changed-function call graph; return fn_id → chain index (≥2-member components only)."""
    changed: set[str] = (
        {fn.function_id for fn in result.added}
        | {fn.function_id for fn in result.modified}
        | {fn.function_id for fn in result.removed}
    )
    adj: dict[str, set[str]] = {fid: set() for fid in changed}
    for a_fn in result.added:
        for fid in a_fn.new_callers + a_fn.new_calls:
            if fid in changed:
                adj[a_fn.function_id].add(fid)
                adj[fid].add(a_fn.function_id)
    for m_fn in result.modified:
        for fid in m_fn.calls_added_new + m_fn.callers:
            if fid in changed:
                adj[m_fn.function_id].add(fid)
                adj[fid].add(m_fn.function_id)

    visited: set[str] = set()
    chain_map: dict[str, int] = {}
    chain_idx = 0
    for start in sorted(changed):
        if start in visited:
            continue
        component: list[str] = []
        queue = [start]
        while queue:
            fid = queue.pop()
            if fid in visited:
                continue
            visited.add(fid)
            component.append(fid)
            queue.extend(adj[fid] - visited)
        if len(component) >= 2:
            for fid in component:
                chain_map[fid] = chain_idx
            chain_idx += 1
    return chain_map


# ── Renderer ──────────────────────────────────────────────────────────────────


def render_mermaid(result: AnalysisResult, include_tests: bool = False) -> str:
    lines: list[str] = ["```mermaid", _INIT, "classDiagram", "    direction LR"]
    style_cmds: list[str] = []  # collected and emitted after all namespaces

    chain_map = _build_chain_map(result)

    # ── Group changed functions by file → class ───────────────────────────────
    files: dict[str, dict] = defaultdict(
        lambda: defaultdict(lambda: {"added": [], "modified": [], "removed": []})
    )
    for a_fn in result.added:
        files[a_fn.file_path][a_fn.class_name]["added"].append(a_fn)
    for m_fn in result.modified:
        files[m_fn.file_path][m_fn.class_name]["modified"].append(m_fn)
    for r_fn in result.removed:
        files[r_fn.file_path][r_fn.class_name]["removed"].append(r_fn)

    # ── Build fn_id → diagram class ID (for edges) ───────────────────────────
    fn_to_cid: dict[str, str] = {}
    for a_fn in result.added:
        fn_to_cid[a_fn.function_id] = (
            _class_id(a_fn.file_path, a_fn.class_name)
            if a_fn.class_name
            else _mod_id(a_fn.file_path)
        )
    for m_fn in result.modified:
        fn_to_cid[m_fn.function_id] = (
            _class_id(m_fn.file_path, m_fn.class_name)
            if m_fn.class_name
            else _mod_id(m_fn.file_path)
        )
    for r_fn in result.removed:
        fn_to_cid[r_fn.function_id] = (
            _class_id(r_fn.file_path, r_fn.class_name)
            if r_fn.class_name
            else _mod_id(r_fn.file_path)
        )
    # ── Build class-level edges first (needed for topo sort) ─────────────────
    cid_to_fp: dict[str, str] = {}
    for fp, classes in files.items():
        for cn in classes:
            cid_to_fp[_mod_id(fp) if cn is None else _class_id(fp, cn)] = fp

    edges: set[tuple[str, str]] = set()
    removed_edges: set[tuple[str, str]] = set()

    for a_fn in result.added:
        src = fn_to_cid.get(a_fn.function_id)
        for fid in a_fn.new_calls:
            dst = fn_to_cid.get(fid)
            if src and dst and src != dst:
                edges.add((src, dst))
        for fid in a_fn.new_callers:
            caller = fn_to_cid.get(fid)
            if caller and src and caller != src:
                edges.add((caller, src))
    for m_fn in result.modified:
        src = fn_to_cid.get(m_fn.function_id)
        for fid in m_fn.calls_added_new:
            dst = fn_to_cid.get(fid)
            if src and dst and src != dst:
                edges.add((src, dst))
        for fid in m_fn.calls_removed:
            dst = fn_to_cid.get(fid)
            if src and dst and src != dst:
                removed_edges.add((src, dst))
    for r_fn in result.removed:
        src = fn_to_cid.get(r_fn.function_id)
        for fid in r_fn.was_called_by:
            caller = fn_to_cid.get(fid)
            if caller and src and caller != src:
                edges.add((caller, src))

    # ── Topo-sort namespaces (callers before callees) ────────────────────────
    ns_edges: set[tuple[str, str]] = set()
    for src, dst in edges:
        sfp, dfp = cid_to_fp.get(src), cid_to_fp.get(dst)
        if sfp and dfp and sfp != dfp:
            ns_edges.add((sfp, dfp))

    sorted_fps = _topo_sort(sorted(files.keys()), ns_edges)

    # ── Emit namespaces in topo order ─────────────────────────────────────────
    for file_path in sorted_fps:
        changed = files[file_path]

        # Topo-sort classes within this namespace
        ns_cids = (
            [_mod_id(file_path)]
            if None in changed
            and (changed[None]["added"] or changed[None]["modified"] or changed[None]["removed"])
            else []
        ) + [_class_id(file_path, cn) for cn in changed if cn is not None]
        intra_edges = {
            (s, d)
            for s, d in edges
            if cid_to_fp.get(s) == file_path and cid_to_fp.get(d) == file_path
        }
        sorted_cids = _topo_sort(ns_cids, intra_edges)

        lines.append("")
        lines.append(f"    namespace {_ns_id(file_path)} {{")

        for cid in sorted_cids:
            if cid == _mod_id(file_path):
                sa = changed.get(None, {"added": [], "modified": [], "removed": []})
                all_fids = [fn.function_id for fn in sa["added"] + sa["modified"] + sa["removed"]]
                cs = _class_style(
                    all_fids, chain_map, _dominant(sa["added"], sa["modified"], sa["removed"])
                )
                lines.append(f'        class {cid}["«standalone functions»"] {{')
                lines.append("            <<standalone>>")
                for fn in sa["added"]:
                    lines.append(f"            {_added_line(fn)}")
                for fn in sa["modified"]:
                    lines.append(f"            {_modified_line(fn)}")
                for fn in sa["removed"]:
                    lines.append(f"            {_removed_line(fn)}")
                lines.append("        }")
                style_cmds.append(f"    style {cid} {cs}")
            else:
                # Reverse-lookup class_name from cid
                class_name = next(
                    cn for cn in changed if cn is not None and _class_id(file_path, cn) == cid
                )
                members = changed[class_name]
                all_fids = [
                    fn.function_id
                    for fn in members["added"] + members["modified"] + members["removed"]
                ]
                cs = _class_style(
                    all_fids,
                    chain_map,
                    _dominant(members["added"], members["modified"], members["removed"]),
                )
                lines.append(f'        class {cid}["{class_name}"] {{')
                for fn in members["added"]:
                    lines.append(f"            {_added_line(fn)}")
                for fn in members["modified"]:
                    lines.append(f"            {_modified_line(fn)}")
                for fn in members["removed"]:
                    lines.append(f"            {_removed_line(fn)}")
                lines.append("        }")
                style_cmds.append(f"    style {cid} {cs}")

        lines.append("    }")

    # ── Emit collected style commands ─────────────────────────────────────────
    if style_cmds:
        lines.append("")
        lines.extend(style_cmds)

    if edges or removed_edges:
        lines.append("")
        lines.append("    %% Relationships")
        for src, dst in sorted(edges):
            lines.append(f"    {src} --> {dst}")
        for src, dst in sorted(removed_edges - edges):
            lines.append(f"    {src} ..> {dst} : removed")

    lines.append("```")
    return "\n".join(lines)
