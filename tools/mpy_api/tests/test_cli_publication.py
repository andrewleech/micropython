"""A changed reference input must not become a published fresh generation."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mpy_api.cli import write_generation
from mpy_api.enrichment import load_enrichment, verify_enrichment


def reference(tmp_path):
    tree = tmp_path / "reference"
    tree.mkdir()
    declaration = tree / "fixture.pyi"
    declaration.write_text("def value() -> int: ...\n")
    notice = tmp_path / "NOTICE.txt"
    notice.write_text("Controlled test fixture; external authorship and redistribution rights are not asserted.\n")

    def pin(path, relative):
        content = path.read_bytes()
        return {"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    source_hash = hashlib.sha256(declaration.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "kind": "mpy-api-pyi-enrichment", "tree": "reference",
        "producer": {"name": "controlled-publication-fixture", "version": "1",
                     "source": {"uri": "urn:mpy-api-test:" + source_hash, "revision": source_hash}},
        "sources": [{"id": "fixture", "role": "documentation", "uri": "urn:mpy-api-test:" + source_hash,
                     "revision": None, "version": "1", "content_sha256": source_hash, "evidence": []}],
        "files": [{**pin(declaration, "fixture.pyi"), "module": "fixture",
                   "source_ids": ["fixture"], "license_ids": ["controlled"]}],
        "licenses": [{"id": "controlled", "expression": "LicenseRef-Controlled-Test-Fixture",
                      "copyright": "Controlled fixture; external authorship is not asserted",
                      "notices": [pin(notice, "NOTICE.txt")], "modified": False}],
        "target": None,
    }))
    return load_enrichment(manifest), declaration, notice


@pytest.mark.parametrize("mutation", ["declaration", "notice", "membership"])
def test_reference_changed_while_staging_is_not_published(tmp_path, monkeypatch, mutation):
    bundle, declaration, notice = reference(tmp_path)
    output = tmp_path / "published"
    write_bytes = Path.write_bytes
    changed = False

    def staging_write(path, data):
        nonlocal changed
        result = write_bytes(path, data)
        if path.name == "inventory.json" and not changed:
            changed = True
            if mutation == "declaration":
                write_bytes(declaration, b"def value() -> str: ...\n")
            elif mutation == "notice":
                write_bytes(notice, b"Changed controlled notice.\n")
            else:
                write_bytes(declaration.parent / "extra.pyi", b"def extra() -> int: ...\n")
        return result

    monkeypatch.setattr(Path, "write_bytes", staging_write)
    with pytest.raises(ValueError):
        write_generation(output, {"build": {"fixture": "controlled"}, "coverage": {}},
                         {"modules/fixture.pyi": "def value() -> int: ...\n"},
                         {"enrichment": bundle["provenance"]},
                         notices=bundle["notice_artifacts"],
                         verify=lambda: verify_enrichment(bundle))
    assert changed
    assert not output.exists()
    assert not list(tmp_path.glob(".mpy-api-*"))


@pytest.mark.parametrize("field", ["port", "target", "variant"])
def test_command_rejects_known_target_mismatch_before_extraction(tmp_path, field):
    bundle, declaration, _ = reference(tmp_path)
    manifest_path = Path(bundle["provenance"]["manifest"]["path"])
    manifest = json.loads(manifest_path.read_text())
    data = declaration.read_bytes()
    config = {"port": "unix", "target": "fixture", "variant": "standard"}
    manifest["target"] = {**config, "inputs": [{"path": str(declaration.resolve()),
                                              "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
    manifest_path.write_text(json.dumps(manifest))
    config[field] = "different"
    absent = tmp_path / "absent-input"
    output = tmp_path / "published"
    command = [sys.executable, "-c", "from mpy_api.cli import main; raise SystemExit(main())",
               "--micropython-root", str(tmp_path), "--build-dir", str(tmp_path),
               "--compdb", str(absent), "--elf", str(absent), "--map", str(absent),
               "--link-command", str(absent), "--enrichment", str(manifest_path), "--output", str(output)]
    for key, value in config.items():
        command.extend(["--" + key, value])
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([
        str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")])}
    result = subprocess.run(command, text=True, capture_output=True, env=environment)
    assert result.returncode == 1
    assert "exact-build target" in result.stderr
    assert not result.stdout
    assert not output.exists()
