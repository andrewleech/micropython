"""Consume published linker inventories without treating them as build-time capture."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA = "mpy-posthoc-link-inventory/v1"


class FirmwareInventoryError(ValueError):
    """The published link inventory is malformed, stale, or mismatched."""


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise FirmwareInventoryError(f"{label} must be an object")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise FirmwareInventoryError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _file_digest(path: Path, label: str) -> str:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise FirmwareInventoryError(f"cannot read {label}: {path}: {error}") from error
    return digest


def _verified_path(path: Any, expected: Any, label: str) -> dict[str, str]:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise FirmwareInventoryError(f"{label} path must be absolute")
    expected = _digest(expected, f"{label}.sha256")
    actual = _file_digest(Path(path), label)
    if actual != expected:
        raise FirmwareInventoryError(f"{label} no longer matches the published SHA-256")
    return {"path": str(Path(path).resolve()), "sha256": expected}


def _source_from_translation_unit(identity: Any, object_path: str) -> Path:
    if not isinstance(identity, str):
        raise FirmwareInventoryError("ownership translation_unit must be a string")
    marker = f"::object:{object_path}::argv:"
    index = identity.rfind(marker)
    if index <= 0:
        raise FirmwareInventoryError(
            f"cannot bind ownership translation unit to exact object path: {object_path}"
        )
    source = identity[:index]
    argv_digest = identity[index + len(marker) :]
    _digest(argv_digest, "ownership translation_unit argv digest")
    path = Path(source)
    if not path.is_absolute():
        raise FirmwareInventoryError("ownership translation unit source path must be absolute")
    return path.resolve()


def load_posthoc_link_inventory(
    inventory_path: Path, *, generation_path: Path | None = None
) -> dict[str, Any]:
    """Load a qualified schema-v1 inventory, preserving its posthoc evidence authority."""
    inventory_path = Path(inventory_path).resolve(strict=True)
    generation_path = (
        Path(generation_path).resolve(strict=True)
        if generation_path is not None
        else (inventory_path.parent / "generation.json").resolve(strict=True)
    )
    try:
        inventory_bytes = inventory_path.read_bytes()
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        inventory = json.loads(inventory_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FirmwareInventoryError(f"cannot load published inventory/generation: {error}") from error

    if not isinstance(generation, dict) or generation.get("schema_version") != 1:
        raise FirmwareInventoryError("generation receipt must use schema_version 1")
    generation_id = _digest(generation.get("generation_id"), "generation_id")
    inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
    artifact_rows = generation.get("artifacts")
    if not isinstance(artifact_rows, list):
        raise FirmwareInventoryError("generation artifacts must be an array")
    matches = [
        item
        for item in artifact_rows
        if isinstance(item, dict)
        and item.get("role") == "inventory"
        and item.get("path") == inventory_path.name
    ]
    if len(matches) != 1:
        raise FirmwareInventoryError("generation receipt must index this inventory exactly once")
    artifact = matches[0]
    if artifact.get("bytes") != len(inventory_bytes) or artifact.get("sha256") != inventory_hash:
        raise FirmwareInventoryError("published inventory does not match the generation artifact index")
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        raise FirmwareInventoryError("published mpy-api inventory must use schema_version 1")

    build = _mapping(inventory.get("build"), "inventory.build")
    provenance = _mapping(inventory.get("provenance"), "inventory.provenance")
    ownership = _mapping(provenance.get("ownership"), "provenance.ownership")
    consumed = _mapping(build.get("consumed_inputs"), "build.consumed_inputs")
    link = _mapping(build.get("link"), "build.link")
    selected = _mapping(ownership.get("selected_link_authority"), "ownership.selected_link_authority")
    for field in ("elf", "elf_sha256", "map", "map_sha256", "linker", "linker_sha256"):
        if link.get(field) != selected.get(field):
            raise FirmwareInventoryError(f"build.link and ownership disagree on {field}")

    build_identity = {}
    for field in ("port", "target", "variant", "root", "source_revision"):
        value = build.get(field)
        if not isinstance(value, str) or not value:
            raise FirmwareInventoryError(f"build.{field} must be a nonempty string")
        build_identity[field] = value

    consumed_by_resolved = {}
    for key, raw in consumed.items():
        row = _mapping(raw, f"build.consumed_inputs[{key!r}]")
        resolved = row.get("resolved")
        digest = _digest(row.get("sha256"), f"build.consumed_inputs[{key!r}].sha256")
        if not isinstance(resolved, str) or not Path(resolved).is_absolute():
            raise FirmwareInventoryError(f"consumed input {key!r} has no absolute resolved path")
        consumed_by_resolved.setdefault(str(Path(resolved).resolve()), []).append(digest)

    def verify_consumed(path: Any, expected: Any, label: str) -> dict[str, str]:
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise FirmwareInventoryError(f"{label} path must be absolute")
        expected = _digest(expected, f"{label}.sha256")
        resolved = str(Path(path).resolve())
        if expected not in consumed_by_resolved.get(resolved, []):
            raise FirmwareInventoryError(
                f"{label} has no exact consumed-input record for resolved path: {resolved}"
            )
        return _verified_path(path, expected, label)

    elf = verify_consumed(link.get("elf"), link.get("elf_sha256"), "linked ELF")
    map_file = verify_consumed(link.get("map"), link.get("map_sha256"), "linker map")
    linker = verify_consumed(link.get("linker"), link.get("linker_sha256"), "linker executable")
    operands = _mapping(selected.get("link_inputs"), "selected link inputs")
    verified_operands = []
    operands_by_resolved = {}
    for raw_path, raw_digest in sorted(operands.items()):
        if not isinstance(raw_path, str):
            raise FirmwareInventoryError("link input paths must be strings")
        digest = _digest(raw_digest, f"link input {raw_path} SHA-256")
        verified = verify_consumed(raw_path, digest, "link input")
        verified_operands.append(verified)
        operands_by_resolved[verified["path"]] = verified["sha256"]

    object_rows = ownership.get("objects")
    if not isinstance(object_rows, list):
        raise FirmwareInventoryError("ownership.objects must be an array")
    contributions = []
    no_contribution = []
    ownership_paths = set()
    for index, raw in enumerate(object_rows):
        row = _mapping(raw, f"ownership.objects[{index}]")
        object_record = _mapping(row.get("object"), f"ownership.objects[{index}].object")
        object_path = object_record.get("path")
        object_hash = _digest(object_record.get("sha256"), "ownership object SHA-256")
        if not isinstance(object_path, str) or not Path(object_path).is_absolute():
            raise FirmwareInventoryError("ownership object path must be absolute")
        if str(Path(object_path).resolve()) in ownership_paths:
            raise FirmwareInventoryError("ownership object paths must be unique")
        verified_object = verify_consumed(object_path, object_hash, "compiler object")
        ownership_paths.add(verified_object["path"])
        if operands_by_resolved.get(verified_object["path"]) != object_hash:
            raise FirmwareInventoryError(f"ownership object is not an exact linker input: {object_path}")
        allocations = row.get("allocations")
        if not isinstance(allocations, list):
            raise FirmwareInventoryError(f"ownership object allocations must be an array: {object_path}")
        if not allocations:
            no_contribution.append(
                {"object": verified_object, "authority": "posthoc-map-no-allocations"}
            )
            continue
        source = _source_from_translation_unit(row.get("translation_unit"), object_path)
        source_row = consumed.get(str(source))
        if not isinstance(source_row, dict) or source_row.get("resolved") != str(source):
            raise FirmwareInventoryError(f"contributing source has no exact consumed-input record: {source}")
        source_digest = _digest(source_row.get("sha256"), f"source SHA-256: {source}")
        verified_source = verify_consumed(str(source), source_digest, "contributing source")
        contributions.append(
            {
                "object": verified_object,
                "source": verified_source,
                "allocation_count": len(allocations),
                "authority": "posthoc-published-ownership-and-map",
            }
        )

    unassessed = ownership.get("unassessed_objects")
    if not isinstance(unassessed, list):
        raise FirmwareInventoryError("ownership.unassessed_objects must be an array")
    unresolved_inputs = [
        {
            "path": item["path"],
            "sha256": item.get("sha256"),
            "reason": "link input has no source-bound ownership record",
        }
        for item in verified_operands
        if item["path"] not in ownership_paths
    ]
    unresolved_objects = []
    for index, raw in enumerate(unassessed):
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise FirmwareInventoryError(
                f"ownership.unassessed_objects[{index}] must be an absolute path"
            )
        unresolved_objects.append(
            {"path": str(Path(raw).resolve()), "reason": "published ownership marks object unassessed"}
        )

    return {
        "schema": _SCHEMA,
        "authority": "posthoc-published-inventory",
        "status": "incomplete",
        "generation": {"id": generation_id, "inventory_sha256": inventory_hash},
        "build": build_identity,
        "image": {
            "id": "firmware-" + generation_id[:16],
            "role": "application",
            "elf": elf,
            "map": map_file,
            "linker": linker,
        },
        "contributions": contributions,
        "no_contribution_objects": no_contribution,
        "unresolved_inputs": unresolved_inputs,
        "unassessed_objects": unresolved_objects,
        "limitations": [
            "Inventory collection happened after the build and does not capture successful build actions.",
            "Archive member inclusion, generated transformations, linker scripts and deployment outputs are not established by this inventory.",
            "Published ownership coverage declares weak/COMDAT contribution unassessed.",
        ],
    }
