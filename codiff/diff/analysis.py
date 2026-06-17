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
    modules_touched: list[str]  # sorted distinct file paths


@dataclass
class AddedFunctionInfo:
    """A new function and how it connects to the rest of the graph."""

    function_id: str
    file_path: str
    class_name: Optional[str]
    existing_callers: list[str]  # callers that existed in base
    new_callers: list[str]  # callers that are also new functions
    existing_calls: list[str]  # callees that existed in base
    new_calls: list[str]  # callees that are also new functions
    is_entry_point: bool  # True when no callers at all


@dataclass
class ModifiedFunctionInfo:
    """An existing function whose code, calls, or signature changed."""

    function_id: str
    file_path: str
    class_name: Optional[str]
    signature_changed: bool
    old_params: list[dict]
    new_params: list[dict]
    old_return_type: Optional[str]
    new_return_type: Optional[str]
    calls_added_new: list[str]  # newly called functions that are also new
    calls_added_existing: list[str]  # newly called functions that already existed
    calls_removed: list[str]  # callees no longer called (were in base graph)
    callers: list[str]  # callers in head (context for who is affected)


@dataclass
class RemovedFunctionInfo:
    """A function that was deleted."""

    function_id: str
    file_path: str
    class_name: Optional[str]
    was_called_by: list[str]  # callers in base


@dataclass
class AnalysisResult:
    summary: SummaryStats
    added: list[AddedFunctionInfo]
    modified: list[ModifiedFunctionInfo]
    removed: list[RemovedFunctionInfo]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyze(
    diff: GraphDiff,
    base: GraphSnapshot,
    head: GraphSnapshot,
) -> AnalysisResult:
    added_ids = set(diff.added_nodes)

    base_reverse = _reverse_index(base)
    head_reverse = _reverse_index(head)

    return AnalysisResult(
        summary=_summary(diff),
        added=_added(diff, added_ids, head, head_reverse),
        modified=_modified(diff, added_ids, base, head, head_reverse),
        removed=_removed(diff, base_reverse),
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
        modules_touched=sorted(touched),
    )


def _added(
    diff: GraphDiff,
    added_ids: set[str],
    head: GraphSnapshot,
    head_reverse: dict[str, set[str]],
) -> list[AddedFunctionInfo]:
    result: list[AddedFunctionInfo] = []
    for fid, node in sorted(diff.added_nodes.items()):
        all_callers = sorted(head_reverse.get(fid, set()))
        existing_callers = [c for c in all_callers if c not in added_ids]
        new_callers = [c for c in all_callers if c in added_ids]

        resolved_calls = [c for c in node.calls if c in head.nodes]
        existing_calls = sorted(c for c in resolved_calls if c not in added_ids)
        new_calls = sorted(c for c in resolved_calls if c in added_ids)

        result.append(
            AddedFunctionInfo(
                function_id=fid,
                file_path=node.file_path,
                class_name=node.class_name,
                existing_callers=existing_callers,
                new_callers=new_callers,
                existing_calls=existing_calls,
                new_calls=new_calls,
                is_entry_point=len(all_callers) == 0,
            )
        )
    return result


def _modified(
    diff: GraphDiff,
    added_ids: set[str],
    base: GraphSnapshot,
    head: GraphSnapshot,
    head_reverse: dict[str, set[str]],
) -> list[ModifiedFunctionInfo]:
    result: list[ModifiedFunctionInfo] = []
    for fid, (old, new) in sorted(diff.modified_nodes.items()):
        sig_changed = old.parameters != new.parameters or old.return_type != new.return_type

        old_calls = set(old.calls)
        new_calls_set = set(new.calls)
        added_calls = sorted(c for c in (new_calls_set - old_calls) if c in head.nodes)
        removed_calls = sorted(c for c in (old_calls - new_calls_set) if c in base.nodes)

        result.append(
            ModifiedFunctionInfo(
                function_id=fid,
                file_path=new.file_path,
                class_name=new.class_name,
                signature_changed=sig_changed,
                old_params=old.parameters,
                new_params=new.parameters,
                old_return_type=old.return_type,
                new_return_type=new.return_type,
                calls_added_new=[c for c in added_calls if c in added_ids],
                calls_added_existing=[c for c in added_calls if c not in added_ids],
                calls_removed=removed_calls,
                callers=sorted(head_reverse.get(fid, set())),
            )
        )
    return result


def _removed(
    diff: GraphDiff,
    base_reverse: dict[str, set[str]],
) -> list[RemovedFunctionInfo]:
    result: list[RemovedFunctionInfo] = []
    for fid, node in sorted(diff.removed_nodes.items()):
        result.append(
            RemovedFunctionInfo(
                function_id=fid,
                file_path=node.file_path,
                class_name=node.class_name,
                was_called_by=sorted(base_reverse.get(fid, set())),
            )
        )
    return result
