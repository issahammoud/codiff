import json

from codiff.export.json import SCHEMA_VERSION, render_json
from codiff.schema.diff import AnalysisResult, SummaryStats


def _result(*, added=0, removed=0, modified=0):
    return AnalysisResult(
        summary=SummaryStats(
            added_functions=added,
            removed_functions=removed,
            modified_functions=modified,
            modules_touched=[],
        ),
        added=[],
        modified=[],
        removed=[],
    )


class TestRenderJson:
    def test_returns_valid_json(self):
        assert isinstance(json.loads(render_json(_result())), dict)

    def test_schema_version_present(self):
        data = json.loads(render_json(_result()))
        assert data["schema_version"] == SCHEMA_VERSION

    def test_default_refs(self):
        data = json.loads(render_json(_result()))
        assert data["base_ref"] == "HEAD"
        assert data["head_ref"] == "working tree"

    def test_custom_refs(self):
        data = json.loads(render_json(_result(), base_ref="v1.0", head_ref="v2.0"))
        assert data["base_ref"] == "v1.0"
        assert data["head_ref"] == "v2.0"

    def test_sections_present(self):
        data = json.loads(render_json(_result()))
        for key in ("added", "modified", "removed", "summary"):
            assert key in data

    def test_summary_counts_serialized(self):
        data = json.loads(render_json(_result(added=3, removed=1, modified=2)))
        assert data["summary"]["added_functions"] == 3
        assert data["summary"]["removed_functions"] == 1
        assert data["summary"]["modified_functions"] == 2
