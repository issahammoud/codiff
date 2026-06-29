"""Loader for codiff/utils/instructions.yaml."""

from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).parent / "instructions.yaml"


@lru_cache(maxsize=1)
def load() -> dict:
    import yaml

    with open(_PATH) as f:
        return yaml.safe_load(f)
