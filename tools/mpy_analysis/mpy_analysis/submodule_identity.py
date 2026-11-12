"""Identity of a fetched dependency that is a Git submodule of an SDK checkout.

An SDK that vendors a library as a submodule can declare its identity in the
superproject's ``.gitmodules`` through ``sbom-*`` variables (the ESP-IDF convention read by
``idf.py sbom-create``) or in the library's own ``sbom.yml``. A submodule that declares
neither is still identified by the repository the superproject pins: the package is named
``submodule-<path>`` and its version is the pinned commit, the same package the ESP-IDF
tool reports for it, with the commit added as the version.

Libraries inside the MicroPython tree are never identified this way; MicroPython declares
those itself (see ``mpy_tree``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import mpy_tree

# Boundary issues that a pinned submodule resolves: containment in a pinned repository is
# its identity, and an enclosing project marker does not change which package owns it.
RESOLVED_ISSUES = frozenset({"unresolved-identity", "unresolved-boundary-candidates"})


def record_of(boundary: Mapping[str, Any]) -> dict[str, Any] | None:
    """The submodule record whose checkout root is the boundary, if it identifies a package."""
    chain = boundary.get("fetch_provenance") or []
    record = chain[-1] if chain and chain[-1].get("owns_root") else None
    if (
        boundary.get("boundary_kind") != "repository"
        or record is None
        or not record.get("url")
        or not record.get("pinned_commit")
        or mpy_tree.find_root(Path(boundary["directory"])) is not None
    ):
        return None
    return record


def unresolved(
    issues: list[dict[str, Any]], record: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Boundary issues that remain once a pinned submodule has identified the boundary."""
    if record is None:
        return issues
    return [issue for issue in issues if issue.get("kind") not in RESOLVED_ISSUES]


def fallback_name(record: Mapping[str, Any]) -> str:
    return "submodule-" + str(record["path"]).replace("/", "-")
