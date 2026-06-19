"""Serialize AnalysisResult to JSON for consumption by external tools (e.g. VSCode extension).

Schema version is included in every output so consumers can detect breaking changes.
"""

import json
from dataclasses import asdict

from codiff.schema.diff import AnalysisResult

SCHEMA_VERSION = "1.0"


def render_json(
    result: AnalysisResult,
    base_ref: str = "HEAD",
    head_ref: str = "working tree",
    indent: int = 2,
) -> str:
    """Return a JSON string representation of *result*.

    Top-level structure:
    {
      "schema_version": "1.0",
      "base_ref": "HEAD",
      "head_ref": "working tree",
      "summary": { ... },
      "added":    [ { function_id, file_path, class_name, is_entry_point,
                      new_callers, existing_callers, new_calls, existing_calls } ],
      "modified": [ { function_id, file_path, class_name, signature_changed,
                      old_params, new_params, old_return_type, new_return_type,
                      calls_added_new, calls_added_existing, calls_removed,
                      callers } ],
      "removed":  [ { function_id, file_path, class_name, was_called_by } ]
    }
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "base_ref": base_ref,
        "head_ref": head_ref,
        **asdict(result),
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)
