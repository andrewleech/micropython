"""CycloneDX inventories from the selected manifest and compiler scope.

Repository pins, selected source hashes and declared package/library versions
are separate evidence. A compilation database does not establish link closure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence
from urllib.parse import quote, urlparse

from .compdb import arguments_of, entry_file, validate_entry
from .native_sbom import selected_libraries
from .scope import FROZEN_GROUP, parse_scope, source_path


def _ref(kind: str, identity: Any) -> str:
    value = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"urn:mpy:{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


def _properties(values: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "name": f"mpy:{key}",
            "value": value if isinstance(value, str) else json.dumps(value, sort_keys=True),
        }
        for key, value in sorted(values.items())
        if value is not None
    ]


def _git(directory: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(directory), *arguments], capture_output=True, text=True
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def _purl(url: str, revision: str, name: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname == "github.com":
        path = parsed.path.strip("/").removesuffix(".git")
        return (
            f"pkg:github/{'/'.join(quote(part, safe='') for part in path.split('/'))}@{revision}"
        )
    return f"pkg:generic/{quote(name, safe='')}@{revision}?vcs_url={quote(url, safe='')}"


class _Builder:
    def __init__(self, scope: dict[str, Any]):
        self.scope = scope
        self.components: dict[str, dict[str, Any]] = {}
        self.dependencies: dict[str, set[str]] = {}
        self.repositories: dict[str, dict[str, Any] | None] = {}
        self.tracked_files: dict[str, set[str]] = {}
        self.gaps: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []

    def add(self, kind: str, identity: Any, component: dict[str, Any]) -> str:
        reference = _ref(kind, identity)
        component["bom-ref"] = reference
        self.components[reference] = component
        self.dependencies.setdefault(reference, set())
        return reference

    def repository(self, path: Path) -> dict[str, Any] | None:
        directory = path if path.is_dir() else path.parent
        key = str(directory)
        if key in self.repositories:
            return self.repositories[key]
        root = _git(directory, "rev-parse", "--show-toplevel")
        if not root:
            self.repositories[key] = None
            self.gaps.append({"kind": "repository-provenance-unavailable", "path": key})
            return None
        if root in self.repositories:
            self.repositories[key] = self.repositories[root]
            return self.repositories[root]
        revision = _git(Path(root), "rev-parse", "HEAD")
        if not revision:
            raise ValueError(f"selected repository has no revision: {root}")
        origin = _git(Path(root), "remote", "get-url", "origin")
        status = _git(Path(root), "status", "--porcelain", "--untracked-files=no")
        if status is None:
            raise ValueError(f"cannot inspect selected repository state: {root}")
        info: dict[str, Any] = {
            "directory": root,
            "revision": revision,
            "origin": origin,
            "dirty": bool(status),
        }
        parent_root = _git(Path(root).parent, "rev-parse", "--show-toplevel")
        if parent_root and parent_root != root:
            relative = Path(root).relative_to(parent_root).as_posix()
            entry = _git(Path(parent_root), "ls-tree", "HEAD", "--", relative)
            if entry:
                fields = entry.split()
                if len(fields) >= 3 and fields[1] == "commit":
                    info["parent_gitlink"] = fields[2]
                    if fields[2] != revision:
                        self.gaps.append(
                            {
                                "kind": "checkout-gitlink-mismatch",
                                "path": root,
                                "gitlink": fields[2],
                                "checkout": revision,
                            }
                        )
        component: dict[str, Any] = {
            "type": "library",
            "name": Path(root).name,
            "version": revision,
            "properties": _properties(
                {**info, "selection": "selected-source-repository", "version-source": "git-commit"}
            ),
        }
        if origin:
            component["externalReferences"] = [{"type": "vcs", "url": origin}]
            component["purl"] = _purl(origin, revision, Path(root).name)
        else:
            self.gaps.append({"kind": "repository-origin-unavailable", "path": root})
        if status:
            self.gaps.append({"kind": "dirty-selected-repository", "path": root, "status": status})
        info["ref"] = self.add("repository", [root, revision], component)
        tracked = _git(Path(root), "ls-files", "-z")
        if tracked is None:
            raise ValueError(f"cannot inspect selected repository index: {root}")
        self.tracked_files[root] = set(tracked.split("\0"))
        self.repositories[root] = info
        self.repositories[key] = info
        return info

    def file(self, group: str, record: dict[str, Any]) -> str:
        path = source_path(self.scope, record)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        identity = [record["root"], record["path"], record.get("target_path"), digest]
        component = {
            "type": "file",
            "name": record.get("target_path", record["path"]),
            "hashes": [{"alg": "SHA-256", "content": digest}],
            "properties": _properties(
                {
                    "root": record["root"],
                    "path": record["path"],
                    "owner": record["owner"],
                    "selection": group,
                    "target-path": record.get("target_path"),
                }
            ),
        }
        reference = self.add("file", identity, component)
        repository = self.repository(path)
        tracked = None
        if repository:
            relative = path.relative_to(repository["directory"]).as_posix()
            tracked = relative in self.tracked_files[repository["directory"]]
            component["properties"].extend(_properties({"git-tracked": tracked}))
            if tracked:
                self.dependencies[repository["ref"]].add(reference)
            if not tracked:
                self.gaps.append(
                    {
                        "kind": "selected-source-not-tracked",
                        "path": str(path),
                        "detail": "The file hash identifies this input; the repository revision does not identify its contents.",
                    }
                )
        self.sources.append(
            {
                "group": group,
                "root": record["root"],
                "path": record["path"],
                "target_path": record.get("target_path"),
                "component": reference,
                "repository": repository["ref"] if repository and tracked else None,
                "git_tracked": tracked,
                "provenance": record["provenance"],
                "compilation": record.get("compilation"),
            }
        )
        return reference


def generate_sbom(
    scope: dict[str, Any], *, micropython_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a CycloneDX document and selected-source coverage receipt, offline."""
    scope = parse_scope(scope)
    mpy = Path(micropython_root).resolve(strict=True)
    if not (mpy / "tools/manifestfile.py").is_file():
        raise ValueError(f"MicroPython authority checkout is required: {mpy}")
    builder = _Builder(scope)
    build_identity = {key: scope[key] for key in ("target", "variant", "port")}
    build_ref = _ref("build", build_identity)
    root_dependencies: set[str] = set()
    package_refs: dict[str, str] = {}
    for package in scope.get("packages", []):
        manifest = source_path(scope, package)
        repository = builder.repository(manifest)
        metadata = package["metadata"]
        component: dict[str, Any] = {
            "type": "library",
            "name": package["name"],
            "properties": _properties(
                {
                    "manifest": str(manifest),
                    "library": package.get("library"),
                    "package-metadata": metadata,
                    "version-source": "manifest" if metadata.get("version") else "unknown",
                    "source-revision": repository["revision"] if repository else None,
                }
            ),
        }
        if metadata.get("version"):
            component["version"] = str(metadata["version"])
        else:
            builder.gaps.append({"kind": "package-version-unavailable", "package": package["id"]})
        if repository and repository.get("origin"):
            component["externalReferences"] = [{"type": "vcs", "url": repository["origin"]}]
        package_refs[package["id"]] = builder.add(
            "package",
            [package["id"], repository["revision"] if repository else None, metadata],
            component,
        )
    if "packages" not in scope:
        builder.gaps.append(
            {
                "kind": "package-inventory-unavailable",
                "detail": "Recollect with the authoritative manifest collector; file metadata alone does not identify required packages.",
            }
        )
    for request in scope.get("package_requirements", []):
        dependency = package_refs[request["package"]]
        parent = request["parent"]
        if parent is None:
            root_dependencies.add(dependency)
        else:
            builder.dependencies[package_refs[parent]].add(dependency)
    for package in scope.get("packages", []):
        builder.dependencies[package_refs[package["id"]]].update(
            package_refs[child] for child in package["dependencies"]
        )
    frozen = scope["groups"].get(FROZEN_GROUP, [])
    native = scope["groups"].get("native", [])
    native_paths: list[Path] = []
    native_refs: dict[str, str] = {}
    for group, records in ((FROZEN_GROUP, frozen), ("native", native)):
        for record in records:
            path = source_path(scope, record)
            if group == "native":
                compilation = record.get("compilation")
                if not isinstance(compilation, dict):
                    raise ValueError(
                        f"native SBOM input requires compiler evidence: {record['root']}/{record['path']}"
                    )
                validate_entry(compilation)
                if Path(entry_file(compilation)).resolve() != path:
                    raise ValueError("compiler source identity does not match selected source")
                if not arguments_of(compilation):
                    raise ValueError("compiler command is empty")
            provenance = record["provenance"]
            if isinstance(provenance, dict) and provenance.get("kind") == "micropython-manifest":
                if Path(provenance["authority"]).resolve() != mpy / "tools/manifestfile.py":
                    raise ValueError(
                        "SBOM MicroPython root must match the selected manifest authority"
                    )
            reference = builder.file(group, record)
            package_id = provenance.get("package_id") if isinstance(provenance, dict) else None
            if group == FROZEN_GROUP and package_id:
                builder.dependencies[package_refs[package_id]].add(reference)
            else:
                root_dependencies.add(reference)
            if group == "native":
                native_paths.append(path)
                native_refs[str(path)] = reference
    if not builder.sources:
        raise ValueError("SBOM requires selected frozen sources or compiler inputs")
    for library in selected_libraries(mpy, native_paths):
        repository = builder.repository(Path(library["directory"]))
        component = {
            "type": "library",
            "name": library["name"],
            "properties": _properties(
                {
                    "directory": library["directory"],
                    "version-source": library["version_source"],
                    "version-evidence": library.get("version_evidence"),
                    "version-kind": library.get("version_kind"),
                    "version-lower-bound": library.get("version_lower_bound"),
                    "source-revision": repository["revision"] if repository else None,
                }
            ),
        }
        if library.get("version"):
            component["version"] = library["version"]
        else:
            builder.gaps.append(
                {
                    "kind": "native-release-version-unavailable",
                    "library": library["name"],
                    "detail": library.get("gaps", []),
                }
            )
        upstream = library.get("upstream") or (repository.get("origin") if repository else None)
        if upstream:
            component["externalReferences"] = [{"type": "vcs", "url": upstream}]
        reference = builder.add(
            "native-library",
            [
                library["directory"],
                library["name"],
                repository["revision"] if repository else None,
            ],
            component,
        )
        builder.dependencies[reference].update(native_refs[path] for path in library["sources"])
        root_dependencies.add(reference)
    root_dependencies.update(info["ref"] for info in builder.repositories.values() if info)
    component = {
        "type": "application",
        "name": scope["target"],
        "bom-ref": build_ref,
        "properties": _properties(
            {
                **build_identity,
                "scope": "manifest-and-compiler-selected-sources",
                "link-closure": "not-established",
            }
        ),
    }
    builder.dependencies[build_ref] = root_dependencies
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": component},
        "components": [builder.components[key] for key in sorted(builder.components)],
        "dependencies": [
            {"ref": key, "dependsOn": sorted(builder.dependencies[key])}
            for key in sorted(builder.dependencies)
        ],
    }
    unassessed = {
        group: len(records)
        for group, records in scope["groups"].items()
        if group not in (FROZEN_GROUP, "native") and records
    }
    receipt = {
        "schema_version": 1,
        **build_identity,
        "status": "generated",
        "coverage_status": "incomplete"
        if builder.gaps or unassessed
        else "selected-sources-accounted",
        "firmware_completeness": "not-established",
        "limitations": [
            "Compilation databases select compiled sources, not linked objects or header dependencies.",
            "Manifest selection does not inventory runtime-loaded or build-fetched dependencies.",
        ],
        "gaps": builder.gaps,
        "unassessed_groups": unassessed,
        "sources": builder.sources,
        "packages": scope.get("packages", []),
        "package_requirements": scope.get("package_requirements", []),
        "component_count": len(builder.components),
        "repositories": [
            builder.repositories[key]
            for key in sorted(builder.repositories)
            if builder.repositories[key] and builder.repositories[key]["directory"] == key
        ],
    }
    return document, receipt


