"""Assemble selected component metadata independently of library identities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from jsonschema import FormatChecker

from .boundaries import discover_boundaries
from .declarations import load_declarations, merge_declaration, revision_matches_selected
from .metadata import collect_metadata
from .metadata_bom import valid_uri
from .scope import FROZEN_GROUP, source_path

REQUIRED = ("name", "version", "license", "license_applicability")
OPTIONAL = ("description", "author", "url", "copyright", "dependencies")

FORMATS = FormatChecker()


def _manifest_identity(
    metadata: dict[str, Any], boundary: dict[str, Any], packages: dict, scope: dict
) -> None:
    """Retain all authority assertions before resolving a distribution identity."""
    for issue in boundary.get("issues", []):
        field = issue.get("metadata_field")
        if not field or issue.get("status") != "conflicting":
            continue
        metadata["fields"].pop(field, None)
        metadata["issues"].append({**issue, "field": field})
        for identity in boundary["package_ids"]:
            package = packages[identity]
            if field not in package["metadata"]:
                continue
            manifest = source_path(scope, package)
            metadata["evidence"].append(
                {
                    "field": field,
                    "value": package["metadata"][field],
                    "parser": "authoritative-manifest",
                    "status": "known",
                    "path": str(manifest),
                    "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                }
            )
    if metadata["fields"].get("name") or not boundary["package_ids"]:
        return
    if any(
        item.get("field") == "name" and item.get("status") == "conflicting"
        for item in metadata["issues"]
    ):
        return
    names = sorted({packages[identity]["name"] for identity in boundary["package_ids"]})
    for identity in boundary["package_ids"]:
        package = packages[identity]
        manifest = source_path(scope, package)
        metadata["evidence"].append(
            {
                "field": "name",
                "value": package["name"],
                "parser": "authoritative-require-selection",
                "status": "known",
                "requested_names": names,
                "path": str(manifest),
                "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            }
        )
    if len(names) == 1:
        metadata["fields"]["name"] = names[0]
    else:
        metadata["issues"].append(
            {
                "field": "name",
                "status": "conflicting",
                "reason": "Multiple required names do not establish one distribution identity.",
                "values": names,
            }
        )


def _dependency_component(
    metadata: dict,
    boundary: dict,
    declaration: dict | None,
    repository: dict | None,
    properties: Any,
) -> dict:
    """Render resolved facts and retain their discovery/manual provenance."""
    fields = metadata["fields"]
    component = {
        "type": declaration["component"].get("type", "library") if declaration else "library",
        "name": fields.get("name", "unresolved:" + boundary["id"]),
        "properties": properties(
            {
                "boundary": boundary["id"],
                "boundary-kind": boundary["boundary_kind"],
                "directory": boundary["directory"],
                "source-revision": repository["revision"] if repository else None,
                "metadata-evidence": metadata["evidence"],
                "metadata-issues": metadata["issues"],
                "declared-dependencies": metadata.get("declared_dependencies", []),
            }
        ),
    }
    for field in ("version", "description", "author", "copyright"):
        if fields.get(field):
            component[field] = fields[field]
    if fields.get("license"):
        component["licenses"] = [{"expression": fields["license"]}]
    if metadata.get("license_texts"):
        component["evidence"] = {
            "licenses": [
                {
                    "license": {
                        "name": identifier,
                        "text": {"contentType": "text/plain", "content": item["text"]},
                    }
                }
                for identifier, item in sorted(metadata["license_texts"].items())
                if item["text"].strip()
            ]
        }
    references = []
    if repository and repository.get("vcs_url"):
        references.append({"type": "vcs", "url": repository["vcs_url"]})
    if fields.get("url") and valid_uri(fields["url"]):
        references.append({"type": "website", "url": fields["url"]})
    if declaration:
        for reference in declaration["component"].get("externalReferences", []):
            if reference not in references:
                references.append(reference)
        for key in ("supplier", "publisher", "purl", "cpe", "group"):
            if declaration["component"].get(key):
                component[key] = declaration["component"][key]
        component["properties"].extend(
            properties({"manual-declared-relationships": declaration["declared_relationships"]})
        )
    if references:
        component["externalReferences"] = references
    return component


def assemble(
    builder: Any, scope: dict[str, Any], declaration_files: list[Path], properties: Any
) -> dict[str, Any]:
    declarations, declaration_issues = load_declarations(scope, declaration_files)
    boundaries = discover_boundaries(scope, declared_boundaries=declarations)
    for boundary in boundaries:
        for candidate in boundary.get("candidates", []):
            if candidate.get("invalid_input"):
                raise ValueError(
                    f"invalid selected upstream BOM {candidate['path']}: "
                    f"{candidate['invalid_input']}"
                )
    packages = {package["id"]: package for package in scope.get("packages", [])}
    represented = {identity for boundary in boundaries for identity in boundary["package_ids"]}
    for identity, package in packages.items():
        if identity not in represented:
            manifest = source_path(scope, package)
            relative = manifest.parent.relative_to(
                Path(scope["roots"][package["root"]]).resolve()
            ).as_posix()
            boundaries.append(
                {
                    "id": identity,
                    "root": package["root"],
                    "path": relative,
                    "directory": str(manifest.parent),
                    "sources": [],
                    "package_ids": [identity],
                    "manifest_metadata": package["metadata"],
                    "boundary_kind": "required-manifest",
                    "issues": [
                        {
                            "status": "missing",
                            "reason": "required package has no proven selected file ownership",
                        }
                    ],
                }
            )
    source_refs = {}
    for row in builder.sources:
        key = (row["group"], row["root"], row["path"], row.get("target_path"))
        source_refs.setdefault(key, set()).add(row["component"])
    components = []
    package_refs = {}
    selected_refs = set()
    used_declarations = set()
    metadata_inputs = set()
    binding_paths = sorted(
        {
            source_path(scope, record)
            for group in (FROZEN_GROUP, "native")
            for record in scope["groups"].get(group, [])
        }
    )
    for boundary in boundaries:
        directory = Path(boundary["directory"])
        files = sorted({source_path(scope, record) for _, record in boundary["sources"]})
        outside = [path for path in files if not path.is_relative_to(directory)]
        repository = builder.repository(
            directory, declared_root=Path(scope["roots"][boundary["root"]])
        )
        metadata = collect_metadata(
            directory,
            manifest_metadata=boundary.get("manifest_metadata"),
            selected_files=[path for path in files if path.is_relative_to(directory)],
            binding_files=[path for path in binding_paths if path.is_relative_to(directory)],
            source_context={
                "root": boundary["root"],
                "root_directory": scope["roots"][boundary["root"]],
                "revision": repository["revision"]
                if repository and revision_matches_selected(scope, directory)
                else None,
            },
        )
        for path in metadata.get("markers", []):
            metadata_inputs.add(str(Path(path).resolve()))
        _manifest_identity(metadata, boundary, packages, scope)
        declaration = next(
            (item for item in declarations if Path(item["directory"]) == directory), None
        )
        if declaration:
            metadata = merge_declaration(metadata, declaration)
            used_declarations.add(str(directory))
            metadata_inputs.add(declaration["evidence"]["path"])
        if outside:
            metadata["fields"].pop("license_applicability", None)
            metadata["issues"].append(
                {
                    "field": "license_applicability",
                    "status": "missing",
                    "reason": "Authority-owned files outside the package directory need separate licence applicability evidence.",
                    "paths": [str(path) for path in outside],
                }
            )
        url = metadata["fields"].get("url")
        if url and (not isinstance(url, str) or not FORMATS.conforms(url, "iri-reference")):
            metadata["fields"].pop("url", None)
            metadata["issues"].append(
                {
                    "field": "url",
                    "status": "unsupported",
                    "reason": "Metadata URL is not a valid IRI reference.",
                    "value": url,
                }
            )
        fields = metadata["fields"]
        component = _dependency_component(metadata, boundary, declaration, repository, properties)
        manifests = [
            str(source_path(scope, packages[identity])) for identity in boundary["package_ids"]
        ]
        if manifests:
            component["properties"].extend(
                properties(
                    {
                        "manifest": manifests[0],
                        "package-metadata": boundary.get("manifest_metadata", {}),
                    }
                )
            )
        ref = builder.add("dependency", [str(directory), boundary["id"]], component)
        selected_refs.add(ref)
        for identity in boundary["package_ids"]:
            package_refs[identity] = ref
        for group, record in boundary["sources"]:
            key = (group, record["root"], record["path"], record.get("target_path"))
            builder.dependencies[ref].update(source_refs[key])
            for row in builder.sources:
                row_key = (row["group"], row["root"], row["path"], row.get("target_path"))
                if row_key == key:
                    row["dependency_component"] = ref
        matrix = {}
        extra_fields = sorted(
            {
                item.get("field")
                for item in metadata["issues"] + metadata["evidence"]
                if item.get("field")
            }
            - set(REQUIRED + OPTIONAL)
        )
        for field in REQUIRED + OPTIONAL + tuple(extra_fields):
            issues = [item for item in metadata["issues"] if item.get("field") == field]
            known = (
                metadata.get("declared_dependencies")
                if field == "dependencies"
                else fields.get(field)
            )
            has_known = bool(known)
            if field == "dependencies" or field in extra_fields:
                has_known = has_known or any(
                    item.get("field") == field and item.get("status") == "known"
                    for item in metadata["evidence"]
                )
            status = "known" if has_known else "missing"
            if any(item.get("status") == "conflicting" for item in issues):
                status = "conflicting"
            elif any(item.get("status") == "stale" for item in issues):
                status = "stale"
            elif not has_known and issues:
                status = issues[0].get("status", "unsupported")
            matrix[field] = {
                "status": status,
                "required": field in REQUIRED,
                "evidence": [item for item in metadata["evidence"] if item.get("field") == field],
                "issues": issues,
            }
            if field in REQUIRED and status != "known":
                builder.gaps.append(
                    {
                        "kind": "dependency-metadata-unresolved",
                        "component": ref,
                        "boundary": boundary["id"],
                        "field": field,
                        "status": status,
                        "evidence": matrix[field]["evidence"],
                        "issues": issues,
                        "remediation": "Fix upstream declarative metadata or supply a pinned application dependency declaration.",
                    }
                )
        matrix["source_content"] = {
            "status": "known" if boundary["sources"] else "not-applicable",
            "required": False,
            "evidence": [
                {
                    "root": row["root"],
                    "path": row["path"],
                    "sha256": row["sha256"],
                    "component": row["component"],
                }
                for row in builder.sources
                if row.get("dependency_component") == ref
            ],
        }
        matrix["source_revision"] = {
            "status": "known" if repository else "missing",
            "required": False,
            "value": repository["revision"] if repository else None,
            "applicability": "base-checkout",
        }
        boundary_issues = boundary.get("issues", [])
        for issue in boundary_issues:
            builder.gaps.append(
                {
                    "kind": "dependency-boundary-unresolved",
                    "component": ref,
                    "boundary": boundary["id"],
                    **issue,
                }
            )
        for issue in metadata["issues"]:
            if not issue.get("field") or (
                issue.get("field") not in REQUIRED
                and issue.get("status") in {"conflicting", "stale"}
            ):
                builder.gaps.append(
                    {"kind": "dependency-evidence-unresolved", "component": ref, **issue}
                )
        components.append(
            {
                "component": ref,
                "boundary": boundary["id"],
                "root": boundary["root"],
                "path": boundary["path"],
                "boundary_kind": boundary["boundary_kind"],
                "fields": matrix,
                "boundary_issues": boundary_issues,
                "evidence": metadata["evidence"],
                "issues": metadata["issues"],
                "declared_dependencies": metadata.get("declared_dependencies", []),
            }
        )
    for declaration in declarations:
        if declaration["directory"] not in used_declarations:
            declaration_issues.append(
                {
                    "kind": "declaration-applicability",
                    "status": "unmatched",
                    "directory": declaration["directory"],
                    "path": declaration["evidence"]["path"],
                }
            )
    builder.gaps.extend(declaration_issues)
    for package in packages.values():
        builder.dependencies[package_refs[package["id"]]].update(
            package_refs[identity] for identity in package["dependencies"]
        )
    roots = set(selected_refs)
    direct = {
        package_refs[item["package"]]
        for item in scope.get("package_requirements", [])
        if item["parent"] is None
    }
    for requirement in scope.get("package_requirements", []):
        if requirement["parent"] is not None:
            child = package_refs[requirement["package"]]
            parent = package_refs[requirement["parent"]]
            builder.dependencies[parent].add(child)
            if child != parent and child not in direct:
                roots.discard(child)
    complete = (
        all(
            item["fields"][field]["status"] == "known" for item in components for field in REQUIRED
        )
        and not declaration_issues
        and not any(
            issue.get("status") in {"conflicting", "stale"}
            for item in components
            for issue in item["issues"]
        )
    )
    boundary_complete = not any(item["boundary_issues"] for item in components)
    return {
        "roots": roots,
        "metadata": components,
        "metadata_inputs": sorted(metadata_inputs),
        "declarations": [
            {
                "directory": item["directory"],
                "evidence": item["evidence"],
                "component": item["component"],
                "declared_relationships": item["declared_relationships"],
            }
            for item in declarations
        ],
        "metadata_completeness": "complete" if complete else "incomplete",
        "boundary_resolution": "resolved" if boundary_complete else "incomplete",
    }
