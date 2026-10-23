"""Fetched-dependency evidence of an SBOM run: submodule checkouts and lock candidates.

Everything here is evidence about how selected source arrived and which other fetched
dependencies were declared; none of it creates a component. A submodule or lock entry that no
selected boundary belongs to stays a candidate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .submodules import SubmoduleInspector


def _contains(directory: Path, path: Path) -> bool:
    return path == directory or directory in path.parents


def _submodules(
    boundaries: Sequence[Mapping[str, Any]], inspector: SubmoduleInspector
) -> list[dict[str, Any]]:
    """Every submodule of every selected checkout and every submodule a boundary lies in.

    ``selected`` is true when a selected boundary lies inside the submodule's checkout.
    """
    selected: dict[str, list[str]] = {}
    checkouts: dict[Path, int] = {}
    records: dict[str, dict[str, Any]] = {}
    for boundary in boundaries:
        chain = boundary.get("fetch_provenance", [])
        for record in chain:
            selected.setdefault(record["directory"], []).append(boundary["id"])
            records.setdefault(record["directory"], record)
        if chain:
            if chain[-1]["checkout"] == "present":
                checkouts[Path(chain[-1]["directory"]).resolve()] = chain[-1]["depth"] + 1
        else:
            top = inspector.toplevel(Path(boundary["directory"]))
            if top is not None:
                checkouts[top] = 0
    for checkout, depth in checkouts.items():
        for record in inspector.tree(checkout, depth):
            records.setdefault(record["directory"], record)
    return [
        {
            **{key: value for key, value in record.items() if key != "owns_root"},
            "selected": directory in selected,
            "selected_boundaries": sorted(set(selected.get(directory, []))),
        }
        for directory, record in sorted(records.items())
    ]


def _lock(
    lock: Mapping[str, Any], boundaries: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Mark which entries of a west manifest have a selected boundary inside their checkout."""
    gaps: list[dict[str, Any]] = []
    entries = []
    topdir = (
        Path(lock["workspace"]["topdir"])
        if lock.get("workspace", {}).get("state") == "bound"
        else None
    )
    for entry in lock["entries"]:
        entry = dict(entry)
        if topdir is None:
            entry["selection"] = "not-determined"
        else:
            project = topdir / entry["path"]
            inside = sorted(
                boundary["id"]
                for boundary in boundaries
                if _contains(project.resolve(), Path(boundary["directory"]).resolve())
            )
            entry["selection"] = "selected" if inside else "candidate"
            entry["selected_boundaries"] = inside
            state = entry["checkout"]["state"]
            if inside and state == "differs":
                gaps.append(
                    {
                        "kind": "checkout-gitlink-mismatch",
                        "path": str(project),
                        "gitlink": entry["revision"],
                        "checkout": entry["checkout"]["head"],
                        "pin_source": "west-manifest",
                        "manifest": lock["path"],
                        "project": entry["name"],
                    }
                )
            elif inside and state == "not-a-checkout":
                gaps.append(
                    {
                        "kind": "repository-provenance-unavailable",
                        "path": str(project),
                        "reason": "the west project directory is not a Git checkout",
                        "manifest": lock["path"],
                        "project": entry["name"],
                    }
                )
        entries.append(entry)
    return {**lock, "entries": entries}, gaps


def summarise(
    boundaries: Sequence[Mapping[str, Any]], inspector: SubmoduleInspector
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The ``fetch_provenance`` summary and the gaps that selected fetched checkouts raise.

    Submodule gaps (changed pin, dirty tree, local override) come from the repository
    components of selected checkouts. This adds the gaps for west projects whose checkout is
    selected but is not at the manifest's pinned commit.
    """
    locks: dict[str, Mapping[str, Any]] = {}
    for boundary in boundaries:
        for candidate in boundary.get("candidates", []):
            if candidate["kind"] == "lock-file":
                locks.setdefault(candidate["path"], candidate)
    gaps: list[dict[str, Any]] = []
    summarised = []
    for path in sorted(locks):
        item, found = _lock(locks[path], boundaries)
        summarised.append(item)
        gaps.extend(found)
    return {"submodules": _submodules(boundaries, inspector), "locks": summarised}, gaps
