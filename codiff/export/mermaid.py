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

from codiff.schema.diff import (
    AddedFunctionInfo,
    AnalysisResult,
    ModifiedFunctionInfo,
    RemovedFunctionInfo,
)

# ── Style constants ───────────────────────────────────────────────────────────

# Tinted fill + vivid border: colour at a glance, still readable
_S = {
    "added": "fill:#f0fdf4,color:#166534,stroke:#22c55e,stroke-width:3px",
    "modified": "fill:#fefce8,color:#854d0e,stroke:#f59e0b,stroke-width:3px",
    "removed": "fill:#fff1f2,color:#991b1b,stroke:#f87171,stroke-width:3px",
}


_THEME = (
    "'maxTextSize': 999999, "
    "'theme': 'base', "
    "'themeVariables': {"
    "'background': '#ffffff', "
    "'clusterBkg': '#f8fafc', "
    "'clusterBorder': '#94a3b8', "
    "'primaryColor': '#f8fafc', "
    "'primaryBorderColor': '#94a3b8', "
    "'primaryTextColor': '#1e293b', "
    "'lineColor': '#64748b', "
    "'fontSize': '13px', "
    "'fontFamily': 'ui-monospace, SFMono-Regular, Menlo, monospace'"
    "}"
)

# Connected diagram: ELK layered algorithm, left-to-right
_INIT = f"%%{{init: {{'layout': 'elk', 'elk': {{'direction': 'RIGHT'}}, {_THEME}}}}}%%"

# Isolated diagram: Dagre with direction LR — namespace blocks (one per
# folder) arranged left-to-right, files within each folder stack vertically.
_INIT_GRID = f"%%{{init: {{{_THEME}}}}}%%"


# ── ID / name helpers ─────────────────────────────────────────────────────────


