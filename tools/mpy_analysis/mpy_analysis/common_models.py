"""Source-justified taint model families, selected by consumer policy."""

import json
from pathlib import Path
from typing import Any, Iterable


_MODEL_ROOT = Path(__file__).parent / "data" / "pysa"
_FAMILIES = frozenset(("builtins", "socket", "execfile"))


def common_model_paths(families: Iterable[str]) -> list[Path]:
    """Return model-only directories; policy owns taint kinds and rules.

    Each family requires target-matched typing and the capabilities identified
    by its provenance. Models do not establish coverage of unmodelled APIs.
    """
    if isinstance(families, (str, bytes)):
        raise ValueError("common model families must be a collection of names")
    names = list(families)
    if any(not isinstance(name, str) or name not in _FAMILIES for name in names):
        raise ValueError(f"common model families must be selected from {sorted(_FAMILIES)}")
    if len(set(names)) != len(names):
        raise ValueError("common model family selected more than once")
    paths = [_MODEL_ROOT / name for name in names]
    for path in paths:
        if not (path / "models.pysa").is_file():
            raise FileNotFoundError(f"common model data missing: {path}")
    return paths


def common_model_provenance(families: Iterable[str]) -> list[dict[str, Any]]:
    return [
        json.loads((path / "provenance.json").read_text(encoding="utf-8"))
        for path in common_model_paths(families)
    ]
