"""Explicit selected-source inventories and MicroPython manifest collection.

Build manifests are trusted executable inputs. Only the named MicroPython
checkout's tools/manifestfile.py is loaded; no consumer resolver is imported.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from .compdb import entry_file, validate_entry

SCHEMA_VERSION = 1
FROZEN_GROUP = "runtime_frozen_python"
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_PACKAGE_METADATA_FIELDS = (
    "version",
    "description",
    "license",
    "author",
    "stdlib",
    "pypi",
    "pypi_publish",
)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _relative_path(value: Any, label: str) -> str:
    value = _text(value, label)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{label} must be a relative POSIX path without traversal: {value}")
    return value


def _roots(values: Any) -> dict[str, str]:
    if not isinstance(values, dict) or not values:
        raise ValueError("roots must be a nonempty mapping of names to absolute directories")
    result = {}
    for name, value in values.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"invalid root name: {name!r}")
        path = Path(_text(value, f"root {name}"))
        if not path.is_absolute():
            raise ValueError(f"root {name} must be absolute: {path}")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f"root {name} is not a directory: {path}")
        result[name] = str(path)
    return result


def source_path(scope: Mapping[str, Any], record: Mapping[str, Any]) -> Path:
    """Return the physical file, rejecting escapes including symlink escapes."""
    root_name = _text(record.get("root"), "source root")
    if root_name not in scope["roots"]:
        raise ValueError(f"unknown source root: {root_name!r}")
    relative = _relative_path(record.get("path"), "source path")
    root = Path(scope["roots"][root_name])
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError(f"source escapes root {root_name}: {relative}") from None
    if not path.is_file():
        raise ValueError(f"source is not a file: {path}")
    return path


def source_identity(record: Mapping[str, Any]) -> str:
    """Human-readable physical selection identity, independent of frozen imports."""
    return f"{record['root']}/{record['path']}"


def report_path(scope: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    """Actual source location for report URIs, never an analysis staging path."""
    return str(source_path(scope, record))


def _validate_packages(scope: Mapping[str, Any]) -> set[str]:
    packages = scope.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError("packages must contain a list")
    indexed = {}
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("package must be an object")
        package_id = _text(package.get("id"), "package id")
        if package_id in indexed:
            raise ValueError(f"duplicate package id: {package_id}")
        _text(package.get("name"), "package name")
        source_path(scope, package)
        if "library" not in package:
            raise ValueError("package library is required")
        if package["library"] is not None:
            _text(package["library"], "package library")
        metadata = package.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("package metadata must be an object")
        for field in _PACKAGE_METADATA_FIELDS:
            if field not in metadata:
                continue
            value = metadata[field]
            if field == "stdlib":
                if type(value) is not bool:
                    raise ValueError("package metadata stdlib must be a boolean")
            elif value is not None and not isinstance(value, str):
                raise ValueError(f"package metadata {field} must be a string or null")
        dependencies = package.get("dependencies")
        if not isinstance(dependencies, list):
            raise ValueError("package dependencies must contain a list")
        for dependency in dependencies:
            _text(dependency, "package dependency")
        indexed[package_id] = package
    for package in packages:
        for dependency in package["dependencies"]:
            if dependency not in indexed:
                raise ValueError(f"unknown package dependency: {dependency}")
    requirements = scope.get("package_requirements", [])
    if not isinstance(requirements, list):
        raise ValueError("package_requirements must contain a list")
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise ValueError("package requirement must be an object")
        if "parent" not in requirement:
            raise ValueError("package requirement parent is required")
        parent = requirement["parent"]
        if parent is not None:
            _text(parent, "package requirement parent")
            if parent not in indexed:
                raise ValueError(f"unknown package requirement parent: {parent}")
        package_id = _text(requirement.get("package"), "package requirement package")
        if package_id not in indexed:
            raise ValueError(f"unknown package requirement package: {package_id}")
        if parent is not None and package_id not in indexed[parent]["dependencies"]:
            raise ValueError(f"package requirement missing from parent dependencies: {package_id}")
    return set(indexed)


def parse_scope(data: Any) -> dict[str, Any]:
    """Validate schema v1 without dropping groups, records or consumer metadata.

    Each record requires root, relative path, owner and provenance. Frozen
    records additionally require target_path, which is an import/staging identity
    rather than a source location. Empty selections are errors. Consumers may
    retain unknown groups; runners must account for them as unassessed or reject
    them, rather than omit them from selected-source coverage.
    Optional packages identify required manifests. Dependency and requirement
    edges, and source package_id provenance, must reference declared packages.
    """
    if not isinstance(data, dict):
        raise ValueError("scope must be a JSON object")
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported scope schema_version: {data.get('schema_version')!r}")
    result = copy.deepcopy(data)
    result["roots"] = _roots(result.get("roots"))
    for field in ("target", "variant", "port"):
        _text(result.get(field), field)
    package_ids = _validate_packages(result)
    groups = result.get("groups")
    if not isinstance(groups, dict):
        raise ValueError("groups must be a mapping of names to source lists")
    count = 0
    for name, records in groups.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"invalid group name: {name!r}")
        if not isinstance(records, list):
            raise ValueError(f"group {name} must contain a list")
        targets = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"group {name} source must be an object")
            _text(record.get("root"), "source root")
            _text(record.get("owner"), "source owner")
            physical = source_path(result, record)
            if physical.suffix.lower() == ".pyi":
                raise ValueError(f"stub dependencies are not selected sources: {physical}")
            provenance = record.get("provenance")
            if not (
                isinstance(provenance, str)
                and provenance.strip()
                or isinstance(provenance, dict)
                and provenance
            ):
                raise ValueError(f"source provenance is required: {source_identity(record)}")
            if isinstance(provenance, dict) and "package_id" in provenance:
                package_id = _text(provenance["package_id"], "source package_id")
                if package_id not in package_ids:
                    raise ValueError(f"unknown source package_id: {package_id}")
            if name == FROZEN_GROUP and "target_path" not in record:
                raise ValueError(f"frozen source requires target_path: {source_identity(record)}")
            if "target_path" in record:
                target = _relative_path(record["target_path"], "target_path")
                if target in targets and targets[target] != physical:
                    raise ValueError(f"staged target collision in {name}: {target}")
                targets[target] = physical
            count += 1
        for target in targets:
            if any(parent.as_posix() in targets for parent in PurePosixPath(target).parents):
                raise ValueError(f"staged file/directory collision in {name}: {target}")
    if not count:
        raise ValueError("source selection is empty")
    return result


def load_scope(path: Path) -> dict[str, Any]:
    """Read a consumer-supplied JSON scope; roots must be absolute."""
    return parse_scope(json.loads(Path(path).read_text(encoding="utf-8")))


def _locate_source(
    path: Path, roots: Mapping[str, str], owners: Mapping[str, str]
) -> dict[str, str]:
    physical = path.resolve(strict=True)
    matches = []
    for name, value in roots.items():
        root = Path(value)
        try:
            relative = physical.relative_to(root)
        except ValueError:
            continue
        matches.append((len(root.parts), name, relative.as_posix()))
    if not matches:
        raise ValueError(f"selected source has no explicit root: {physical}")
    # Nested library/consumer roots take precedence over their containing checkout.
    depth = max(match[0] for match in matches)
    nearest = [match for match in matches if match[0] == depth]
    if len(nearest) != 1:
        raise ValueError(f"selected source has ambiguous roots: {physical}")
    _, name, relative = nearest[0]
    owner = _text(owners.get(name), f"owner for root {name}")
    return {"root": name, "path": relative, "owner": owner}


def collect_manifest_scope(
    micropython_root: Path,
    manifests: Sequence[Path],
    *,
    roots: Mapping[str, str],
    path_vars: Mapping[str, str],
    owners: Mapping[str, str],
    target: str,
    variant: str,
    port: str,
    group: str = FROZEN_GROUP,
) -> dict[str, Any]:
    """Collect .py frozen inputs using the checkout's ManifestFile MODE_FREEZE.

    Roots and root owners are supplied by the caller. Path variables are resolved
    against the invocation working directory before manifest execution; MPY_DIR
    must name the authority checkout. Precompiled .mpy inputs fail explicitly
    because their Python source is unavailable. Repeated physical files with
    distinct frozen import identities are retained. The authority changes cwd
    while executing includes, so callers must serialize collection in a process.
    Required packages and edges are observed on the authority's selected include
    calls. Frozen ownership follows its metadata objects, including shared
    metadata across ordinary includes, rather than source-directory guesses.
    """
    roots = _roots(dict(roots))
    mpy = Path(micropython_root).resolve(strict=True)
    authority = mpy / "tools" / "manifestfile.py"
    if not authority.is_file():
        raise FileNotFoundError(f"MicroPython manifest authority not found: {authority}")
    variables = dict(path_vars)
    for name, value in variables.items():
        _text(name, "path variable name")
        _text(value, f"path variable {name}")
        variables[name] = str(Path(value).resolve())
    if "MPY_DIR" in variables and Path(variables["MPY_DIR"]).resolve() != mpy:
        raise ValueError("MPY_DIR must name the explicit MicroPython authority checkout")
    variables["MPY_DIR"] = str(mpy)
    manifest_paths = [Path(path).resolve(strict=True) for path in manifests]
    if not manifest_paths:
        raise ValueError("at least one manifest is required")
    for path in manifest_paths:
        if not path.is_file() or path.suffix != ".py":
            raise ValueError(f"manifest must be a Python file: {path}")
    spec = importlib.util.spec_from_file_location("mpy_analysis_manifest_authority", authority)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load MicroPython manifest authority: {authority}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class ObservedManifestFile(module.ManifestFile):
        """Observe authority-selected require includes and metadata ownership."""

        def __init__(self):
            super().__init__(module.MODE_FREEZE, variables)
            self.packages = {}
            self.package_requirements = []
            self.metadata_packages = {}
            self.package_metadata = {}
            self.requests = []
            self.include_packages = []

        def require(self, name, version=None, pypi=None, library=None, **kwargs):
            request = {
                "name": name,
                "library": library,
                "parent": self.metadata_packages.get(self._metadata[-1]),
                "selected": False,
            }
            self.requests.append(request)
            try:
                return super().require(name, version=version, pypi=pypi, library=library, **kwargs)
            finally:
                self.requests.pop()

        def include(self, manifest_path, is_require=False, **kwargs):
            package_id = None
            if is_require and self.requests and not self.requests[-1]["selected"]:
                request = self.requests[-1]
                request["selected"] = True
                # This path is chosen by the authority's library search, not by
                # a parallel resolver. Identity uses its physical explicit root.
                selected = Path(self._resolve_path(manifest_path))
                if selected.is_dir():
                    selected /= "manifest.py"
                location = _locate_source(selected, roots, owners)
                package_id = source_identity(location)
                if package_id not in self.packages:
                    self.packages[package_id] = {
                        "id": package_id,
                        "name": request["name"],
                        "root": location["root"],
                        "path": location["path"],
                        "library": request["library"],
                        "metadata": {},
                        "dependencies": [],
                    }
                parent = request["parent"]
                self.package_requirements.append({"parent": parent, "package": package_id})
                if parent is not None:
                    dependencies = self.packages[parent]["dependencies"]
                    if package_id not in dependencies:
                        dependencies.append(package_id)
            self.include_packages.append(package_id)
            try:
                return super().include(manifest_path, is_require=is_require, **kwargs)
            finally:
                self.include_packages.pop()

        def _manifest_globals(self, kwargs):
            if self.include_packages and self.include_packages[-1] is not None:
                package_id = self.include_packages[-1]
                metadata = self._metadata[-1]
                self.metadata_packages[metadata] = package_id
                self.package_metadata.setdefault(package_id, metadata)
            return super()._manifest_globals(kwargs)

    resolver = ObservedManifestFile()
    cwd = os.getcwd()
    try:
        for path in manifest_paths:
            resolver.execute(str(path))
    except module.ManifestFileError as exc:
        raise ValueError(f"manifest resolution failed: {exc}") from exc
    finally:
        os.chdir(cwd)
    records = []
    for output in resolver.files():
        if output.file_type != module.FILE_TYPE_LOCAL:
            raise ValueError(f"non-local frozen input is unsupported: {output.full_path}")
        physical = Path(output.full_path).resolve(strict=True)
        if physical.suffix.lower() != ".py":
            raise ValueError(f"frozen input has no assessable Python source: {physical}")
        record = _locate_source(physical, roots, owners)
        record["target_path"] = output.target_path.replace(os.sep, "/")
        record["provenance"] = {
            "kind": "micropython-manifest",
            "authority": str(authority),
            "manifests": [str(path) for path in manifest_paths],
            "path_vars": variables,
            "freeze_kind": output.kind,
            "opt": output.opt,
            "package_metadata": {
                field: getattr(output.metadata, field) for field in _PACKAGE_METADATA_FIELDS
            },
        }
        package_id = resolver.metadata_packages.get(output.metadata)
        if package_id is not None:
            record["provenance"]["package_id"] = package_id
        records.append(record)
    if not records:
        raise ValueError("manifest resolved no Python sources")
    for package_id, package in resolver.packages.items():
        metadata = resolver.package_metadata.get(package_id)
        if metadata is not None:
            package["metadata"] = {
                field: getattr(metadata, field) for field in _PACKAGE_METADATA_FIELDS
            }
    return parse_scope(
        {
            "schema_version": SCHEMA_VERSION,
            "roots": roots,
            "target": target,
            "variant": variant,
            "port": port,
            "groups": {group: records},
            "packages": list(resolver.packages.values()),
            "package_requirements": resolver.package_requirements,
        }
    )


def collect_compdb_sources(
    database: Path, *, roots: Mapping[str, str], owners: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Collect every database entry against its actual compiler working directory.

    No ownership filter or suffix filter may remove compiled sources. Multiple
    commands for one source remain distinct inventory records, with their exact
    arguments or command and directory retained under compilation.
    """
    roots = _roots(dict(roots))
    database = Path(database).resolve(strict=True)
    entries = json.loads(database.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"compiler database must contain selected entries: {database}")
    records = []
    for index, entry in enumerate(entries):
        validate_entry(entry, f"entry {index}")
        record = _locate_source(Path(entry_file(entry)), roots, owners)
        if "owner" in entry:
            record["owner"] = _text(entry["owner"], f"entry {index} owner")
        record["provenance"] = {
            "kind": "compilation-database",
            "database": str(database),
            "entry_index": index,
        }
        if "provenance" in entry:
            record["provenance"]["compiler_provenance"] = copy.deepcopy(entry["provenance"])
        record["compilation"] = copy.deepcopy(entry)
        records.append(record)
    return records


