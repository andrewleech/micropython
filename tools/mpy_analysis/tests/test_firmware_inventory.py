import hashlib
import json
from pathlib import Path

import pytest
from mpy_analysis.firmware_inventory import (
    FirmwareInventoryError,
    load_posthoc_link_inventory,
)


def _write(path: Path, data: bytes) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest()


def make_inventory(tmp_path: Path, source: Path) -> Path:
    build_dir = tmp_path / "build"
    elf, elf_hash = _write(build_dir / "micropython.elf", b"elf")
    map_path, map_hash = _write(build_dir / "micropython.map", b"map")
    linker, linker_hash = _write(build_dir / "ld", b"linker")
    object_path, object_hash = _write(build_dir / "selected.o", b"object")
    archive, archive_hash = _write(build_dir / "libc.a", b"archive")
    source_path, source_hash = str(source.resolve()), hashlib.sha256(source.read_bytes()).hexdigest()
    argv_hash = hashlib.sha256(b"cc -c selected.c").hexdigest()

    consumed = {}
    for path, digest in (
        (elf, elf_hash),
        (map_path, map_hash),
        (linker, linker_hash),
        (object_path, object_hash),
        (archive, archive_hash),
        (source_path, source_hash),
    ):
        consumed[path] = {"resolved": str(Path(path).resolve()), "sha256": digest}

    link = {
        "elf": elf,
        "elf_sha256": elf_hash,
        "map": map_path,
        "map_sha256": map_hash,
        "linker": linker,
        "linker_sha256": linker_hash,
    }
    inventory = {
        "schema_version": 1,
        "build": {
            "port": "unix",
            "target": "unix",
            "variant": "minimal",
            "root": str(tmp_path),
            "source_revision": "a" * 40,
            "consumed_inputs": consumed,
            "link": link,
        },
        "provenance": {
            "ownership": {
                "selected_link_authority": {
                    **link,
                    "link_inputs": {object_path: object_hash, archive: archive_hash},
                },
                "objects": [
                    {
                        "object": {"path": object_path, "sha256": object_hash},
                        "translation_unit": f"{source_path}::object:{object_path}::argv:{argv_hash}",
                        "allocations": [{"section": ".text", "size": 4}],
                    }
                ],
                "unassessed_objects": [str(build_dir / "unassessed.o")],
            }
        },
    }
    inventory_path = tmp_path / "inventory.json"
    payload = json.dumps(inventory, sort_keys=True).encode()
    inventory_path.write_bytes(payload)
    generation = {
        "schema_version": 1,
        "generation_id": "b" * 64,
        "artifacts": [
            {
                "role": "inventory",
                "path": inventory_path.name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    (tmp_path / "generation.json").write_text(json.dumps(generation))
    return inventory_path


def test_posthoc_inventory_verifies_generation_and_bound_inputs(tmp_path):
    source = tmp_path / "selected.c"
    source.write_bytes(b"int selected(void) { return 1; }\n")
    path = make_inventory(tmp_path, source)

    evidence = load_posthoc_link_inventory(path)

    assert evidence["authority"] == "posthoc-published-inventory"
    assert evidence["status"] == "incomplete"
    assert evidence["generation"]["id"] == "b" * 64
    assert evidence["image"]["elf"]["sha256"] == hashlib.sha256(
        (tmp_path / "build/micropython.elf").read_bytes()
    ).hexdigest()
    assert evidence["contributions"] == [
        {
            "object": {
                "path": str((tmp_path / "build/selected.o").resolve()),
                "sha256": hashlib.sha256(b"object").hexdigest(),
            },
            "source": {
                "path": str(source.resolve()),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
            "allocation_count": 1,
            "authority": "posthoc-published-ownership-and-map",
        }
    ]
    assert len(evidence["unresolved_inputs"]) == 1
    assert len(evidence["unassessed_objects"]) == 1


def test_posthoc_inventory_rejects_changed_generation_artifact(tmp_path):
    source = tmp_path / "selected.c"
    source.write_bytes(b"source")
    path = make_inventory(tmp_path, source)
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(FirmwareInventoryError, match="generation artifact index"):
        load_posthoc_link_inventory(path)


def test_posthoc_inventory_rejects_stale_link_input(tmp_path):
    source = tmp_path / "selected.c"
    source.write_bytes(b"source")
    path = make_inventory(tmp_path, source)
    (tmp_path / "build/selected.o").write_bytes(b"changed object")

    with pytest.raises(FirmwareInventoryError, match="no longer matches"):
        load_posthoc_link_inventory(path)
