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

from .compdb import entry_file

SCHEMA_VERSION = 1
FROZEN_GROUP = "runtime_frozen_python"
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


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


def parse_scope(data: Any) -> dict[str, Any]:
    """Validate schema v1 without dropping groups, records or consumer metadata.

    Each record requires root, relative path, owner and provenance. Frozen
    records additionally require target_path, which is an import/staging identity
    rather than a source location. Empty selections are errors. Consumers may
    retain unknown groups; runners must account for them as unassessed or reject
    them, rather than omit them from selected-source coverage.
    """
    if not isinstance(data, dict):
        raise ValueError("scope must be a JSON object")
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported scope schema_version: {data.get('schema_version')!r}")
    result = copy.deepcopy(data)
    result["roots"] = _roots(result.get("roots"))
    for field in ("target", "variant", "port"):
        _text(result.get(field), field)
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
    resolver = module.ManifestFile(module.MODE_FREEZE, variables)
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
                field: getattr(output.metadata, field)
                for field in (
                    "version",
                    "description",
                    "license",
                    "author",
                    "stdlib",
                    "pypi",
                    "pypi_publish",
                )
            },
        }
        records.append(record)
    if not records:
        raise ValueError("manifest resolved no Python sources")
    return parse_scope(
        {
            "schema_version": SCHEMA_VERSION,
            "roots": roots,
            "target": target,
            "variant": variant,
            "port": port,
            "groups": {group: records},
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
        if not isinstance(entry, dict):
            raise ValueError(f"compiler database entry {index} must be an object")
        directory = Path(_text(entry.get("directory"), f"entry {index} directory"))
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError(f"entry {index} directory must be an existing absolute directory")
        _text(entry.get("file"), f"entry {index} file")
        if "arguments" in entry:
            arguments = entry["arguments"]
            if not isinstance(arguments, list) or not arguments:
                raise ValueError(f"entry {index} arguments must be a nonempty list")
            for argument in arguments:
                if not isinstance(argument, str) or "\x00" in argument:
                    raise ValueError(f"entry {index} arguments must contain strings")
        elif "command" not in entry:
            raise ValueError(f"entry {index} requires arguments or command")
        if "command" in entry:
            _text(entry["command"], f"entry {index} command")
        record = _locate_source(Path(entry_file(entry)), roots, owners)
        if not Path(entry_file(entry)).is_file():
            raise ValueError(f"entry {index} source is not a file")
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
