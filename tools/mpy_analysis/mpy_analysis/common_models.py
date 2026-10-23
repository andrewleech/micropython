"""Source-justified taint model families, selected by consumer policy."""

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


_MODEL_ROOT = Path(__file__).parent / "data" / "pysa"
_FAMILIES = frozenset(("input", "eval_exec", "socket", "execfile"))


def common_model_paths(families: Iterable[str]) -> list[Path]:
    """Return model-only directories; policy owns taint kinds and rules.

    Each family requires target-matched typing and the capabilities identified
    by its provenance. Models do not establish coverage of unmodelled APIs.
    """
    if isinstance(families, (str, bytes)):
        raise ValueError("common model families must be a collection of names")
    try:
        names = list(families)
    except TypeError as error:
        raise ValueError("common model families must be a collection of names") from error
    unknown = [name for name in names if not isinstance(name, str) or name not in _FAMILIES]
    if unknown:
        raise ValueError(
            f"unknown common model families {unknown!r}; must be selected from {sorted(_FAMILIES)}"
        )
    if len(set(names)) != len(names):
        raise ValueError("common model family selected more than once")
    paths = [_MODEL_ROOT / name for name in names]
    for path in paths:
        if not (path / "models.pysa").is_file():
            raise FileNotFoundError(f"common model data missing: {path}")
    return paths


def common_model_provenance(families: Iterable[str]) -> list[dict[str, Any]]:
    receipts = []
    for path in common_model_paths(families):
        receipt = json.loads((path / "provenance.json").read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError(f"common model provenance must be an object: {path}")
        receipt["model_files"] = [
            {
                "path": "models.pysa",
                "sha256": hashlib.sha256((path / "models.pysa").read_bytes()).hexdigest(),
            }
        ]
        receipts.append(receipt)
    return receipts


def _kind_names(value: Any, location: str) -> set[str]:
    if not isinstance(value, list) or any(
        not isinstance(name, str) or not name.strip() for name in value
    ):
        raise ValueError(f"{location} must be a list of nonempty kind strings")
    return set(value)


def resolve_model_policy(policy_dir: Path, families: Iterable[str]) -> dict[str, Any]:
    """Resolve model inputs and check selected kinds' ordinary-rule participation.

    The engine owns model grammar, configuration union and rule effects. Kind
    participation does not establish source reachability or target capabilities.
    """
    policy_dir = Path(policy_dir).resolve()
    if not policy_dir.is_dir():
        raise FileNotFoundError(f"model policy directory missing: {policy_dir}")
    if isinstance(families, (str, bytes)):
        raise ValueError("common model families must be a collection of names")
    try:
        names = list(families)
    except TypeError as error:
        raise ValueError("common model families must be a collection of names") from error
    shared_paths = common_model_paths(names)
    provenance = common_model_provenance(names)
    app_path = policy_dir / "pysa"
    if app_path.exists() and not app_path.is_dir():
        raise ValueError(f"application model path must be a directory: {app_path}")
    model_paths = ([app_path.resolve()] if app_path.is_dir() else []) + [
        path.resolve() for path in shared_paths
    ]
    config_paths = list(
        dict.fromkeys(
            path.resolve()
            for directory in model_paths
            for path in sorted(directory.rglob("*.config"))
            if path.is_file()
        )
    )
    if not config_paths:
        raise FileNotFoundError(f"no model configuration (*.config) found: {policy_dir}")
    if not any(
        path.is_file() for directory in model_paths for path in directory.rglob("*.pysa")
    ):
        raise FileNotFoundError(f"no taint models (*.pysa) found: {policy_dir}")

    declared = {"sources": set(), "sinks": set()}
    participating = {"sources": set(), "sinks": set()}
    unsupported_forms = set()
    for path in config_paths:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError(f"model configuration must be an object: {path}")
        for side in declared:
            declarations = config.get(side, [])
            if not isinstance(declarations, list):
                raise ValueError(f"{path}: {side} declarations must be a list")
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    raise ValueError(f"{path}: {side} declarations must be objects")
                name = declaration.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"{path}: {side} declaration requires a nonempty name")
                declared[side].add(name)
        rules = config.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError(f"{path}: rules must be a list")
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError(f"{path}: rules must be objects")
            for side in participating:
                participating[side].update(
                    _kind_names(rule.get(side), f"{path}: rules.{side}")
                )
        unsupported_forms.update(
            form
            for form in ("string_combine_rules", "combined_source_rules")
            if config.get(form)
        )

    for receipt in provenance:
        family = receipt.get("family")
        kinds = receipt.get("taint_kinds")
        if not isinstance(kinds, dict):
            raise ValueError(f"common model family {family!r} requires taint_kinds object")
        for side in declared:
            for kind in sorted(
                _kind_names(kinds.get(side), f"common model family {family!r}: {side}")
            ):
                if kind not in declared[side]:
                    raise ValueError(
                        f"common model family {family!r}: {side} kind {kind!r} is not declared"
                    )
                if kind not in participating[side]:
                    unsupported = (
                        "; selected-kind validation through "
                        + ", ".join(sorted(unsupported_forms))
                        + " is unsupported"
                        if unsupported_forms
                        else ""
                    )
                    raise ValueError(
                        f"common model family {family!r}: kind {kind!r} lacks "
                        f"ordinary rules.{side} participation{unsupported}"
                    )
    return {
        "model_paths": model_paths,
        "config_paths": config_paths,
        "common_model_provenance": provenance,
    }
