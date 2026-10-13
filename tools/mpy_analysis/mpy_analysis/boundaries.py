"""Physical package candidates and ownership for selected frozen/compiler rows.

Discovery follows selected-file ancestry within declared roots. Repository
boundaries describe checkouts, not distribution names or release versions.
"""

from __future__ import annotations

import ast
import configparser
import re
import subprocess
from email.parser import Parser

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from .declarations import revision_matches_selected, selected_digest, source_binding
from .metadata_bom import UnsupportedBomError, parse_json, read_bom
from .metadata_native import parse_library_json, parse_native
from .locks import LOCK_FILES, inspect_lock
from .scope import SELECTED_GROUPS, source_path
from .submodules import SubmoduleInspector


_PACKAGE_MARKERS = ("metadata.txt", "setup.py")
_KIND_ORDER = (
    "required-package",
    "package-metadata",
    "native-project",
    "declared",
    "repository",
    "unresolved-root",
)


def _contains(directory: Path, path: Path) -> bool:
    return path == directory or directory in path.parents


def _repository_root(directory: Path) -> Path | None:
    """Ask Git about a selected file's directory, without inspecting .git trees."""
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    value = result.stdout.rstrip("\n")
    return Path(value).resolve(strict=True) if value else None


def _marker(directory: Path, name: str, root: Path) -> Path | None:
    path = directory / name
    if not path.is_file():
        return None
    physical = path.resolve(strict=True)
    return physical if _contains(root, physical) else None


def _binding_matches(scope: dict[str, Any], directory: Path, properties: dict[str, Any]) -> bool:
    """Bind all selected physical descendants, independently of component ownership."""
    root, relative = properties.get("mpy:source:root"), properties.get("mpy:source:path")
    revision, digest = (
        properties.get("mpy:source:revision"),
        properties.get("mpy:source:sha256"),
    )
    if (
        not isinstance(root, str)
        or not isinstance(relative, str)
        or bool(revision) == bool(digest)
    ):
        return False
    if (root, relative) != (".", "."):
        canonical_root = min(
            (
                (name, Path(value))
                for name, value in scope["roots"].items()
                if _contains(Path(value), directory)
            ),
            key=lambda item: (-len(item[1].parts), item[0]),
        )[0]
        if root != canonical_root or ":" in relative:
            return False
        try:
            if source_binding(scope, root, relative) != directory:
                return False
        except (ValueError, OSError):
            return False
    if digest:
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False
        actual_digest, count = selected_digest(scope, directory)
        return count > 0 and digest == actual_digest
    if (
        not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is None
    ):
        return False
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.returncode == 0
        and result.stdout.strip() == revision
        and revision_matches_selected(scope, directory)
    )


def _core_binding(path: Path) -> dict[str, Any]:
    message = Parser().parsestr(path.read_text(encoding="utf-8-sig"))
    if message.get("Metadata-Version") not in ("1.0", "1.1", "1.2", "2.1", "2.2", "2.3", "2.4"):
        raise ValueError("unsupported or absent Core Metadata version")
    keys = ("root", "path", "revision", "sha256")
    if any(len(message.get_all("X-Mpy-Source-" + key.title(), [])) > 1 for key in keys):
        raise ValueError("duplicate Core Metadata source-binding header")
    return {"mpy:source:" + key: message.get("X-Mpy-Source-" + key.title()) for key in keys}


def _generated_marker(
    scope: dict[str, Any], directory: Path, path: Path, *, bom: bool
) -> dict[str, Any]:
    item = {"kind": "candidate-metadata", "path": str(path)}
    try:
        if not _contains(directory, path):
            raise ValueError("generated metadata escapes its physical package directory")
        if bom:
            components, _ = read_bom(parse_json(path.read_text(encoding="utf-8-sig")))
            bindings = [component["binding"] for component in components]
        else:
            bindings = [_core_binding(path)]
        matching = sum(_binding_matches(scope, directory, binding) for binding in bindings)
        if matching == 1:
            item["kind"] = "package-metadata"
        else:
            item["detail"] = (
                "Multiple components bind the same selected boundary."
                if matching > 1
                else "Generated metadata is unbound, stale or unsupported for selected inputs."
            )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        item["detail"] = str(exc)
        if bom and _contains(directory, path) and not isinstance(exc, UnsupportedBomError):
            item["invalid_input"] = str(exc)
    return item


