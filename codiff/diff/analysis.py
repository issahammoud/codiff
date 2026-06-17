"""Derive structural facts from a GraphDiff.

Pure: takes GraphDiff + base/head GraphSnapshots, returns AnalysisResult.
No I/O, no DB access. Every fact is computed deterministically.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from codiff.diff.differ import GraphDiff
from codiff.diff.snapshot import GraphSnapshot

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SummaryStats:
    added_functions: int
    removed_functions: int
    modified_functions: int
    added_edges: int
    removed_edges: int
    modules_touched: list[str]  # sorted distinct file paths


@dataclass
class ChainInsertion:
    """A new node was inserted on the path between two previously adjacent nodes."""

    new_node_id: str
    predecessor_id: str
    successor_id: str


@dataclass
class WiringFacts:
    new_edges: list[tuple[str, str]]
    removed_edges: list[tuple[str, str]]
    chain_insertions: list[ChainInsertion]


@dataclass
class BlastFacts:
    """Callers / callees of changed nodes that were NOT themselves changed."""

    # changed_node_id → sorted list of caller_ids not in the changed set
    upstream_callers: dict[str, list[str]]
    # changed_node_id → list of callee_ids not in the changed set
    downstream_callees: dict[str, list[str]]


@dataclass
class LivenessFacts:
    dead_on_arrival: list[str]  # new functions with 0 callers in head
    newly_orphaned: list[str]  # existing functions that lost all callers


@dataclass
class SignatureChange:
    function_id: str
    old_params: list[dict]
    new_params: list[dict]
    old_return_type: Optional[str]
    new_return_type: Optional[str]
    unreconciled_callers: list[str]  # callers in head that were NOT modified


@dataclass
class SignatureFacts:
    changes: list[SignatureChange]


@dataclass
class BoundaryFacts:
    """New call edges that cross module (file) boundaries."""

    new_cross_module_edges: list[tuple[str, str]]  # (caller_id, callee_id)


@dataclass
class AnalysisResult:
    summary: SummaryStats
    wiring: WiringFacts
    blast: BlastFacts
    liveness: LivenessFacts
    boundaries: BoundaryFacts
    signatures: SignatureFacts
    flags: list[str]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

HIGH_FAN_IN_THRESHOLD = 5


def analyze(
    diff: GraphDiff,
    base: GraphSnapshot,
    head: GraphSnapshot,
) -> AnalysisResult:
    changed_ids = set(diff.added_nodes) | set(diff.removed_nodes) | set(diff.modified_nodes)

    head_reverse = _reverse_index(head)
    base_in_deg = {fid: len(callers) for fid, callers in _reverse_index(base).items()}
    head_in_deg = {fid: len(callers) for fid, callers in head_reverse.items()}

    return AnalysisResult(
        summary=_summary(diff),
        wiring=_wiring(diff, base),
        blast=_blast(diff, changed_ids, head, head_reverse),
        liveness=_liveness(diff, head, base_in_deg, head_in_deg),
        boundaries=_boundaries(diff, head),
        signatures=_signatures(diff, head_reverse, changed_ids),
        flags=_flags(diff, base_in_deg),
    )


# ---------------------------------------------------------------------------
# Section computations
# ---------------------------------------------------------------------------


def _reverse_index(snapshot: GraphSnapshot) -> dict[str, set[str]]:
    """Build callee_id → {caller_ids}."""
    rev: dict[str, set[str]] = defaultdict(set)
    for caller_id, callee_id in snapshot.edges:
        rev[callee_id].add(caller_id)
    return dict(rev)


def _summary(diff: GraphDiff) -> SummaryStats:
    touched: set[str] = set()
    for node in diff.added_nodes.values():
        touched.add(node.file_path)
    for node in diff.removed_nodes.values():
        touched.add(node.file_path)
    for old, new in diff.modified_nodes.values():
        touched.add(old.file_path)
        touched.add(new.file_path)

    return SummaryStats(
        added_functions=len(diff.added_nodes),
        removed_functions=len(diff.removed_nodes),
        modified_functions=len(diff.modified_nodes),
        added_edges=len(diff.added_edges),
        removed_edges=len(diff.removed_edges),
        modules_touched=sorted(touched),
    )


def _wiring(diff: GraphDiff, base: GraphSnapshot) -> WiringFacts:
    # Index added edges for fast predecessor / successor lookup
    new_in: dict[str, set[str]] = defaultdict(set)  # callee → callers (added edges)
    new_out: dict[str, set[str]] = defaultdict(set)  # caller → callees (added edges)
    for caller, callee in diff.added_edges:
        new_in[callee].add(caller)
        new_out[caller].add(callee)

    # Chain insertion: new node N with A→N and N→B in head, where A→B existed in base
    chain_insertions: list[ChainInsertion] = []
    for new_id in diff.added_nodes:
        for pred in new_in.get(new_id, set()):
            for succ in new_out.get(new_id, set()):
                if (pred, succ) in base.edges:
                    chain_insertions.append(
                        ChainInsertion(
                            new_node_id=new_id,
                            predecessor_id=pred,
                            successor_id=succ,
                        )
                    )

    return WiringFacts(
        new_edges=sorted(diff.added_edges),
        removed_edges=sorted(diff.removed_edges),
        chain_insertions=chain_insertions,
    )


def _blast(
    diff: GraphDiff,
    changed_ids: set[str],
    head: GraphSnapshot,
    head_reverse: dict[str, set[str]],
) -> BlastFacts:
    upstream: dict[str, list[str]] = {}
    downstream: dict[str, list[str]] = {}

    for fid in changed_ids:
        if fid in diff.removed_nodes:
            continue

        callers = sorted(c for c in head_reverse.get(fid, set()) if c not in changed_ids)
        if callers:
            upstream[fid] = callers

        node = head.nodes.get(fid)
        if node:
            callees = [c for c in node.calls if c in head.nodes and c not in changed_ids]
            if callees:
                downstream[fid] = callees

    return BlastFacts(
        upstream_callers=upstream,
        downstream_callees=downstream,
    )


def _liveness(
    diff: GraphDiff,
    head: GraphSnapshot,
    base_in_deg: dict[str, int],
    head_in_deg: dict[str, int],
) -> LivenessFacts:
    dead_on_arrival = sorted(fid for fid in diff.added_nodes if head_in_deg.get(fid, 0) == 0)
    newly_orphaned = sorted(
        fid
        for fid in head.nodes
        if fid not in diff.added_nodes
        and base_in_deg.get(fid, 0) > 0
        and head_in_deg.get(fid, 0) == 0
    )
    return LivenessFacts(
        dead_on_arrival=dead_on_arrival,
        newly_orphaned=newly_orphaned,
    )


def _boundaries(diff: GraphDiff, head: GraphSnapshot) -> BoundaryFacts:
    cross: list[tuple[str, str]] = []
    for caller_id, callee_id in diff.added_edges:
        caller = head.nodes.get(caller_id)
        callee = head.nodes.get(callee_id)
        if caller and callee and caller.file_path != callee.file_path:
            cross.append((caller_id, callee_id))
    return BoundaryFacts(new_cross_module_edges=sorted(cross))


def _signatures(
    diff: GraphDiff,
    head_reverse: dict[str, set[str]],
    changed_ids: set[str],
) -> SignatureFacts:
    changes: list[SignatureChange] = []
    for fid, (old, new) in diff.modified_nodes.items():
        if old.parameters == new.parameters and old.return_type == new.return_type:
            continue
        unreconciled = sorted(c for c in head_reverse.get(fid, set()) if c not in changed_ids)
        changes.append(
            SignatureChange(
                function_id=fid,
                old_params=old.parameters,
                new_params=new.parameters,
                old_return_type=old.return_type,
                new_return_type=new.return_type,
                unreconciled_callers=unreconciled,
            )
        )
    return SignatureFacts(changes=changes)


def _flags(
    diff: GraphDiff,
    base_in_deg: dict[str, int],
) -> list[str]:
    flags: list[str] = []
    for fid in diff.modified_nodes:
        deg = base_in_deg.get(fid, 0)
        if deg >= HIGH_FAN_IN_THRESHOLD:
            flags.append(f"{fid} has {deg} callers in base — high-fan-in edit")
    return sorted(flags)
