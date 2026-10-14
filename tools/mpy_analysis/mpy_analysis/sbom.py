"""Neutral SBOM model of the selected manifest and compiler scope.

Repository pins, selected source hashes and declared package/library versions
are separate evidence. A compilation database does not establish link closure.
``generate_sbom`` returns the format-neutral model; ``sbom_spdx`` and ``sbom_cyclonedx``
write it.
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
from .deployment import IMAGE_KINDS, NOT_DECLARED, load_deployment, verify_deployment
from .firmware_inventory import load_posthoc_link_inventory
from .generic_inventory import assemble
from .link_evidence import load_link_evidence
from .metadata import collect_metadata
from .metadata_bom import valid_uri
from .sbom_cyclonedx import serialize_cyclonedx, to_cyclonedx
from .sbom_model import (
    APPLICATION,
    FILE,
    FIRMWARE,
    LIBRARY,
    Component,
    ExternalReference,
    Model,
    property_values,
)
from .sbom_spdx import serialize_spdx, to_spdx
from .scope import FROZEN_GROUP, parse_scope, source_path
from .submodules import SubmoduleInspector


def _ref(kind: str, identity: Any) -> str:
    value = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"urn:mpy:{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


# Gap kinds that prevent `firmware_completeness: established`; `sbom_policy` rejects a summary
# that claims `established` while carrying one of these.
FIRMWARE_BLOCKING_GAPS = frozenset(
    {
        "checkout-gitlink-mismatch",
        "dirty-selected-repository",
        "linked-header-not-in-selected-scope",
        "linked-source-not-in-selected-scope",
        "posthoc-linked-source-not-in-selected-scope",
        "repository-provenance-unavailable",
        "selected-source-not-tracked",
        "submodule-local-override",
        "unresolved-link-input",
    }
)


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
        self.components: dict[str, Component] = {}
        self.depends_on: dict[str, set[str]] = {}
        self.dynamic_links: dict[str, list[str]] = {}
        self.repositories: dict[str, dict[str, Any] | None] = {}
        self.tracked_files: dict[str, set[str]] = {}
        self.gaps: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.submodules = SubmoduleInspector()

    def add(
        self, kind: str, identity: Any, component: Component, *, parent: str | None = None
    ) -> str:
        """Register a component under a stable reference.

        A component added again under the same reference replaces the earlier one together
        with the components nested under it.
        """
        reference = _ref(kind, identity)
        if reference in self.components:
            for nested in [ref for ref, item in self.components.items() if item.parent == reference]:
                del self.components[nested]
        component.ref = reference
        component.parent = parent
        self.components[reference] = component
        if parent is None:
            self.depends_on.setdefault(reference, set())
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
        submodule = self.submodules.checkout_record(Path(root))
        if submodule:
            info["submodule"] = submodule
            info["parent_gitlink"] = submodule["pinned_commit"]
            findings = submodule["findings"]
            if "head-differs-from-pin" in findings or "gitlink-not-committed" in findings:
                gap = {
                    "kind": "checkout-gitlink-mismatch",
                    "path": root,
                    "gitlink": submodule["pinned_commit"],
                    "checkout": revision,
                    "pin_source": "git-submodule",
                }
                if "index_commit" in submodule:
                    gap["index_gitlink"] = submodule["index_commit"]
                self.gaps.append(gap)
            if "local-override" in findings:
                self.gaps.append(
                    {
                        "kind": "submodule-local-override",
                        "path": root,
                        "declared_url": submodule["url"],
                        "remote_url": submodule["remote_url"],
                    }
                )
            if "not-registered-in-gitmodules" in findings:
                self.gaps.append(
                    {
                        "kind": "repository-provenance-unavailable",
                        "path": root,
                        "reason": "the superproject records a gitlink without a .gitmodules URL",
                    }
                )
        component = Component(
            kind=LIBRARY,
            name=Path(root).name,
            version=revision,
            properties=property_values(
                {**info, "selection": "selected-source-repository", "version-source": "git-commit"}
            ),
        )
        if vcs_url:
            component.external_references = [ExternalReference("vcs", vcs_url)]
            component.purl = _purl(vcs_url, revision, Path(root).name)
        elif origin:
            self.gaps.append(
                {"kind": "repository-origin-unsupported", "path": root, "origin": origin}
            )
        else:
            self.gaps.append({"kind": "repository-origin-unavailable", "path": root})
        if status:
            gap = {"kind": "dirty-selected-repository", "path": root, "status": status}
            changes = (submodule or {}).get("diff_from_pin") or (submodule or {}).get(
                "diff_from_head"
            )
            if changes:
                gap["tracked_changes"] = changes
            self.gaps.append(gap)
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
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        identity = [record["root"], record["path"], record.get("target_path"), digest]
        reference = self.add(
            "file",
            identity,
            Component(
                kind=FILE,
                name=record.get("target_path", record["path"]),
                hashes={
                    "sha1": hashlib.sha1(content, usedforsecurity=False).hexdigest(),
                    "sha256": digest,
                },
                properties=property_values(
                    {
                        "root": record["root"],
                        "path": record["path"],
                        "owner": record.get("owner"),
                        "role": record.get("role"),
                        "selection": group,
                        "target-path": record.get("target_path"),
                    }
                ),
            ),
        )
        component = self.components[reference]
        repository = self.repository(path, declared_root=Path(self.scope["roots"][record["root"]]))
        tracked = None
        if repository:
            relative = path.relative_to(repository["directory"]).as_posix()
            tracked = relative in self.tracked_files[repository["directory"]]
            component.properties.update(property_values({"git-tracked": tracked}))
            if tracked:
                self.depends_on[repository["ref"]].add(reference)
            if not tracked:
                gap = {
                    "kind": "selected-source-not-tracked",
                    "path": str(path),
                    "detail": "The file hash identifies this input; the repository revision does not identify its contents.",
                }
                enclosing = self.submodules.chain(path.parent)
                if enclosing and enclosing[-1]["checkout"] != "present":
                    gap["submodule"] = {
                        key: enclosing[-1].get(key)
                        for key in ("path", "url", "pinned_commit", "checkout", "checkout_reason")
                    }
                self.gaps.append(gap)
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


def _runtime_file_hashes(path: str, sha256: str) -> dict[str, str]:
    """Return the receipt SHA-256 plus a SHA-1 of the file, which SPDX 2.3 requires for files.

    The SHA-1 is only taken from bytes whose SHA-256 equals the receipt's. A file that is no
    longer readable keeps its SHA-256 only and is then emitted as an SPDX package, not a file.
    """
    hashes = {"sha256": sha256}
    try:
        content = Path(path).read_bytes()
    except OSError:
        return hashes
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError(f"linked runtime file changed since the receipt: {path}")
    return {"sha1": hashlib.sha1(content, usedforsecurity=False).hexdigest(), **hashes}


def _runtime_components(
    builder: _Builder, runtime: list[dict[str, Any]], toolchain: list[dict[str, Any]], evidence_id: str
) -> set[str]:
    """Add components for statically linked compiler runtime files and C library files.

    Compiler runtime files carry the compiler release version. C library files are grouped
    under one library component whose version comes from the library's header macros.
    """
    references = set()
    libraries: dict[tuple[str, str, str], list[Component]] = {}

    def collect(item: dict[str, Any], path: str, sha256: str, members: list[str]) -> None:
        if "c_library" in item:
            library = item["c_library"]
            libraries.setdefault((library["name"], library["version"], library["version_source"]), []).append(
                Component(
                    kind=FILE,
                    name=path,
                    hashes=_runtime_file_hashes(path, sha256),
                    properties=property_values({"linkage": "static", "contributing-members": members or None}),
                )
            )
        elif "prebuilt" in item:
            references.add(
                builder.add(
                    "prebuilt-library",
                    [evidence_id, sha256],
                    Component(
                        kind=LIBRARY,
                        name=Path(path).name,
                        hashes={"sha256": sha256},
                        properties=property_values(
                            {
                                "selection": "linked-prebuilt-binary",
                                "path": path,
                                "contributing-members": str(len(members)),
                            }
                        ),
                    ),
                )
            )
        elif "toolchain" in item:
            compiler = next(
                tool
                for tool in toolchain
                if tool["role"] == item["toolchain"]["role"]
                and tool["executable"]["path"] == item["toolchain"]["executable"]["path"]
            )
            references.add(
                builder.add(
                    "toolchain-library",
                    [evidence_id, sha256],
                    Component(
                        kind=LIBRARY,
                        name=Path(path).name,
                        version=item["toolchain"]["release"],
                        hashes={"sha256": sha256},
                        properties=property_values(
                            {
                                "toolchain-compiler": compiler["executable"]["path"],
                                "toolchain-compiler-sha256": compiler["executable"]["sha256"],
                                "toolchain-compiler-banner": compiler["version"],
                                "contributing-members": str(len(members)) if members else None,
                            }
                        ),
                    ),
                )
            )

    for item in runtime:
        collect(item, item["path"], item["sha256"], item["contributing_members"])
    for (name, version, source), files in sorted(libraries.items()):
        library_ref = builder.add(
            "c-library",
            [name, version, evidence_id],
            Component(
                kind=LIBRARY,
                name=name,
                version=version,
                purl=f"pkg:generic/{quote(name, safe='')}@{quote(version, safe='')}",
                properties=property_values({"selection": "linked-c-library", "version-source": source}),
            ),
        )
        for member in sorted(files, key=lambda item: item.name):
            builder.add("c-library-file", [library_ref, member.name], member, parent=library_ref)
        references.add(library_ref)
    return references


def _enclosing_boundary(
    scope: dict[str, Any], boundaries: list[dict[str, Any]], root: str, path: str
) -> dict[str, Any] | None:
    """The selected boundary of the same root whose directory contains the path, deepest first.

    The path is canonical, so a file reached through a symlink alias is placed by the location
    it resolves to; a boundary of another root never matches.
    """
    physical = Path(scope["roots"][root]) / path
    best: tuple[int, dict[str, Any]] | None = None
    for boundary in boundaries:
        if boundary["root"] != root:
            continue
        directory = Path(scope["roots"][root]) / boundary["path"]
        if physical.is_relative_to(directory) and (best is None or len(directory.parts) > best[0]):
            best = (len(directory.parts), boundary)
    return best[1] if best else None


def _boundary_owner(builder: _Builder, boundary: dict[str, Any]) -> str | None:
    """The one owner class of the boundary's selected sources, or None when there is not exactly one."""
    owners = {
        builder.components[source["component"]].properties.get("owner")
        for source in builder.sources
        if source.get("dependency_component") == boundary["component"]
    } - {None}
    return next(iter(owners)) if len(owners) == 1 else None


