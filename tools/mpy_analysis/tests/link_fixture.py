"""Synthetic `mpy-link-evidence/v1` files for tests that consume evidence without a real build."""

import hashlib
import json
from pathlib import Path

from conftest import fake_elf
from mpy_analysis.link_evidence import SCHEMA


def _record(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def image_record(build_dir: Path, image_id: str, role: str, sources: list[dict], **extra) -> dict:
    """One image: fake ELF and map under `build_dir`, linking the given `{root, path}` sources."""
    elf, map_file = build_dir / f"{image_id}.elf", build_dir / f"{image_id}.map"
    elf.write_bytes(fake_elf() + image_id.encode())
    map_file.write_bytes(b"link map")
    return {
        "id": image_id,
        "role": role,
        "elf": _record(elf),
        "map": _record(map_file),
        "artifacts": [],
        "dynamic_needed": [],
        "sources": sources,
        "generated_sources": [],
        "unbound_sources": [],
        "header_accounting": [],
        "runtime": [],
        "unresolved": [],
        **extra,
    }


def write_evidence(path: Path, images: list[dict], toolchain: list[dict] | None = None) -> Path:
    body = {"schema": SCHEMA, "toolchain": toolchain or [], "images": images}
    body["evidence_id"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(body))
    return path


def link_evidence(build_dir: Path, source_path: str = "selected.c") -> Path:
    """Evidence of one application image that links `source_path` of the root `app`."""
    build_dir.mkdir()
    image = image_record(build_dir, "application", "application", [{"root": "app", "path": source_path}])
    return write_evidence(build_dir / "evidence.json", [image])