def _assignments(values: Sequence[str], label: str) -> dict[str, str]:
    result = {}
    for value in values:
        name, separator, item = value.partition("=")
        if not separator or not name or not item or name in result:
            raise ValueError(f"{label} expects unique NAME=VALUE assignments: {value}")
        result[name] = item
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--scope", type=Path, help="validate a consumer-supplied scope")
    inputs.add_argument(
        "--manifest", type=Path, action="append", help="trusted MicroPython manifest"
    )
    parser.add_argument("--micropython-root", type=Path)
    parser.add_argument("--root", action="append", default=[], metavar="NAME=ABSOLUTE_PATH")
    parser.add_argument("--owner", action="append", default=[], metavar="ROOT=OWNER")
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--target")
    parser.add_argument("--variant")
    parser.add_argument("--port")
    parser.add_argument("--compile-database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.scope:
            if any(
                (
                    args.manifest,
                    args.micropython_root,
                    args.root,
                    args.owner,
                    args.var,
                    args.target,
                    args.variant,
                    args.port,
                    args.compile_database,
                )
            ):
                raise ValueError("--scope cannot be combined with manifest collection options")
            data = load_scope(args.scope)
        else:
            if not all((args.micropython_root, args.target, args.variant, args.port)):
                raise ValueError(
                    "manifest collection requires --micropython-root, --target, --variant, --port"
                )
            owners = _assignments(args.owner, "--owner")
            data = collect_manifest_scope(
                args.micropython_root,
                args.manifest,
                roots=_assignments(args.root, "--root"),
                path_vars=_assignments(args.var, "--var"),
                owners=owners,
                target=args.target,
                variant=args.variant,
                port=args.port,
            )
            if args.compile_database:
                data["groups"]["native"] = collect_compdb_sources(
                    args.compile_database,
                    roots=data["roots"],
                    owners=owners,
                )
                data = parse_scope(data)
        rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"scope: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