def _owned_file(
    builder: _Builder,
    scope: dict[str, Any],
    boundaries: list[dict[str, Any]],
    source_refs: dict[tuple[str, str], set[str]],
    binding: tuple[str, str],
    group: str,
    provenance: str,
) -> tuple[set[str] | None, str]:
    """Attach the file of a root/path binding to the boundary that contains it.

    Returns the file component references, or None and the reason when no selected boundary
    owns the path. A file that is already a selected source or an earlier input keeps its
    component and is only added to the owning boundary.
    """
    root, path = binding
    boundary = _enclosing_boundary(scope, boundaries, root, path)
    if boundary is None:
        return None, "no selected component boundary of this root contains the file"
    if binding not in source_refs:
        source_refs[binding] = {
            builder.file(
                group,
                {
                    "root": root,
                    "path": path,
                    "owner": _boundary_owner(builder, boundary),
                    "role": group,
                    "provenance": {"kind": provenance},
                },
            )
        }
    builder.depends_on[boundary["component"]].update(source_refs[binding])
    return source_refs[binding], ""


def _consumed_headers(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """The root, path and hash of every source header a contributing compile of any image read."""
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for image in receipt["images"]:
        for entry in image.get("header_accounting") or ():
            if entry["disposition"] != "source":
                continue
            for binding in entry["source_bindings"]:
                records.setdefault(
                    (binding["root"], binding["path"]),
                    {
                        "root": binding["root"],
                        "path": binding["path"],
                        "sha256": entry["sha256"],
                        "provenance": {"kind": "compiler-dependency-file"},
                    },
                )
    return [records[key] for key in sorted(records)]


def _header_components(
    builder: _Builder,
    scope: dict[str, Any],
    boundaries: list[dict[str, Any]],
    source_refs: dict[tuple[str, str], set[str]],
    image: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[set[str], dict[str, Any]]:
    """Attribute every header a contributing compile of one image read.

    Source headers become file components under the boundary that owns them (a header-only
    boundary when no boundary of the selected sources does, which the image then depends on);
    headers in the compiler's own directories become file components of one toolchain header
    library; anything else is a named gap.
    """
    entries = image["header_accounting"]
    counts = {"consumed": len(entries), "source": 0, "toolchain": 0, "unattributed": 0}
    toolchain: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    references: set[str] = set()

    def gap(entry: dict[str, Any], reason: str, binding: tuple[str, str] | None = None) -> None:
        counts["unattributed"] += 1
        builder.gaps.append(
            {
                "kind": "linked-header-not-in-selected-scope",
                "path": entry["path"],
                **({"root": binding[0], "binding-path": binding[1]} if binding else {}),
                "image": image["id"],
                "reason": reason,
            }
        )

    for entry in entries:
        disposition = entry["disposition"]
        if disposition == "unowned":
            gap(entry, entry["reason"])
        elif disposition == "toolchain":
            binding = entry["toolchain"]
            key = (binding["role"], binding["executable"]["path"], binding["executable"]["sha256"])
            toolchain.setdefault(key, []).append(entry)
            counts["toolchain"] += 1
        else:
            placed = []
            for item in entry["source_bindings"]:
                binding = (item["root"], item["path"])
                refs, reason = _owned_file(
                    builder, scope, boundaries, source_refs, binding, "header", "compiler-dependency-file"
                )
                placed.append(refs is not None)
                if refs is None:
                    gap(entry, reason, binding)
                else:
                    boundary = _enclosing_boundary(scope, boundaries, *binding)
                    if boundary["headers"]:
                        references.add(boundary["component"])
            if all(placed):
                counts["source"] += 1

    for (role, compiler, sha256), members in sorted(toolchain.items()):
        tool = next(
            item
            for item in evidence["toolchain"]
            if item["role"] == role and item["executable"]["path"] == compiler
        )
        release = next((item["toolchain"]["release"] for item in members if "release" in item["toolchain"]), None)
        library = builder.add(
            "toolchain-headers",
            [evidence["evidence_id"], image["id"], role, sha256],
            Component(
                kind=LIBRARY,
                name=f"{Path(compiler).name} compiler headers",
                version=release,
                properties=property_values(
                    {
                        "selection": "compiler-header-directories",
                        "toolchain-compiler": compiler,
                        "toolchain-compiler-sha256": sha256,
                        "toolchain-compiler-banner": tool["version"],
                        "include-dirs": sorted({item["toolchain"]["include_dir"] for item in members}),
                        "header-count": str(len(members)),
                    }
                ),
            ),
        )
        for item in members:
            builder.add(
                "toolchain-header-file",
                [library, item["path"]],
                Component(
                    kind=FILE,
                    name=item["path"],
                    hashes=_runtime_file_hashes(item["path"], item["sha256"]),
                    properties=property_values(
                        {"role": "header", "include-dir": item["toolchain"]["include_dir"]}
                    ),
                ),
                parent=library,
            )
        references.add(library)
    return references, {"status": "attributed" if not counts["unattributed"] else "incomplete", **counts}


def _attach_firmware_images(
    evidence: dict[str, Any],
    scope: dict[str, Any],
    builder: _Builder,
    root_dependencies: set[str],
    boundaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add the firmware images of the link evidence and the selected components they link."""
    source_refs: dict[tuple[str, str], set[str]] = {}
    for source in builder.sources:
        source_refs.setdefault((source["root"], source["path"]), set()).add(source["component"])

    images = []
    for image in evidence["images"]:
        mapped, missing = set(), []
        for item in image["sources"]:
            binding = (item["root"], item["path"])
            if binding in source_refs:
                mapped.update(source_refs[binding])
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
        for path in image["unbound_sources"]:
            builder.gaps.append(
                {"kind": "linked-source-not-in-selected-scope", "path": path, "image": image["id"]}
            )
        for row in image["unresolved"]:
            builder.gaps.append(
                {"image": image["id"], **{key: value for key, value in row.items() if key != "kind"}, "input-kind": row["kind"], "kind": "unresolved-link-input"}
            )
        header_refs, headers = _header_components(builder, scope, boundaries, source_refs, image, evidence)
        link_status = (
            "complete"
            if not (missing or image["unbound_sources"] or image["unresolved"])
            and headers["status"] == "attributed"
            else "incomplete"
        )
        component = Component(
            kind=FIRMWARE,
            name=image["id"],
            hashes={"sha256": image["elf"]["sha256"]},
            properties=property_values(
                {
                    "evidence-id": evidence["evidence_id"],
                    "image-role": image["role"],
                    "port": scope["port"],
                    "target": scope["target"],
                    "variant": scope["variant"],
                    "link-closure": link_status,
                    "linker-map-sha256": image["map"]["sha256"],
                }
            ),
        )
        reference = builder.add("firmware-image", [evidence["evidence_id"], image["id"]], component)
        if image["dynamic_needed"]:
            builder.dynamic_links[reference] = list(image["dynamic_needed"])
        mapped.update(_runtime_components(builder, image["runtime"], evidence["toolchain"], evidence["evidence_id"]))
        mapped.update(header_refs)
        builder.depends_on[reference] = mapped
        root_dependencies.add(reference)
        images.append(
            {
                "id": image["id"],
                "role": image["role"],
                "elf_sha256": image["elf"]["sha256"],
                "link_closure": link_status,
                "linked_components": sorted(mapped),
                "headers": headers,
                "unmapped_source_bindings": [{"root": root, "path": path} for root, path in missing],
            }
        )
    return {
        "schema": evidence["schema"],
        "evidence_id": evidence["evidence_id"],
        "status": "complete" if all(image["link_closure"] == "complete" for image in images) else "incomplete",
        "images": images,
    }


def _attach_deployment(
    builder: _Builder, evidence: dict[str, Any], root_dependencies: set[str]
) -> str:
    """Add the deployment component, the verified deployed non-image files nested under it, and
    its dependencies on the receipt images the manifest binds.

    Only members whose file hashes to the declared digest become file components. Image members
    are represented by the firmware components of the receipt, never by a second component.
    """
    component = Component(
        kind=APPLICATION,
        name=evidence["name"],
        properties=property_values(
            {
                "role": "deployment",
                "target": evidence["target"],
                "deployment-completeness": evidence["status"],
                "deployment-manifest-sha256": evidence["manifest_sha256"],
                "runtime-fetch": evidence["runtime_fetch"]["mode"],
            }
        ),
    )
    reference = builder.add(
        "deployment", [evidence["name"], evidence["target"], evidence["manifest_sha256"]], component
    )
    bound = set()
    for row in evidence["members"]:
        if row["kind"] in IMAGE_KINDS:
            binding = row["image_binding"]
            if binding and evidence["link_evidence"]:
                bound.add(_ref("firmware-image", [evidence["link_evidence"]["evidence_id"], binding["image"]]))
            continue
        if row["digest"] != "match":
            continue
        builder.add(
            "deployment-file",
            [reference, row["id"], row["actual_sha256"]],
            Component(
                kind=FILE,
                name=row["path"],
                hashes={"sha1": row["actual_sha1"], "sha256": row["actual_sha256"]},
                properties=property_values(
                    {
                        "role": f"deployed-{row['kind']}",
                        "deployment": evidence["name"],
                        "deployment-member": row["id"],
                    }
                ),
            ),
            parent=reference,
        )
    builder.depends_on[reference] = {item for item in bound if item in builder.components}
    root_dependencies.add(reference)
    return reference


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
    component = Component(
        kind=FIRMWARE,
        name=image["id"],
        hashes={"sha256": image["elf"]["sha256"]},
        properties=property_values(
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
    )
    reference = builder.add(
        "posthoc-firmware-image",
        [evidence["generation"]["id"], image["id"]],
        component,
    )
    builder.depends_on[reference] = mapped
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
    link_evidence: Path | None = None,
    posthoc_link_inventory: Path | None = None,
    link_generation: Path | None = None,
    deployment: Path | None = None,
) -> tuple[Model, dict[str, Any]]:
    """Return the neutral SBOM model of the selected dependencies and field-level evidence."""
    if link_evidence is not None and posthoc_link_inventory is not None:
        raise ValueError("provide either link evidence or a posthoc link inventory, not both")
    if link_generation is not None and posthoc_link_inventory is None:
        raise ValueError("--link-generation requires --link-inventory")
    if posthoc_link_inventory is not None:
        posthoc_evidence = load_posthoc_link_inventory(
            posthoc_link_inventory, generation_path=link_generation
        )
    else:
        posthoc_evidence = None
    firmware_evidence = load_link_evidence(link_evidence) if link_evidence is not None else None
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
    inventory = assemble(
        builder,
        scope,
        list(declaration_files),
        _consumed_headers(firmware_evidence) if firmware_evidence else (),
    )
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
            firmware_evidence, scope, builder, root_dependencies, inventory["metadata"]
        )
    elif posthoc_evidence:
        link_closure = _attach_posthoc_firmware_image(
            posthoc_evidence, scope, builder, root_dependencies
        )
    else:
        link_closure = None
    deployment_evidence = None
    if deployment is not None:
        deployment_evidence = verify_deployment(
            load_deployment(deployment), target=scope["target"], link_evidence=firmware_evidence
        )
        _attach_deployment(builder, deployment_evidence, root_dependencies)
    root = Component(
        kind=APPLICATION,
        name=scope["target"],
        ref=build_ref,
        properties=property_values(
            {
                **build_identity,
                "scope": "manifest-and-compiler-selected-sources",
                "link-closure": link_closure["status"] if link_closure else "not-established",
                "deployment-completeness": deployment_evidence["status"] if deployment_evidence else None,
            }
        ),
    )
    builder.depends_on[build_ref] = root_dependencies
    model = Model(
        root=build_ref,
        name=scope["target"],
        components={**builder.components, build_ref: root},
        depends_on=builder.depends_on,
        dynamic_links=builder.dynamic_links,
    )
    unassessed = {
        group: len(records)
        for group, records in scope["groups"].items()
        if group not in (FROZEN_GROUP, "native") and records
    }

    firmware_established = bool(
        firmware_evidence
        and link_closure
        and link_closure["status"] == "complete"
        and not any(gap["kind"] in FIRMWARE_BLOCKING_GAPS for gap in builder.gaps)
    )
    receipt = {
        "schema_version": 1,
        **build_identity,
        "status": "generated",
        "coverage_status": "incomplete"
        if builder.gaps or unassessed
        else "selected-sources-accounted",
        "source_accounting": "accounted" if not unassessed else "incomplete",
        "firmware_completeness": "established" if firmware_established else "not-established",
        "deployment_completeness": deployment_evidence["status"] if deployment_evidence else NOT_DECLARED,
        "limitations": [
            *(
                []
                if link_closure
                else ["Compilation databases select compiled sources, not linked objects."]
            ),
            "Manifest selection does not inventory runtime-loaded or build-fetched dependencies.",
            *(
                []
                if firmware_evidence
                else ["Header/sysroot and generated-input provenance remain separate closure requirements."]
            ),
            "Unmarked vendored boundaries cannot be discovered reliably; declare selected dependencies explicitly.",
        ],
        **inventory,
        "gaps": builder.gaps,
        "unassessed_groups": unassessed,
        "sources": builder.sources,
        "link_closure": link_closure or {"status": "not-established"},
        "deployment": deployment_evidence,
        "packages": scope.get("packages", []),
        "package_requirements": scope.get("package_requirements", []),
        "component_count": sum(1 for item in builder.components.values() if item.parent is None),
        "repositories": [
            builder.repositories[key]
            for key in sorted(builder.repositories)
            if builder.repositories[key] and builder.repositories[key]["directory"] == key
        ],
    }
    model.check()
    return model, receipt


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
        "--link-evidence",
        type=Path,
        help="mpy-link-evidence/v1 file from mpy_analysis.link_evidence for the selected firmware build",
    )
    parser.add_argument(
        "--deployment",
        type=Path,
        help="mpy-deployment/v1 manifest naming the deployed images, files and runtime fetch policy",
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
    parser.add_argument(
        "--format",
        choices=("spdx", "cyclonedx"),
        default="spdx",
        help="document format to write: sbom.spdx.json (SPDX 2.3) or sbom.json (CycloneDX 1.6)",
    )
    args = parser.parse_args(argv)
    try:
        scope_path = args.scope.resolve()
        output = args.output_dir.resolve()
        documents = {"cyclonedx": output / "sbom.json", "spdx": output / "sbom.spdx.json"}
        summary_path = output / "sbom-summary.json"
        destinations = [*documents.values(), summary_path]
        input_error = None
        try:
            data = json.loads(scope_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            input_error, data = exc, None
        protected = _protected_inputs(scope_path, data)
        protected.update(path.resolve() for path in args.dependency_metadata)
        for evidence_path in (args.link_evidence, args.link_inventory, args.link_generation):
            if evidence_path:
                protected.add(evidence_path.resolve())
        if args.deployment:
            protected.add(args.deployment.resolve())
        _invalidate_outputs(destinations, protected)
        if input_error is not None:
            raise input_error
        scope = parse_scope(data)
        model, receipt = generate_sbom(
            scope,
            micropython_root=args.micropython_root,
            declaration_files=args.dependency_metadata,
            link_evidence=args.link_evidence,
            posthoc_link_inventory=args.link_inventory,
            link_generation=args.link_generation,
            deployment=args.deployment,
        )
        rendered = (
            serialize_spdx(to_spdx(model))
            if args.format == "spdx"
            else serialize_cyclonedx(to_cyclonedx(model))
        )
        output.mkdir(parents=True, exist_ok=True)
        receipt["scope_file"] = str(scope_path)
        receipt["scope_sha256"] = hashlib.sha256(scope_path.read_bytes()).hexdigest()
        receipt["document_format"] = "SPDX-2.3" if args.format == "spdx" else "CycloneDX-1.6"
        receipt["document_file"] = documents[args.format].name
        documents[args.format].write_bytes(rendered)
        summary_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"SBOM: {receipt['component_count']} components; {receipt['coverage_status']}")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"sbom: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