def _san(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", s)


def _class_id(file_path: str, class_name: str) -> str:
    return _san(f"cls__{file_path}__{class_name}")


def _mod_id(file_path: str) -> str:
    return _san(f"mod__{file_path}")


# ── Member label helpers ──────────────────────────────────────────────────────


def _method(fid: str) -> str:
    return fid.split(".")[-1]


def _added_line(fn: AddedFunctionInfo) -> str:
    return f"+ {_method(fn.function_id)}()"


def _modified_line(fn: ModifiedFunctionInfo) -> str:
    name = _method(fn.function_id)
    if fn.signature_changed:
        return f"~ {name}()  sig"
    added = len(fn.calls_added_new) + len(fn.calls_added_existing)
    removed = len(fn.calls_removed)
    if added or removed:
        parts = ([f"+{added}"] if added else []) + ([f"−{removed}"] if removed else [])
        return f"~ {name}()  calls {''.join(parts)}"
    return f"~ {name}()"


def _removed_line(fn: RemovedFunctionInfo) -> str:
    return f"- {_method(fn.function_id)}()"


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


def _class_style(change_type: str) -> str:
    """Always use change-type color — green/yellow/red is unambiguous at a glance.
    Chain membership is already communicated by the relationship arrows."""
    return _S[change_type]


def _dominant(added: list, modified: list, removed: list) -> str:
    if removed and not added and not modified:
        return "removed"
    if added and not modified and not removed:
        return "added"
    return "modified"


_MAX_METHODS = 12  # max method lines per class box to keep diagram size in check


def _emit_methods(
    lines: list[str],
    added: list,
    modified: list,
    removed: list,
    indent: str = "        ",
) -> None:
    """Append method lines to *lines*, truncating if the total exceeds _MAX_METHODS."""
    all_lines: list[str] = (
        [_added_line(fn) for fn in added]
        + [_modified_line(fn) for fn in modified]
        + [_removed_line(fn) for fn in removed]
    )
    shown = all_lines[:_MAX_METHODS]
    rest = len(all_lines) - len(shown)
    for ln in shown:
        lines.append(f"{indent}{ln}")
    if rest:
        lines.append(f"{indent}... {rest} more")


# ── Renderer ──────────────────────────────────────────────────────────────────


def render_mermaid(result: AnalysisResult) -> str:

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
    # ── Assign short sequential IDs to keep diagram size manageable ──────────
    # Long sanitised IDs (e.g. cls__very__long__path__ClassName) are repeated
    # in class defs, style commands, and every edge.  Replacing them with n0,
    # n1, … cuts 60-70 % off diagram size on large diffs, staying within
    # GitHub's hard rendering limit.
    _all_long_ids: list[str] = sorted(
        {_mod_id(fp) for fp in files}
        | {_class_id(fp, cn) for fp, classes in files.items() for cn in classes if cn is not None}
    )
    _short: dict[str, str] = {lid: f"n{i}" for i, lid in enumerate(_all_long_ids)}

    def _sid(long_id: str) -> str:
        return _short.get(long_id, long_id)

    # Remap fn_to_cid to short IDs
    fn_to_cid = {fid: _sid(cid) for fid, cid in fn_to_cid.items()}

    # ── Build class-level edges first (needed for topo sort) ─────────────────
    cid_to_fp: dict[str, str] = {}
    for fp, classes in files.items():
        for cn in classes:
            long = _mod_id(fp) if cn is None else _class_id(fp, cn)
            cid_to_fp[_sid(long)] = fp

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

    # ── Build inheritance edges from class_parents ───────────────────────────
    # Map class_name → cid for all changed classes (to look up by name)
    class_name_to_cid: dict[str, str] = {}
    for fp, classes in files.items():
        for cn in classes:
            if cn is not None:
                class_name_to_cid[cn] = _sid(_class_id(fp, cn))

    inherit_edges: set[tuple[str, str]] = set()
    if result.class_parents:
        for class_id, parents in result.class_parents.items():
            child_name = class_id.split(".")[-1]
            child_cid = class_name_to_cid.get(child_name)
            if not child_cid:
                continue
            for parent_name in parents:
                parent_name = parent_name.split("[")[0].strip()
                parent_cid = class_name_to_cid.get(parent_name)
                if parent_cid and parent_cid != child_cid:
                    # Use short IDs — must match the remapped fn_to_cid/cid_to_fp
                    inherit_edges.add((_sid(child_cid), _sid(parent_cid)))

    # ── Topo-sort namespaces (callers before callees) ────────────────────────
    ns_edges: set[tuple[str, str]] = set()
    for src, dst in edges:
        sfp, dfp = cid_to_fp.get(src), cid_to_fp.get(dst)
        if sfp and dfp and sfp != dfp:
            ns_edges.add((sfp, dfp))

    # Also propagate namespace ordering from inheritance
    for src, dst in inherit_edges:
        sfp, dfp = cid_to_fp.get(src), cid_to_fp.get(dst)
        if sfp and dfp and sfp != dfp:
            ns_edges.add((sfp, dfp))

    # Split files into connected (appear in at least one cross-file edge) and
    # isolated (no call/inheritance relationship with any other changed file).
    fps_in_edges: set[str] = set()
    for src, dst in edges | inherit_edges:
        sfp, dfp = cid_to_fp.get(src), cid_to_fp.get(dst)
        if sfp and dfp and sfp != dfp:
            fps_in_edges.add(sfp)
            fps_in_edges.add(dfp)

    connected_fps = {fp for fp in files if fp in fps_in_edges}
    isolated_fps = {fp for fp in files if fp not in fps_in_edges}

    # Topo-sort the connected files by call direction
    sorted_connected = _topo_sort(sorted(connected_fps), ns_edges)

    # Group isolated files by immediate parent directory so related modules
    # (e.g. migration versions) are emitted together in a namespace block.
    isolated_by_dir: dict[str, list[str]] = defaultdict(list)
    for fp in sorted(isolated_fps):
        dirs = fp.split("/")[:-1]  # path components excluding the filename
        # Group by first 2 folder levels — the first folder is usually the
        # top-level source root (backend/, src/, …), the second gives meaning.
        key = "/".join(dirs[:2]) if len(dirs) >= 2 else (dirs[0] if dirs else ".")
        isolated_by_dir[key].append(fp)

    # ── Helper: emit one file's class boxes into caller-supplied lists ────────
    emitted_ids: set[str] = set()

    def _emit_file(
        file_path: str,
        out: list[str],
        out_styles: list[str],
        out_del: list[str],
        out_del_styles: list[str],
        indent: str = "    ",
    ) -> None:
        changed = files[file_path]
        file_cids = (
            [_sid(_mod_id(file_path))]
            if None in changed
            and (changed[None]["added"] or changed[None]["modified"] or changed[None]["removed"])
            else []
        ) + [_sid(_class_id(file_path, cn)) for cn in changed if cn is not None]
        intra = {
            (s, d)
            for s, d in edges
            if cid_to_fp.get(s) == file_path and cid_to_fp.get(d) == file_path
        }
        for cid in _topo_sort(file_cids, intra):
            if cid == _sid(_mod_id(file_path)):
                sa = changed.get(None, {"added": [], "modified": [], "removed": []})
                cs = _class_style(_dominant(sa["added"], sa["modified"], sa["removed"]))
                if sa["added"] or sa["modified"]:
                    out.append(f'{indent}class {cid}["{file_path}"] {{')
                    _emit_methods(out, sa["added"], sa["modified"], [], indent + "    ")
                    out.append(f"{indent}}}")
                    out_styles.append(f"    style {cid} {cs}")
                    emitted_ids.add(cid)
                if sa["removed"]:
                    del_cid = f"{cid}_d"
                    out_del.append(f'    class {del_cid}["{file_path} (deleted)"] {{')
                    _emit_methods(out_del, [], [], sa["removed"])
                    out_del.append("    }")
                    out_del_styles.append(f"    style {del_cid} {_S['removed']}")
                    emitted_ids.add(del_cid)
            else:
                class_name = next(
                    cn for cn in changed if cn is not None and _sid(_class_id(file_path, cn)) == cid
                )
                members = changed[class_name]
                cs = _class_style(
                    _dominant(members["added"], members["modified"], members["removed"])
                )
                if members["added"] or members["modified"]:
                    out.append(f'{indent}class {cid}["{class_name}"] {{')
                    out.append(f"{indent}    <<{file_path}>>")
                    _emit_methods(out, members["added"], members["modified"], [], indent + "    ")
                    out.append(f"{indent}}}")
                    out_styles.append(f"    style {cid} {cs}")
                    emitted_ids.add(cid)
                if members["removed"]:
                    del_cid = f"{cid}_d"
                    out_del.append(f'    class {del_cid}["{class_name} (deleted)"] {{')
                    out_del.append(f"        <<{file_path}>>")
                    _emit_methods(out_del, [], [], members["removed"])
                    out_del.append("    }")
                    out_del_styles.append(f"    style {del_cid} {_S['removed']}")
                    emitted_ids.add(del_cid)

    # ── Diagram 1: connected modules — direction TB, with relationships ────────
    d1_body: list[str] = []
    d1_styles: list[str] = []
    d1_del: list[str] = []
    d1_del_styles: list[str] = []

    for file_path in sorted_connected:
        d1_body.append("")
        _emit_file(file_path, d1_body, d1_styles, d1_del, d1_del_styles)

    # ── Diagram 2: isolated modules — direction TB, namespace-grouped ──────────
    d2_body: list[str] = []
    d2_styles: list[str] = []
    d2_del: list[str] = []
    d2_del_styles: list[str] = []

    for dir_path, fps in sorted(isolated_by_dir.items()):
        ns_id = dir_path.replace("/", "-").replace(".", "-") if dir_path != "." else "root"
        d2_body.append("")
        d2_body.append(f"    namespace {ns_id} {{")
        for file_path in fps:
            _emit_file(file_path, d2_body, d2_styles, d2_del, d2_del_styles, indent="        ")
        d2_body.append("    }")

    # ── Build edge section (only for diagram 1 — isolated have no edges) ──────
    def _valid(s: str, d: str) -> bool:
        return s in emitted_ids and d in emitted_ids

    call_edges = {
        (s, d)
        for s, d in edges
        if _valid(s, d) and (s, d) not in inherit_edges and (d, s) not in inherit_edges
    }
    inherit_edges = {(s, d) for s, d in inherit_edges if _valid(s, d)}
    removed_edges = {(s, d) for s, d in removed_edges if _valid(s, d)}

    edge_lines: list[str] = []
    if call_edges or removed_edges or inherit_edges:
        edge_lines.append("")
        edge_lines.append("    %% Relationships")
        for src, dst in sorted(inherit_edges):
            edge_lines.append(f"    {src} --|> {dst}")
        for src, dst in sorted(call_edges):
            edge_lines.append(f"    {src} --> {dst} : calls")
        for src, dst in sorted(removed_edges - edges):
            edge_lines.append(f"    {src} ..> {dst} : removed")

    # ── Assemble final output ─────────────────────────────────────────────────
    def _build_diagram(
        body: list[str],
        styles: list[str],
        del_body: list[str],
        del_styles: list[str],
        extra: list[str] | None = None,
        init: str = _INIT,
    ) -> str | None:
        if not body and not del_body:
            return None
        out = ["```mermaid", init, "classDiagram", "    direction LR"]
        out.extend(body)
        if del_body:
            out.append("")
            out.extend(del_body)
        if styles or del_styles:
            out.append("")
            out.extend(styles)
            out.extend(del_styles)
        if extra:
            out.extend(extra)
        out.append("```")
        return "\n".join(out)

    diagrams = [
        _build_diagram(d1_body, d1_styles, d1_del, d1_del_styles, edge_lines),
        _build_diagram(d2_body, d2_styles, d2_del, d2_del_styles, init=_INIT_GRID),
    ]
    return "\n\n".join(d for d in diagrams if d is not None)