def _protected_inputs(scope_path: Path, data: Any) -> set[Path]:
    """Identify input paths before validation, including missing files and aliases."""
    protected = {scope_path}
    if not isinstance(data, dict) or not isinstance(data.get("roots"), dict):
        return protected
    lists = []
    groups = data.get("groups")
    if isinstance(groups, dict):
        lists.extend(value for value in groups.values() if isinstance(value, list))
    if isinstance(data.get("packages"), list):
        lists.append(data["packages"])
    for records in lists:
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("root"), str):
                continue
            root = data["roots"].get(record["root"])
            path = record.get("path")
            if isinstance(root, str) and isinstance(path, str) and "\x00" not in root + path:
                protected.add((Path(root) / path).resolve())
    return protected


def _invalidate_outputs(destinations: list[Path], protected: set[Path]) -> None:
    existing_inputs = [path for path in protected if path.exists()]
    for path in destinations:
        if path.is_symlink():
            raise ValueError(f"SBOM output must not be a symlink: {path}")
        if path.resolve() in protected or (
            path.exists() and any(path.samefile(source) for source in existing_inputs)
        ):
            raise ValueError(
                "SBOM outputs must not overwrite scope, package manifests or selected sources"
            )
    for path in destinations:
        if path.exists():
            path.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--micropython-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        scope_path = args.scope.resolve()
        output = args.output_dir.resolve()
        destinations = [output / "sbom.json", output / "sbom-summary.json"]
        input_error = None
        try:
            data = json.loads(scope_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            input_error, data = exc, None
        _invalidate_outputs(destinations, _protected_inputs(scope_path, data))
        if input_error is not None:
            raise input_error
        scope = parse_scope(data)
        document, receipt = generate_sbom(scope, micropython_root=args.micropython_root)
        output.mkdir(parents=True, exist_ok=True)
        receipt["scope_file"] = str(scope_path)
        receipt["scope_sha256"] = hashlib.sha256(scope_path.read_bytes()).hexdigest()
        for path, value in zip(destinations, (document, receipt)):
            path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"SBOM: {receipt['component_count']} components; {receipt['coverage_status']}")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"sbom: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
