"""CycloneDX inventories from the selected manifest and compiler scope.

Repository pins, selected source hashes and declared package/library versions
are separate evidence. A compilation database does not establish link closure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote, urlparse

from .compdb import arguments_of, entry_file, validate_entry
from .firmware_inventory import load_posthoc_link_inventory
from .firmware_receipt import load_firmware_receipt, validate_firmware_receipt
from .generic_inventory import assemble
from .metadata import collect_metadata
from .metadata_bom import valid_uri, validate_cyclonedx
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


def _vcs_url(origin: str) -> str | None:
    """Return a standards-valid remote URI without losing the raw Git origin."""
    candidate = origin
    if "://" not in origin:
        scp = re.fullmatch(r"((?:[^/@:\s]+@)?(?:\[[^\]]+\]|[^/:\s]+)):(.+)", origin)
        if not scp:
            return None
        authority, path = scp.groups()
        candidate = f"ssh://{authority}/{quote(path, safe='/~')}"
    return candidate if valid_uri(candidate) else None


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

    def repository(self, path: Path, *, declared_root: Path | None = None) -> dict[str, Any] | None:
        source = path.resolve(strict=True)
        directory = path if path.is_dir() else path.parent
        declared = declared_root.resolve(strict=True) if declared_root is not None else None
        if declared is not None and not source.is_relative_to(declared):
            raise ValueError(f"selected source is outside its declared repository root: {path}")
        git_root = _git(directory, "rev-parse", "--show-toplevel")
        if git_root and source.is_relative_to(Path(git_root).resolve()):
            root = str(Path(git_root).resolve())
        elif git_root and declared is not None:
            root = str(declared)
        else:
            root = git_root
        key = str(Path(root).resolve()) if root else str(directory)
        if key in self.repositories:
            return self.repositories[key]
        if not root:
            self.repositories[key] = None
            self.gaps.append({"kind": "repository-provenance-unavailable", "path": key})
            return None
        if root in self.repositories:
            self.repositories[key] = self.repositories[root]
            return self.repositories[root]
        revision = _git(Path(root), "rev-parse", "HEAD")
        if not revision:
            self.repositories[key] = None
            self.gaps.append({"kind": "repository-provenance-unavailable", "path": key})
            return None
        origin = _git(Path(root), "remote", "get-url", "origin")
        vcs_url = _vcs_url(origin) if origin else None
        status = _git(
            Path(root), "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"
        )
        if status is None:
            raise ValueError(f"cannot inspect selected repository state: {root}")
        info: dict[str, Any] = {
            "directory": root,
            "revision": revision,
            "origin": origin,
            "vcs_url": vcs_url,
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
        if vcs_url:
            component["externalReferences"] = [{"type": "vcs", "url": vcs_url}]
            component["purl"] = _purl(vcs_url, revision, Path(root).name)
        elif origin:
            self.gaps.append(
                {"kind": "repository-origin-unsupported", "path": root, "origin": origin}
            )
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
        repository = self.repository(path, declared_root=Path(self.scope["roots"][record["root"]]))
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
                "sha256": digest,
                "repository": repository["ref"] if repository and tracked else None,
                "git_tracked": tracked,
                "provenance": record["provenance"],
                "compilation": record.get("compilation"),
            }
        )
        return reference


def _attach_firmware_images(
    receipt: dict[str, Any],
    scope: dict[str, Any],
    builder: _Builder,
    root_dependencies: set[str],
) -> dict[str, Any]:
    """Add build-bound firmware subjects and their exact selected source dependencies."""
    validated = receipt
    configuration = validated["configuration"]
    for field in ("port", "target", "variant"):
        if configuration[field] != scope[field]:
            raise ValueError(
                f"firmware receipt {field} {configuration[field]!r} does not match "
                f"SBOM scope {scope[field]!r}"
            )
    for repository in validated["source_repositories"]:
        name = repository["name"]
        root = scope["roots"].get(name)
        if not root or Path(root).resolve() != Path(repository["path"]).resolve():
            raise ValueError(f"firmware receipt source root does not match SBOM root: {name}")
        revision = _git(Path(root), "rev-parse", "HEAD")
        if revision and revision != repository["revision"]:
            raise ValueError(f"firmware receipt source revision is stale: {name}")

    source_refs: dict[tuple[str, str], set[str]] = {}
    for source in builder.sources:
        source_refs.setdefault((source["root"], source["path"]), set()).add(source["component"])

    images = []
    for image in validated["images"]:
        link = image["link"]
        bindings = set()
        for row in image["object_accounting"]:
            if row["disposition"] == "contributed":
                bindings.update((item["root"], item["path"]) for item in row["source_bindings"])
        for operand in link["operands"]:
            if operand["disposition"] != "contributed":
                continue
            if operand["kind"] == "archive":
                members = (item for item in operand["members"] if item["disposition"] == "contributed")
                for member in members:
                    bindings.update(
                        (item["root"], item["path"]) for item in member["source_bindings"]
                    )
            else:
                bindings.update(
                    (item["root"], item["path"])
                    for item in operand.get("source_bindings", [])
                )
        mapped, missing = set(), []
        for binding in sorted(bindings):
            refs = source_refs.get(binding)
            if refs:
                mapped.update(refs)
            else:
                missing.append(binding)
                builder.gaps.append(
                    {
                        "kind": "linked-source-not-in-selected-scope",
                        "root": binding[0],
                        "path": binding[1],
                        "image": image["id"],
                    }
                )
        link_status = (
            "complete"
            if validated["status"] == "complete" and link["status"] == "complete" and not missing
            else "incomplete"
        )
        component = {
            "type": "firmware",
            "name": image["id"],
            "hashes": [{"alg": "SHA-256", "content": link["elf"]["sha256"]}],
            "properties": _properties(
                {
                    "build-id": validated["build_id"],
                    "receipt-id": validated["receipt_id"],
                    "image-role": image["role"],
                    "port": configuration["port"],
                    "target": configuration["target"],
                    "variant": configuration["variant"],
                    "link-closure": link_status,
                    "linker-format": link["linker_format"],
                    "linker-map-sha256": link["map"]["sha256"],
                    "lto": link["lto"],
                }
            ),
        }
        reference = builder.add(
            "firmware-image", [validated["build_id"], image["id"]], component
        )
        builder.dependencies[reference] = mapped
        root_dependencies.add(reference)
        images.append(
            {
                "id": image["id"],
                "role": image["role"],
                "elf_sha256": link["elf"]["sha256"],
                "link_closure": link_status,
                "linked_components": sorted(mapped),
                "unmapped_source_bindings": [
                    {"root": root, "path": path} for root, path in missing
                ],
            }
        )
    return {
        "schema": validated["schema"],
        "receipt_id": validated["receipt_id"],
        "build_id": validated["build_id"],
        "status": "complete"
        if all(image["link_closure"] == "complete" for image in images)
        else "incomplete",
        "images": images,
    }

def _attach_posthoc_firmware_image(
    evidence: dict[str, Any],
    scope: dict[str, Any],
    builder: _Builder,
    root_dependencies: set[str],
) -> dict[str, Any]:
    """Add an explicitly incomplete image edge from a posthoc published inventory."""
    source_refs: dict[tuple[str, str], set[str]] = {}
    for source in builder.sources:
        path = source_path(scope, source).resolve()
        source_refs.setdefault((str(path), source["sha256"]), set()).add(source["component"])

    mapped: set[str] = set()
    unmapped = []
    for contribution in evidence["contributions"]:
        key = (contribution["source"]["path"], contribution["source"]["sha256"])
        refs = source_refs.get(key)
        if refs:
            mapped.update(refs)
        else:
            unmapped.append(key)
            builder.gaps.append(
                {
                    "kind": "posthoc-linked-source-not-in-selected-scope",
                    "path": key[0],
                    "sha256": key[1],
                }
            )

    image = evidence["image"]
    build = evidence["build"]
    component = {
        "type": "firmware",
        "name": image["id"],
        "hashes": [{"alg": "SHA-256", "content": image["elf"]["sha256"]}],
        "properties": _properties(
            {
                "capture-authority": evidence["authority"],
                "generation-id": evidence["generation"]["id"],
                "inventory-sha256": evidence["generation"]["inventory_sha256"],
                "image-role": image["role"],
                "link-closure": "posthoc-incomplete",
                "linker": image["linker"]["path"],
                "linker-sha256": image["linker"]["sha256"],
                "linker-map-sha256": image["map"]["sha256"],
                "port": build["port"],
                "source-revision": build["source_revision"],
                "target": build["target"],
                "variant": build["variant"],
            }
        ),
    }
    reference = builder.add(
        "posthoc-firmware-image",
        [evidence["generation"]["id"], image["id"]],
        component,
    )
    builder.dependencies[reference] = mapped
    root_dependencies.add(reference)
    builder.gaps.append(
        {
            "kind": "posthoc-firmware-link-inventory",
            "generation": evidence["generation"]["id"],
            "unresolved_inputs": len(evidence["unresolved_inputs"]),
            "unassessed_objects": len(evidence["unassessed_objects"]),
            "reason": "posthoc link attribution does not establish event-time build or full firmware closure",
        }
    )
    return {
        "schema": evidence["schema"],
        "authority": evidence["authority"],
        "generation": evidence["generation"],
        "status": "posthoc-incomplete",
        "images": [
            {
                "id": image["id"],
                "role": image["role"],
                "elf_sha256": image["elf"]["sha256"],
                "linked_components": sorted(mapped),
                "unmapped_sources": [
                    {"path": path, "sha256": digest} for path, digest in unmapped
                ],
                "unresolved_inputs": evidence["unresolved_inputs"],
                "unassessed_objects": evidence["unassessed_objects"],
                "no_contribution_objects": evidence["no_contribution_objects"],
            }
        ],
        "limitations": evidence["limitations"],
    }

def generate_sbom(
    scope: dict[str, Any],
    *,
    micropython_root: Path,
    declaration_files: Sequence[Path] = (),
    build_receipt: Path | dict[str, Any] | None = None,
    posthoc_link_inventory: Path | None = None,
    link_generation: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return generic selected-dependency components and field-level evidence."""
    if build_receipt is not None and posthoc_link_inventory is not None:
        raise ValueError("provide either an actual build receipt or a posthoc link inventory, not both")
    if link_generation is not None and posthoc_link_inventory is None:
        raise ValueError("--link-generation requires --link-inventory")
    if posthoc_link_inventory is not None:
        posthoc_evidence = load_posthoc_link_inventory(
            posthoc_link_inventory, generation_path=link_generation
        )
    else:
        posthoc_evidence = None
    if build_receipt is None:
        firmware_evidence = None
    elif isinstance(build_receipt, Path):
        firmware_evidence = load_firmware_receipt(build_receipt)
    elif isinstance(build_receipt, dict):
        firmware_evidence = validate_firmware_receipt(build_receipt)
    else:
        raise ValueError("build_receipt must be a path or JSON object")
    scope = parse_scope(scope)
    mpy = Path(micropython_root).resolve(strict=True)
    if not (mpy / "tools/manifestfile.py").is_file():
        raise ValueError(f"MicroPython authority checkout is required: {mpy}")
    builder = _Builder(scope)
    build_identity = {key: scope[key] for key in ("target", "variant", "port")}
    build_ref = _ref("build", build_identity)
    for group in (FROZEN_GROUP, "native"):
        for record in scope["groups"].get(group, []):
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
            builder.file(group, record)
    if not builder.sources:
        raise ValueError("SBOM requires selected frozen sources or compiler inputs")
    inventory = assemble(builder, scope, list(declaration_files), _properties)
    if "packages" not in scope:
        builder.gaps.append(
            {
                "kind": "package-inventory-unavailable",
                "detail": "Recollect with the authoritative manifest collector; file metadata alone does not identify required packages.",
            }
        )
    root_dependencies = inventory.pop("roots")
    root_dependencies.update(info["ref"] for info in builder.repositories.values() if info)
    if firmware_evidence:
        link_closure = _attach_firmware_images(
            firmware_evidence, scope, builder, root_dependencies
        )
    elif posthoc_evidence:
        link_closure = _attach_posthoc_firmware_image(
            posthoc_evidence, scope, builder, root_dependencies
        )
    else:
        link_closure = None
    component = {
        "type": "application",
        "name": scope["target"],
        "bom-ref": build_ref,
        "properties": _properties(
            {
                **build_identity,
                "scope": "manifest-and-compiler-selected-sources",
                "link-closure": link_closure["status"] if link_closure else "not-established",
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
        "source_accounting": "accounted" if not unassessed else "incomplete",
        "firmware_completeness": "not-established",
        "limitations": [
            *(
                []
                if link_closure
                else ["Compilation databases select compiled sources, not linked objects."]
            ),
            "Manifest selection does not inventory runtime-loaded or build-fetched dependencies.",
            "Header/sysroot and generated-input provenance remain separate closure requirements.",
            "Unmarked vendored boundaries cannot be discovered reliably; declare selected dependencies explicitly.",
        ],
        **inventory,
        "gaps": builder.gaps,
        "unassessed_groups": unassessed,
        "sources": builder.sources,
        "link_closure": link_closure or {"status": "not-established"},
        "packages": scope.get("packages", []),
        "package_requirements": scope.get("package_requirements", []),
        "component_count": len(builder.components),
        "repositories": [
            builder.repositories[key]
            for key in sorted(builder.repositories)
            if builder.repositories[key] and builder.repositories[key]["directory"] == key
        ],
    }
    validate_cyclonedx(document)
    return document, receipt


def _protected_inputs(scope_path: Path, data: Any) -> set[Path]:
    """Identify input paths before validation, including missing files and aliases."""
    protected = {scope_path}
    if not isinstance(data, dict) or not isinstance(data.get("roots"), dict):
        return protected
    lists = []
    groups = data.get("groups")
    if isinstance(groups, dict):
        lists.extend(
            (group, records) for group, records in groups.items() if isinstance(records, list)
        )
    if isinstance(data.get("packages"), list):
        lists.append((None, data["packages"]))
    directories: dict[Path, dict[str, str]] = {}
    selected_files: set[Path] = set()
    for group, records in lists:
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("root"), str):
                continue
            root = data["roots"].get(record["root"])
            path = record.get("path")
            if isinstance(root, str) and isinstance(path, str) and "\x00" not in root + path:
                base = Path(root).resolve()
                selected = (base / path).resolve()
                protected.add(selected)
                if (
                    group in (FROZEN_GROUP, "native")
                    and selected.is_file()
                    and selected.is_relative_to(base)
                ):
                    selected_files.add(selected)
                directory = selected.parent
                while directory.is_relative_to(base):
                    if directory.is_dir():
                        directories.setdefault(
                            directory,
                            {"root": record["root"], "root_directory": str(base)},
                        )
                    if directory == base:
                        break
                    directory = directory.parent
    for directory, context in directories.items():
        try:
            collect_metadata(
                directory,
                selected_files=sorted(
                    path for path in selected_files if path.is_relative_to(directory)
                ),
                source_context={**context, "revision": _git(directory, "rev-parse", "HEAD")},
                input_paths=protected,
            )
        except (OSError, ValueError, UnicodeError, SyntaxError) as error:
            raise ValueError(
                f"cannot establish protected metadata inputs at {directory}: {error}"
            ) from error
    return protected


def _invalidate_outputs(destinations: list[Path], protected: set[Path]) -> None:
    existing_inputs = [path for path in protected if path.exists()]
    for path in destinations:
        if path.is_symlink():
            raise ValueError(f"SBOM output must not be a symlink: {path}")
        if path.resolve() in protected or (
            path.exists() and any(path.samefile(source) for source in existing_inputs)
        ):
            raise ValueError("SBOM outputs must not overwrite consumed or referenced inputs")
    for path in destinations:
        if path.exists():
            path.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--micropython-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--dependency-metadata",
        type=Path,
        action="append",
        default=[],
        help="Source-bound CycloneDX 1.6 dependency declarations; repeatable",
    )
    parser.add_argument(
        "--build-receipt",
        type=Path,
        help="versioned actual-build/link receipt from the selected firmware build",
    )
    parser.add_argument(
        "--link-inventory",
        type=Path,
        help="qualified published linker inventory, recorded as posthoc/incomplete evidence",
    )
    parser.add_argument(
        "--link-generation",
        type=Path,
        help="generation.json companion for --link-inventory; defaults to its sibling",
    )
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
        protected = _protected_inputs(scope_path, data)
        protected.update(path.resolve() for path in args.dependency_metadata)
        for evidence_path in (args.build_receipt, args.link_inventory, args.link_generation):
            if evidence_path:
                protected.add(evidence_path.resolve())
        _invalidate_outputs(destinations, protected)
        if input_error is not None:
            raise input_error
        scope = parse_scope(data)
        document, receipt = generate_sbom(
            scope,
            micropython_root=args.micropython_root,
            declaration_files=args.dependency_metadata,
            build_receipt=args.build_receipt,
            posthoc_link_inventory=args.link_inventory,
            link_generation=args.link_generation,
        )
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
