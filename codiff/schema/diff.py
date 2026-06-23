from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Graph snapshot types
# ---------------------------------------------------------------------------


@dataclass
class NodeInfo:
    """Everything we need about a single function for diffing."""

    function_id: str
    name: str
    file_path: str
    class_name: Optional[str]
    parameters: list[dict]  # [{name, type, value}, ...]
    return_type: Optional[str]
    calls: list[str]  # resolved callee function_ids
    code: str  # used to detect implementation changes


@dataclass
class GraphSnapshot:
    """Nodes and edges of a resolved call graph at one point in time."""

    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    # class_id → list of superclass names (populated from ClassChunk.superclasses)
    class_parents: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Diff types
# ---------------------------------------------------------------------------


@dataclass
class GraphDiff:
    added_nodes: dict[str, NodeInfo] = field(default_factory=dict)
    removed_nodes: dict[str, NodeInfo] = field(default_factory=dict)
    # id → (old_node, new_node)
    modified_nodes: dict[str, tuple[NodeInfo, NodeInfo]] = field(default_factory=dict)
    added_edges: set[tuple[str, str]] = field(default_factory=set)
    removed_edges: set[tuple[str, str]] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Analysis result types
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
    # class_id → superclass names, from head snapshot (for rendering inheritance arrows)
    class_parents: dict[str, list[str]] = field(default_factory=dict)
