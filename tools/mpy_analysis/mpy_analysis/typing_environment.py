"""Explicit target typing dependencies and version-specific compatibility findings."""

import ast
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
from typing import Any

from .common_models import common_model_provenance


PYREFLY_132_FINDINGS = [
    "Pyrefly 1.3.2 falls back from missing custom stdlib modules to bundled CPython typeshed (module/finder.rs:505-528); no supported no-fallback configuration is established",
]


@dataclass(frozen=True)
class TypingEnvironment:
    stub_directory: Path
    typeshed_directory: Path
    python_version: str
    python_platform: str
    prerequisite_info: dict[str, Any]

    def pyrefly_config(self, source_directory: Path) -> str:
        """Return isolated TOML without querying a host Python interpreter."""
        configuration = {
            "project-includes": [str(source_directory / "**/*.py")],
            "search-path": [str(source_directory), str(self.stub_directory)],
            "typeshed-path": str(self.typeshed_directory),
            "python-version": self.python_version,
            "python-platform": self.python_platform,
            "site-package-path": [],
            "skip-interpreter-query": True,
            "disable-search-path-heuristics": True,
            "disable-project-excludes-heuristics": True,
            "project-excludes": [],
        }
        return "".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
            for key, value in configuration.items()
        )


def _text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"typing policy requires a nonempty {key!r} string")
    return value


def _legacy_any(path: Path) -> bool:
    """Identify the constructor assignment implicated in the bootstrap panic."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "Any" for target in targets):
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "object"
                and not value.args
                and not value.keywords
            ):
                return True
    return False


def load_typing_environment(
    policy_dir: Path, port: str, *, target: str, variant: str
) -> TypingEnvironment:
    """Load external policy, inventory dependencies, and retain unverified coverage.

    A port entry names directory, typeshed, target, variant, target_package,
    python_version, python_platform, pyrefly_version, packages, and provenance. Relative paths
    are relative to policy_dir. Provenance must include firmware_version and
    source_revision; it is a consumer declaration, not a fidelity approval.
    """
    policy_dir = Path(policy_dir).resolve()
    policy = json.loads((policy_dir / "typing.json").read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("typing.json must map port names to environment objects")
    entry = policy.get(port)
    if not isinstance(entry, dict):
        raise ValueError(f"typing.json requires an explicit environment object for port {port!r}")
    for key, expected in (("target", target), ("variant", variant)):
        if entry.get(key) != expected:
            raise ValueError(
                f"typing environment {key} {entry.get(key)!r} does not match selected {expected!r}"
            )
    stub_directory = (policy_dir / _text(entry, "directory")).resolve()
    typeshed_directory = (policy_dir / _text(entry, "typeshed")).resolve()
    python_version = _text(entry, "python_version")
    if not re.fullmatch(r"3\.\d+(?:\.\d+)?", python_version):
        raise ValueError(
            "typing python_version must be an explicit 3.minor[.micro] conditional selector"
        )
    python_platform = _text(entry, "python_platform")
    pyrefly_version = _text(entry, "pyrefly_version")
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("typing policy requires provenance object")
    firmware_version = _text(provenance, "firmware_version")
    _text(provenance, "source_revision")
    expected_packages = entry.get("packages")
    if not isinstance(expected_packages, dict) or not expected_packages:
        raise ValueError("typing policy requires a nonempty package-name to exact-version mapping")
    distributions = list(importlib.metadata.distributions(path=[str(stub_directory)]))
    installed = {
        distribution.metadata["Name"]: distribution.version for distribution in distributions
    }
    canonical = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    actual = {canonical(name): version for name, version in installed.items()}
    for name, version in expected_packages.items():
        if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
            raise ValueError("typing packages must map nonempty package names to exact versions")
        if actual.get(canonical(name)) != version:
            raise ValueError(
                f"typing package {name!r}: expected {version!r}, installed {actual.get(canonical(name))!r}"
            )
    target_package = canonical(_text(entry, "target_package"))
    if target_package not in actual or "micropython-stdlib-stubs" not in actual:
        raise ValueError(
            f"typing dependencies require {target_package} and micropython-stdlib-stubs"
        )
    if target_package not in {
        canonical(name) for name in expected_packages
    } or "micropython-stdlib-stubs" not in {canonical(name) for name in expected_packages}:
        raise ValueError(
            "typing packages must explicitly pin both target and stdlib stub distributions"
        )
    required = [
        stub_directory / "micropython.pyi",
        typeshed_directory / "stdlib/builtins.pyi",
        typeshed_directory / "stdlib/typing.pyi",
    ]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"target typing dependency missing: {path}")
    inventory = []
    for role, directory in (("modules", stub_directory), ("typeshed", typeshed_directory)):
        for path in sorted(directory.rglob("*.pyi")):
            inventory.append(
                {
                    "role": role,
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    blockers = (
        list(PYREFLY_132_FINDINGS)
        if pyrefly_version == "1.3.2"
        else [
            f"Pyrefly {pyrefly_version}: supported no-fallback target typeshed compatibility has not been established",
        ]
    )
    if pyrefly_version == "1.3.2" and _legacy_any(required[2]):
        blockers.append(
            "Selected stdlib/typing.pyi declares Any = object(); Pyrefly 1.3.2 panics while bootstrapping builtins.type"
        )
    for package in (target_package, "micropython-stdlib-stubs"):
        package_base = actual[package].split(".post", 1)[0]
        if package_base != firmware_version:
            blockers.append(
                f"{package} targets {package_base}, not declared firmware {firmware_version}"
            )
    coverage_gaps = [
        "Stub package versions and hashes identify dependencies, not selected firmware API fidelity",
        "Python version/platform select stub conditionals, not MicroPython build capabilities",
        "Compiler builtins, core method availability, and optional modules require target/build behavioral verification",
    ]
    extra_gaps = entry.get("coverage_gaps", [])
    if not isinstance(extra_gaps, list) or any(
        not isinstance(gap, str) or not gap for gap in extra_gaps
    ):
        raise ValueError("typing coverage_gaps must be a list of nonempty strings")
    families = entry.get("common_models", [])
    if not isinstance(families, list):
        raise ValueError("typing common_models must be a list of opt-in family names")
    model_provenance = common_model_provenance(families)
    information = {
        "port": port,
        "target": target,
        "variant": variant,
        "target_package": target_package,
        "path": str(stub_directory),
        "typeshed_path": str(typeshed_directory),
        "python_version": python_version,
        "python_platform": python_platform,
        "expected_pyrefly_version": pyrefly_version,
        "packages": installed,
        "expected_packages": expected_packages,
        "provenance": provenance,
        "stub_files": inventory,
        "common_models": families,
        "common_model_provenance": model_provenance,
        "compatibility_blockers": blockers,
        "coverage_gaps": coverage_gaps + extra_gaps,
        "typing_coverage_complete": False,
    }
    return TypingEnvironment(
        stub_directory, typeshed_directory, python_version, python_platform, information
    )
