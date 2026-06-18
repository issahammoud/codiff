"""Pure graph diff: diff two GraphSnapshots into a GraphDiff.

No I/O. Takes two GraphSnapshot objects and returns a GraphDiff describing
exactly what changed between them: added/removed/modified nodes and edges.

A node is "modified" if any of: code, parameters, return_type, calls, or
file_path changed. This includes pure implementation changes (same signature,
same call list) so blast-radius analysis sees the full change set.
"""

from codiff.schema.diff import GraphDiff, GraphSnapshot, NodeInfo


def diff_snapshots(base: GraphSnapshot, head: GraphSnapshot) -> GraphDiff:
    """Compute the structural delta between *base* and *head*."""
    base_ids = set(base.nodes)
    head_ids = set(head.nodes)

    added_ids = head_ids - base_ids
    removed_ids = base_ids - head_ids
    common_ids = base_ids & head_ids

    modified: dict[str, tuple[NodeInfo, NodeInfo]] = {}
    for fid in common_ids:
        b, h = base.nodes[fid], head.nodes[fid]
        if _node_changed(b, h):
            modified[fid] = (b, h)

    return GraphDiff(
        added_nodes={fid: head.nodes[fid] for fid in added_ids},
        removed_nodes={fid: base.nodes[fid] for fid in removed_ids},
        modified_nodes=modified,
        added_edges=head.edges - base.edges,
        removed_edges=base.edges - head.edges,
    )


def _node_changed(base: NodeInfo, head: NodeInfo) -> bool:
    return (
        base.code != head.code
        or base.parameters != head.parameters
        or base.return_type != head.return_type
        or base.calls != head.calls
        or base.file_path != head.file_path
    )