def _generated_markers(scope: dict[str, Any], directory: Path, root: Path) -> list[dict[str, Any]]:
    """Inspect immediate format-specific metadata children, never dependency trees."""
    result = []
    for path in sorted(directory.iterdir()):
        relative = None
        bom = False
        if path.name in {"PKG-INFO", "METADATA"}:
            relative = path.name
        elif path.name.endswith((".dist-info", ".egg-info")) and path.is_dir():
            name = "METADATA" if path.name.endswith(".dist-info") else "PKG-INFO"
            relative = f"{path.name}/{name}"
        elif path.name in {"bom.json", "sbom.json"} or path.name.endswith(
            (".cdx.json", ".spdx.json")
        ):
            relative = path.name
            bom = True
        if relative is not None:
            marker = _marker(directory, relative, root)
            if marker is not None:
                result.append(_generated_marker(scope, directory, marker, bom=bom))
    return result


def _markers(directory: Path, root: Path, scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Inspect format-specific markers only; filenames alone never select sources."""
    result = []
    for name in _PACKAGE_MARKERS:
        path = _marker(directory, name, root)
        if path is not None:
            kind = "package-metadata"
            detail = None
            if name == "setup.py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    calls = [
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        and (
                            isinstance(node.func, ast.Name)
                            and node.func.id == "setup"
                            or isinstance(node.func, ast.Attribute)
                            and node.func.attr == "setup"
                        )
                    ]
                    if not calls:
                        kind = "candidate-metadata"
                        detail = "No statically identifiable setup call."
                except (SyntaxError, UnicodeError, OSError) as exc:
                    kind = "candidate-metadata"
                    detail = str(exc)
            item = {"kind": kind, "path": str(path)}
            if detail is not None:
                item["detail"] = detail
            result.append(item)
    path = _marker(directory, "pyproject.toml", root)
    if path is not None:
        try:
            value = tomllib.loads(path.read_text(encoding="utf-8"))
            kind = (
                "package-metadata"
                if isinstance(value.get("project"), dict)
                else "candidate-metadata"
            )
            result.append({"kind": kind, "path": str(path)})
        except (tomllib.TOMLDecodeError, UnicodeError, OSError) as exc:
            result.append({"kind": "candidate-metadata", "path": str(path), "detail": str(exc)})
    path = _marker(directory, "library.json", root)
    if path is not None:
        try:
            rows = parse_library_json(path.read_text(encoding="utf-8-sig"))
            named = any(
                row["field"] == "name"
                and row["status"] == "known"
                and isinstance(row["value"], str)
                and row["value"].strip()
                for row in rows
            )
            item = {
                "kind": "package-metadata" if named else "candidate-metadata",
                "path": str(path),
            }
            problems = [
                {key: row[key] for key in ("field", "status", "reason")}
                for row in rows
                if row["status"] not in ("known", "candidate")
            ]
            if not named:
                item["detail"] = "library.json has no literal name"
            if problems:
                item["parser_issues"] = problems
            result.append(item)
        except (ValueError, UnicodeError, OSError) as exc:
            result.append({"kind": "candidate-metadata", "path": str(path), "detail": str(exc)})
    path = _marker(directory, "setup.cfg", root)
    if path is not None:
        try:
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(path.read_text(encoding="utf-8"))
            kind = "package-metadata" if config.has_section("metadata") else "candidate-metadata"
            result.append({"kind": kind, "path": str(path)})
        except (configparser.Error, UnicodeError, OSError) as exc:
            result.append({"kind": "candidate-metadata", "path": str(path), "detail": str(exc)})
    for name in ("CMakeLists.txt", "meson.build"):
        path = _marker(directory, name, root)
        if path is not None:
            try:
                fields, issues = parse_native(path.read_text(encoding="utf-8"), name)
                kind = "native-project" if fields.get("name") else "candidate-native-project"
                item = {"kind": kind, "path": str(path)}
                if issues:
                    item["parser_issues"] = issues
                result.append(item)
            except (UnicodeError, OSError) as exc:
                result.append(
                    {"kind": "candidate-native-project", "path": str(path), "detail": str(exc)}
                )
    path = _marker(directory, "manifest.py", root)
    if path is not None:
        result.append({"kind": "candidate-manifest", "path": str(path)})
    for name in LOCK_FILES:
        path = _marker(directory, name, root)
        if path is not None:
            result.append(inspect_lock(path, name))
    result.extend(_generated_markers(scope, directory, root))
    return result


def _declared_directory(scope: dict[str, Any], declaration: Mapping[str, Any]) -> Path:
    root_name = declaration.get("root")
    if not isinstance(root_name, str) or root_name not in scope["roots"]:
        raise ValueError(f"unknown declared boundary root: {root_name!r}")
    value = declaration.get("path")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("declared boundary path must be a relative POSIX directory")
    if value != "." and (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"unsafe declared boundary path: {value}")
    root = Path(scope["roots"][root_name]).resolve(strict=True)
    directory = (root / value).resolve(strict=True)
    if not _contains(root, directory) or not directory.is_dir():
        raise ValueError(f"declared boundary is not a directory within root: {value}")
    return directory


def _issue(value: dict[str, Any], kind: str, status: str, reason: str, **details: Any) -> None:
    item = {"kind": kind, "field": "boundary", "status": status, "reason": reason, **details}
    if item not in value["issues"]:
        value["issues"].append(item)


def _record_candidate_issues(
    value: dict[str, Any],
    owner: Path,
    physical: Path,
    package_id: str | None,
    applicable: list[Path],
    candidate_evidence: dict[Path, list[dict[str, Any]]],
    kinds: dict[Path, set[str]],
) -> None:
    """Retain applicable evidence and disagreements without changing ownership."""
    for candidate in applicable:
        for item in candidate_evidence[candidate]:
            if item not in value["candidates"]:
                value["candidates"].append(item)
        # Ancestor checkouts are containment evidence; nested checkouts
        # and metadata can contradict authoritative package ownership.
        competing = kinds[candidate] & {
            "package-metadata",
            "native-project",
            "declared",
            "repository",
        }
        if candidate != owner and competing and _contains(owner, candidate):
            _issue(
                value,
                "ambiguous-nested-boundary",
                "conflicting",
                "Applicable package/project/declaration boundaries disagree with source ownership.",
                path=str(physical),
                candidate=str(candidate),
            )
        if package_id is None:
            required = [
                item
                for item in candidate_evidence[candidate]
                if item["kind"] == "required-package"
            ]
            if required:
                _issue(
                    value,
                    "required-package-ownership-unconfirmed",
                    "unsupported",
                    "Physical proximity does not establish authority package ownership.",
                    path=str(physical),
                    package_ids=sorted(item["package_id"] for item in required),
                )


def _finalize_boundary(
    value: dict[str, Any],
    kinds: set[str],
    package_assertions: dict[str, list[Any]],
) -> None:
    """Resolve compatible assertions and expose incomplete boundary identity."""
    value["boundary_kind"] = next(kind for kind in _KIND_ORDER if kind in kinds)
    value["package_ids"].sort()
    for field, assertions in package_assertions.items():
        if all(assertion == assertions[0] for assertion in assertions):
            value["manifest_metadata"][field] = assertions[0]
        else:
            _issue(
                value,
                "conflicting-manifest-metadata",
                "conflicting",
                "Authority packages sharing one physical boundary declare conflicting values.",
                metadata_field=field,
                values=assertions,
            )
    if value["boundary_kind"] in {"repository", "unresolved-root"}:
        _issue(
            value,
            "unresolved-identity",
            "missing",
            "Checkout/root containment does not establish a distribution identity; supply package metadata or a source-bound declaration.",
        )
    weak = [item for item in value["candidates"] if item["kind"].startswith("candidate-")]
    if weak and value["boundary_kind"] in {"repository", "unresolved-root"}:
        _issue(
            value,
            "unresolved-boundary-candidates",
            "unsupported",
            "Marker candidates do not independently establish selected package ownership.",
            candidates=weak,
        )
    for item in value["candidates"]:
        if item["kind"] == "lock-file" and item["status"] == "unsupported":
            _issue(
                value,
                "unsupported-lock-format",
                "unsupported",
                item["reason"],
                path=item["path"],
                format=item["format"],
            )
    value["candidates"].sort(
        key=lambda item: (
            item["directory"],
            item["kind"],
            item.get("path", ""),
            item.get("package_id", ""),
        )
    )


def discover_boundaries(
    scope: dict[str, Any],
    *,
    declared_boundaries: Sequence[Mapping[str, Any]] | None = None,
    submodules: SubmoduleInspector | None = None,
) -> list[dict[str, Any]]:
    """Return physical boundaries, retaining every selected row exactly once.

    ``scope`` is the validated selected-source inventory. ``declared_boundaries``
    accepts root/path directory mappings (``path='.'`` binds the root). These
    selectors define otherwise unidentified vendored boundaries; the caller
    validates declaration metadata, revision/hash applicability and unmatched
    selectors. Selectors cannot override authoritative package ownership or
    independently discovered package/project boundaries.

    Applicable evidence, including non-owning nested candidates, is exposed in
    ``candidates``. Source records are not copied, deduplicated or rewritten;
    duplicate compiler commands and frozen import aliases remain distinct rows.
    Physical aliases coalesce boundary identity, never inventory rows.

    ``fetch_provenance`` is the chain of Git submodule records enclosing the boundary,
    outermost first (see ``submodules``): where the fetched checkout was declared to come
    from, the commit the superproject pins, and the checkout's actual state. ``owns_root``
    marks the record of the checkout whose root is the boundary directory. The evidence
    never changes ownership or identity.
    """
    roots = {name: Path(value).resolve(strict=True) for name, value in scope["roots"].items()}
    submodules = submodules or SubmoduleInspector()
    registry: dict[Path, dict[str, Any]] = {}
    candidate_evidence: dict[Path, list[dict[str, Any]]] = {}
    kinds: dict[Path, set[str]] = {}
    package_directories: dict[str, Path] = {}
    package_assertions: dict[Path, dict[str, list[Any]]] = {}
    discovered: dict[Path, list[dict[str, Any]]] = {}
    git_roots: dict[Path, Path | None] = {}
    physical_owners: dict[Path, set[Path]] = {}
    owned: set[Path] = set()

    def location(directory: Path) -> tuple[str, str]:
        available = [(name, root) for name, root in roots.items() if _contains(root, directory)]
        if not available:
            raise ValueError(f"boundary outside declared roots: {directory}")
        name, root = min(available, key=lambda item: (-len(item[1].parts), item[0]))
        return name, directory.relative_to(root).as_posix()

    def boundary(directory: Path) -> dict[str, Any]:
        if directory not in registry:
            root, relative = location(directory)
            registry[directory] = {
                "id": f"{root}/{relative}",
                "root": root,
                "path": relative,
                "directory": str(directory),
                "sources": [],
                "package_ids": [],
                "manifest_metadata": {},
                "boundary_kind": "unresolved-root",
                "issues": [],
                "candidates": [],
                "fetch_provenance": [],
            }
            kinds[directory] = set()
            candidate_evidence[directory] = []
        return registry[directory]

    def evidence(directory: Path, item: dict[str, Any]) -> dict[str, Any]:
        item = {"directory": str(directory), **item}
        value = boundary(directory)
        if item not in candidate_evidence[directory]:
            candidate_evidence[directory].append(item)
        if item not in value["candidates"]:
            value["candidates"].append(item)
        return item

    for package in scope.get("packages", []):
        manifest = source_path(scope, package)
        directory = manifest.parent
        package_directories[package["id"]] = directory
        evidence(
            directory,
            {"kind": "required-package", "path": str(manifest), "package_id": package["id"]},
        )

    for declaration in declared_boundaries or ():
        if not isinstance(declaration, Mapping):
            raise ValueError("declared boundary must be a root/path mapping")
        directory = _declared_directory(scope, declaration)
        value = boundary(directory)
        if "declared" in kinds[directory]:
            _issue(
                value,
                "duplicate-declared-boundary",
                "conflicting",
                "Multiple declarations bind the same physical boundary.",
            )
        kinds[directory].add("declared")
        evidence(
            directory,
            {"kind": "declared", "root": declaration["root"], "path": declaration["path"]},
        )

    for group in SELECTED_GROUPS:
        for record in scope["groups"].get(group, []):
            physical = source_path(scope, record)
            containing_roots = [root for root in roots.values() if _contains(root, physical)]
            outer = min(containing_roots, key=lambda root: len(root.parts))
            directory = physical.parent
            while _contains(outer, directory):
                if directory not in discovered:
                    discovered[directory] = _markers(directory, outer, scope)
                    for marker in discovered[directory]:
                        evidence(directory, marker)
                        if marker["kind"] in {"package-metadata", "native-project"}:
                            kinds[directory].add(marker["kind"])
                if directory == outer:
                    break
                directory = directory.parent
            if physical.parent not in git_roots:
                git_roots[physical.parent] = _repository_root(physical.parent)
            checkout = git_roots[physical.parent]
            if checkout is not None and _contains(outer, checkout):
                evidence(checkout, {"kind": "repository", "path": str(checkout)})
                kinds[checkout].add("repository")
            provenance = record.get("provenance")
            package_id = provenance.get("package_id") if isinstance(provenance, dict) else None
            applicable = [candidate for candidate in registry if _contains(candidate, physical)]
            automatic = [
                candidate
                for candidate in applicable
                if kinds[candidate] & {"package-metadata", "native-project"}
            ]
            manual = [candidate for candidate in applicable if "declared" in kinds[candidate]]
            if checkout is not None and _contains(outer, checkout):
                # Metadata outside an independent checkout cannot identify its
                # distribution. Explicit authority ownership is handled below.
                automatic = [
                    candidate for candidate in automatic if _contains(checkout, candidate)
                ]
                manual = [candidate for candidate in manual if _contains(checkout, candidate)]
            if package_id is not None:
                owner = package_directories[package_id]
                kinds[owner].add("required-package")
                value = boundary(owner)
                if package_id not in value["package_ids"]:
                    value["package_ids"].append(package_id)
                    package = next(item for item in scope["packages"] if item["id"] == package_id)
                    assertions = package_assertions.setdefault(owner, {})
                    for field, assertion in package["metadata"].items():
                        if assertion is not None:
                            assertions.setdefault(field, []).append(assertion)
                if not _contains(owner, physical):
                    _issue(
                        value,
                        "source-outside-package-directory",
                        "unsupported",
                        "Authority ownership extends outside the manifest directory.",
                        path=str(physical),
                    )
            elif automatic:
                owner = max(automatic, key=lambda path: len(path.parts))
            elif manual:
                owner = max(manual, key=lambda path: len(path.parts))
            elif checkout is not None and _contains(outer, checkout):
                owner = checkout
            else:
                owner = max(containing_roots, key=lambda path: len(path.parts))
                boundary(owner)
                kinds[owner].add("unresolved-root")
            value = boundary(owner)
            owned.add(owner)
            value["sources"].append((group, record))
            previous_owners = physical_owners.setdefault(physical, set())
            for previous in sorted(previous_owners - {owner}):
                for affected, alternative in ((value, previous), (registry[previous], owner)):
                    _issue(
                        affected,
                        "conflicting-source-ownership",
                        "conflicting",
                        "Rows selecting the same physical source claim different boundaries.",
                        path=str(physical),
                        candidate=str(alternative),
                    )
            previous_owners.add(owner)
            _record_candidate_issues(
                value, owner, physical, package_id, applicable, candidate_evidence, kinds
            )
            if package_id is not None and owner not in applicable:
                # The selected include can belong to a package in another root.
                for candidate in automatic + manual:
                    _issue(
                        value,
                        "ambiguous-nested-boundary",
                        "conflicting",
                        "Physical package evidence differs from authority ownership.",
                        path=str(physical),
                        candidate=str(candidate),
                    )
            if checkout is not None and not _contains(outer, checkout):
                evidence_value = {
                    "directory": str(checkout),
                    "kind": "repository",
                    "path": str(checkout),
                }
                if evidence_value not in value["candidates"]:
                    value["candidates"].append(evidence_value)

    for directory in owned:
        registry[directory]["fetch_provenance"] = [
            {**record, "owns_root": record["directory"] == str(directory)}
            for record in submodules.chain(directory)
        ]
    for directory in owned:
        _finalize_boundary(
            registry[directory], kinds[directory], package_assertions.get(directory, {})
        )
    return sorted((registry[directory] for directory in owned), key=lambda value: value["id"])


def uncovered_headers(
    scope: dict[str, Any],
    boundaries: Sequence[Mapping[str, Any]],
    headers: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The header records no boundary of their root contains, from a Git checkout.

    ``boundaries`` are the boundaries of the selected sources. A package directory counts as
    containing its files even when no selected source claims it. A header outside every Git
    checkout is left out: its owner cannot be named.
    """
    containing = [(item["root"], Path(item["directory"])) for item in boundaries]
    containing += [
        (package["root"], source_path(scope, package).parent) for package in scope.get("packages", [])
    ]
    result = []
    for record in headers:
        physical = source_path(scope, record)
        if any(root == record["root"] and _contains(directory, physical) for root, directory in containing):
            continue
        if _repository_root(physical.parent) is not None:
            result.append(record)
    return result


# A boundary with its own package identity needs no enclosing metadata.
_CONTEXT_KINDS = {"repository", "unresolved-root", "declared"}


def metadata_context(scope: dict[str, Any], boundary: Mapping[str, Any]) -> list[Path]:
    """Parent directories whose metadata may be reported, never asserted, for a boundary.

    Only a boundary without its own package identity (a checkout, an unresolved root
    or a declared directory) has context. The chain starts at its parent and ends at the
    first of: the root of its enclosing Git checkout (a submodule or other checkout is
    its own root, so the chain cannot cross it), the first directory with package or
    native project metadata (a different package), or the edge of another declared
    root. The chain never changes source selection or ownership. The list is nearest
    first, with no gaps, and empty when no enclosing checkout is known.
    """
    if boundary.get("boundary_kind") not in _CONTEXT_KINDS:
        return []
    directory = Path(boundary["directory"]).resolve(strict=True)
    checkout = _repository_root(directory)
    if checkout is None or checkout == directory or not _contains(checkout, directory):
        return []
    own_root = Path(scope["roots"][boundary["root"]]).resolve(strict=True)
    foreign = [
        root
        for root in (Path(value).resolve(strict=True) for value in scope["roots"].values())
        if root != own_root
    ]
    chain = []
    current = directory.parent
    while _contains(checkout, current):
        if any(_contains(root, current) for root in foreign):
            break
        chain.append(current)
        identity = {"package-metadata", "native-project"}
        if current == checkout or any(
            item["kind"] in identity for item in _markers(current, current, scope)
        ):
            break
        current = current.parent
    return chain
