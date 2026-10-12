"""Behavioural checks for immutable firmware build/link receipts."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from mpy_analysis.firmware_receipt import (
    FirmwareReceiptError,
    receipt_id,
    validate_firmware_receipt,
)


def path_record(path: Path, content: bytes, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), **fields}


def make_receipt(root: Path):
    compiler = path_record(root / "tools" / "cc", b"compiler", role="compiler", version="test-1")
    obj = path_record(root / "build" / "unit.o", b"object", role="object")
    link_operand = {**obj, "role": "link-operand"}
    elf = path_record(root / "build" / "firmware.elf", b"elf", role="elf")
    map_file = path_record(root / "build" / "firmware.map", b"map", role="map")
    binary = path_record(root / "build" / "firmware.bin", b"binary", role="binary")
    build_id = "a" * 64
    compile_action = {
        "id": "compile-1",
        "build_id": build_id,
        "kind": "compile",
        "image": "application",
        "argv": [str(compiler["path"]), "-c", "unit.c", "-o", obj["path"]],
        "cwd": str(root),
        "exit_code": 0,
        "executable": compiler,
        "inputs": [path_record(root / "src" / "unit.c", b"source", role="source")],
        "outputs": [obj],
    }
    link_action = {
        "id": "link-1",
        "build_id": build_id,
        "kind": "link",
        "argv": [str(compiler["path"]), obj["path"], "-Wl,-Map=firmware.map", "-o", elf["path"]],
        "cwd": str(root),
        "exit_code": 0,
        "executable": compiler,
        "inputs": [link_operand],
        "outputs": [elf, map_file],
    }
    transform_action = {
        "id": "transform-1",
        "build_id": build_id,
        "kind": "transform",
        "argv": ["objcopy", elf["path"], binary["path"]],
        "cwd": str(root),
        "exit_code": 0,
        "executable": compiler,
        "inputs": [elf],
        "outputs": [binary],
    }
    receipt = {
        "schema": "mpy-firmware-build/v1",
        "build_id": build_id,
        "configuration": {
            "port": "test",
            "target": "test-board",
            "variant": "standard",
            "variables": {"LTO": "0"},
        },
        "source_repositories": [
            {"name": "firmware", "path": str(root), "revision": "b" * 40, "tree_sha256": "c" * 64, "dirty": False}
        ],
        "toolchain": [{"role": "compiler", "version": "test-1", "executable": compiler}],
        "actions": [compile_action, link_action, transform_action],
        "images": [
            {
                "id": "application",
                "build_id": build_id,
                "role": "application",
                "link_action": "link-1",
                "link": {
                    "adapter": "test-linker/v1",
                    "linker_format": "test-map/v1",
                    "status": "complete",
                    "elf": elf,
                    "map": map_file,
                    "operands": [
                        {
                            "kind": "object",
                            "input": link_operand,
                            "disposition": "contributed",
                            "source_bindings": [{"root": "firmware", "path": "src/unit.c"}],
                            "evidence": "map-section:.text",
                        }
                    ],
                    "discarded_sections": [],
                    "unresolved": [],
                    "lto": "disabled",
                },
                "object_accounting": [
                    {
                        "object": obj,
                        "disposition": "contributed",
                        "source_bindings": [{"root": "firmware", "path": "src/unit.c"}],
                        "evidence": "compile-action:compile-1",
                    }
                ],
                "artifacts": [elf, binary],
            }
        ],
        "status": "complete",
    }
    receipt["receipt_id"] = receipt_id(receipt)
    return receipt, {"compiler": compiler, "obj": obj, "elf": elf, "map": map_file, "binary": binary}


def test_complete_receipt_binds_successful_link_and_deployment_outputs(tmp_path):
    receipt, _ = make_receipt(tmp_path)

    assert validate_firmware_receipt(receipt)["status"] == "complete"


def test_modified_link_input_makes_receipt_stale(tmp_path):
    receipt, files = make_receipt(tmp_path)
    Path(files["obj"]["path"]).write_bytes(b"changed object")

    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(receipt)

@pytest.mark.parametrize("artifact_name", ["elf", "map"])
def test_modified_firmware_artifact_makes_receipt_stale(tmp_path, artifact_name):
    receipt, files = make_receipt(tmp_path)
    Path(files[artifact_name]["path"]).write_bytes(b"changed artifact")

    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(receipt)


def test_missing_link_action_cannot_be_receipted(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["actions"] = [action for action in receipt["actions"] if action["kind"] != "link"]
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="exactly one successful link action"):
        validate_firmware_receipt(receipt)


def test_elf_must_be_output_of_captured_image_link(tmp_path):
    receipt, files = make_receipt(tmp_path)
    other_elf = path_record(tmp_path / "other.elf", b"other elf", role="elf")
    receipt["images"][0]["link"]["elf"] = other_elf
    receipt["images"][0]["artifacts"][0] = other_elf
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="not the output"):
        validate_firmware_receipt(receipt)


def test_complete_status_rejects_unresolved_link_inputs(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["images"][0]["link"]["unresolved"] = [
        {"path": "unknown.a", "reason": "unsupported linker map"}
    ]
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="unresolved link evidence"):
        validate_firmware_receipt(receipt)


def test_nonzero_build_action_cannot_be_receipted_as_success(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["actions"][0]["exit_code"] = 1
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="did not succeed"):
        validate_firmware_receipt(receipt)


def test_image_must_reference_its_own_build_and_successful_link_action(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["images"][0]["link_action"] = "compile-1"
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="exactly one successful link action"):
        validate_firmware_receipt(receipt)


def test_lto_cannot_be_claimed_complete_without_qualified_adapter(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["images"][0]["link"]["lto"] = "unsupported"
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="unresolved link evidence"):
        validate_firmware_receipt(receipt)


def test_lto_flags_require_qualified_linker_evidence(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["configuration"]["variables"]["LTO"] = "1"
    receipt["actions"][0]["argv"].append("-flto=auto")
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="without a qualified adapter"):
        validate_firmware_receipt(receipt)


def test_qualified_lto_requires_explicit_evidence(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["configuration"]["variables"]["LTO"] = "1"
    receipt["actions"][0]["argv"].append("-flto=auto")
    receipt["images"][0]["link"]["lto"] = "qualified"
    receipt["images"][0]["link"]["lto_evidence"] = "captured compiler-plugin and link-map profile"
    receipt["receipt_id"] = receipt_id(receipt)

    assert validate_firmware_receipt(receipt)["status"] == "complete"


def test_receipt_id_detects_changed_build_metadata(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["configuration"]["variant"] = "changed"

    with pytest.raises(FirmwareReceiptError, match="receipt_id"):
        validate_firmware_receipt(receipt)


def test_receipt_requires_absolute_cwd_and_tool_executable(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    receipt["actions"][0]["cwd"] = "."
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="cwd must be absolute"):
        validate_firmware_receipt(receipt)


def test_source_bindings_reject_noncanonical_paths(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    for binding in (
        receipt["images"][0]["object_accounting"][0]["source_bindings"][0],
        receipt["images"][0]["link"]["operands"][0]["source_bindings"][0],
    ):
        binding["path"] = "../src/unit.c"
    receipt["receipt_id"] = receipt_id(receipt)

    with pytest.raises(FirmwareReceiptError, match="canonical contained path"):
        validate_firmware_receipt(receipt)


def test_receipt_validation_does_not_mutate_canonical_input(tmp_path):
    receipt, _ = make_receipt(tmp_path)
    original = json.loads(json.dumps(receipt))

    validate_firmware_receipt(receipt)

    assert receipt == original


def test_actual_native_archive_selection_is_bound_to_elf(tmp_path):
    compiler, archiver, objcopy = (shutil.which(name) for name in ("cc", "ar", "objcopy"))
    if not all((compiler, archiver, objcopy)):
        pytest.skip("host C compiler, ar and objcopy are required")

    source_dir = tmp_path / "src"
    build_dir = tmp_path / "build"
    source_dir.mkdir()
    build_dir.mkdir()
    sources = {
        "main.c": b"int used(void); int main(void) { return used(); }\n",
        "used.c": b"int used(void) { return 0; }\n",
        "unused.c": b"int unused(void) { return 1; }\n",
        "discarded.c": b"int discarded(void) { return 2; }\n",
    }
    for name, content in sources.items():
        (source_dir / name).write_bytes(content)

    build_id = "d" * 64

    def identity(path, role):
        path = Path(path).resolve()
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "role": role,
        }

    def execute(action_id, kind, executable, argv, inputs, outputs, **extra):
        subprocess.run(argv, cwd=tmp_path, check=True, capture_output=True)
        return {
            "id": action_id,
            "build_id": build_id,
            "kind": kind,
            "image": "application" if kind in ("compile", "assemble") else None,
            "argv": list(argv),
            "cwd": str(tmp_path),
            "exit_code": 0,
            "executable": identity(executable, "tool"),
            "inputs": inputs,
            "outputs": [identity(path, role) for path, role in outputs],
            **extra,
        }

    objects = {
        name: build_dir / f"{Path(name).stem}.o"
        for name in sources
    }
    actions = []
    for name, source in sources.items():
        actions.append(
            execute(
                f"compile-{Path(name).stem}",
                "compile",
                compiler,
                [compiler, "-ffunction-sections", "-c", str(source_dir / name), "-o", str(objects[name])],
                [identity(source_dir / name, "source")],
                [(objects[name], "object")],
            )
        )
    archive = build_dir / "libfixture.a"
    actions.append(
        execute(
            "archive-1",
            "archive",
            archiver,
            [archiver, "rcs", str(archive), str(objects["used.c"]), str(objects["unused.c"])],
            [identity(objects["used.c"], "archive-member"), identity(objects["unused.c"], "archive-member")],
            [(archive, "archive")],
        )
    )
    elf, map_file, binary = build_dir / "firmware.elf", build_dir / "firmware.map", build_dir / "firmware.bin"
    link_argv = [
        compiler,
        str(objects["main.c"]),
        str(objects["discarded.c"]),
        str(archive),
        "-Wl,--gc-sections",
        f"-Wl,-Map={map_file}",
        "-o",
        str(elf),
    ]
    link_inputs = [
        identity(objects["main.c"], "link-operand"),
        identity(objects["discarded.c"], "link-operand"),
        identity(archive, "link-operand"),
    ]
    actions.append(
        execute(
            "link-1",
            "link",
            compiler,
            link_argv,
            link_inputs,
            [(elf, "elf"), (map_file, "map")],
        )
    )
    actions.append(
        execute(
            "objcopy-1",
            "transform",
            objcopy,
            [objcopy, "-O", "binary", str(elf), str(binary)],
            [identity(elf, "elf")],
            [(binary, "binary")],
        )
    )

    map_text = map_file.read_text()

    assert "libfixture.a(used.o)" in map_text
    assert "libfixture.a(unused.o)" not in map_text
    assert ".text.discarded" in map_text
    assert "Discarded input sections" in map_text
    assert "discarded.o" in map_text
    archive_members = subprocess.run(
        [archiver, "t", str(archive)], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert archive_members == ["used.o", "unused.o"]
    for name, member in (("used.c", "used.o"), ("unused.c", "unused.o")):
        archived = subprocess.run(
            [archiver, "p", str(archive), member], check=True, capture_output=True
        ).stdout
        assert hashlib.sha256(archived).hexdigest() == hashlib.sha256(objects[name].read_bytes()).hexdigest()
    source_bindings = {
        name: [{"root": "firmware", "path": f"src/{name}"}]
        for name in sources
    }
    archive_bytes = archive.read_bytes()
    receipt = {
        "schema": "mpy-firmware-build/v1",
        "build_id": build_id,
        "configuration": {
            "port": "native",
            "target": "test",
            "variant": "standard",
            "variables": {"LTO": "0", "gc_sections": "1"},
        },
        "source_repositories": [{
            "name": "firmware",
            "path": str(tmp_path),
            "revision": "e" * 40,
            "tree_sha256": hashlib.sha256(b"".join(sources.values())).hexdigest(),
            "dirty": False,
        }],
        "toolchain": [
            {"role": "compiler", "version": "host", "executable": identity(compiler, "compiler")},
            {"role": "archiver", "version": "host", "executable": identity(archiver, "archiver")},
            {"role": "objcopy", "version": "host", "executable": identity(objcopy, "objcopy")},
        ],
        "actions": actions,
        "images": [{
            "id": "application",
            "build_id": build_id,
            "role": "application",
            "link_action": "link-1",
            "link": {
                "adapter": "test-gnu-map/v1",
                "linker_format": "gnu-ld-map",
                "status": "complete",
                "elf": identity(elf, "elf"),
                "map": identity(map_file, "map"),
                "operands": [
                    {
                        "kind": "object",
                        "input": link_inputs[0],
                        "disposition": "contributed",
                        "source_bindings": source_bindings["main.c"],
                        "evidence": "GNU map allocated main.o sections",
                    },
                    {
                        "kind": "object",
                        "input": link_inputs[1],
                        "disposition": "discarded",
                        "source_bindings": source_bindings["discarded.c"],
                        "evidence": "GNU map discarded .text.discarded",
                    },
                    {
                        "kind": "archive",
                        "input": link_inputs[2],
                        "disposition": "contributed",
                        "source_bindings": source_bindings["used.c"] + source_bindings["unused.c"],
                        "members_complete": True,
                        "members": [
                            {
                                "name": "used.o",
                                "sha256": hashlib.sha256(objects["used.c"].read_bytes()).hexdigest(),
                                "disposition": "contributed",
                                "source_bindings": source_bindings["used.c"],
                                "evidence": "GNU map archive-member extraction",
                            },
                            {
                                "name": "unused.o",
                                "sha256": hashlib.sha256(objects["unused.c"].read_bytes()).hexdigest(),
                                "disposition": "not-extracted",
                                "evidence": "ar member inventory absent from GNU map extraction list",
                            },
                        ],
                        "evidence": f"archive SHA-256 {hashlib.sha256(archive_bytes).hexdigest()}",
                    },
                ],
                "discarded_sections": [
                    {
                        "operand": link_inputs[1]["path"],
                        "section": ".text.discarded",
                        "evidence": "GNU ld Discarded input sections",
                    }
                ],
                "unresolved": [],
                "lto": "disabled",
            },
            "object_accounting": [
                {
                    "object": identity(objects[name], "object"),
                    "disposition": (
                        "discarded"
                        if name == "discarded.c"
                        else "not-linked"
                        if name == "unused.c"
                        else "contributed"
                    ),
                    **({"reason": "archive member was not extracted"} if name == "unused.c" else {}),
                    "source_bindings": source_bindings[name],
                    "evidence": "compiler action and GNU link map",
                }
                for name in sources
            ],
            "artifacts": [identity(elf, "elf"), identity(binary, "binary")],
        }],
        "status": "complete",
    }
    receipt["receipt_id"] = receipt_id(receipt)

    assert validate_firmware_receipt(receipt)["status"] == "complete"


def test_unsupported_object_can_preserve_missing_source_boundary(tmp_path):
    receipt, files = make_receipt(tmp_path)
    obj = path_record(tmp_path / "build" / "generated.o", b"generated", role="object")
    receipt["actions"].append(
        {
            "id": "compile-generated",
            "build_id": receipt["build_id"],
            "kind": "compile",
            "image": "application",
            "argv": [files["compiler"]["path"], "-c", "generated.c", "-o", obj["path"]],
            "cwd": str(tmp_path),
            "exit_code": 0,
            "executable": files["compiler"],
            "inputs": [],
            "outputs": [obj],
        }
    )
    receipt["images"][0]["object_accounting"].append(
        {
            "object": obj,
            "disposition": "unsupported",
            "reason": "generated source is outside declared source roots",
            "source_bindings": [],
            "evidence": "captured compiler output without a source boundary",
        }
    )
    receipt["images"][0]["link"]["status"] = "incomplete"
    receipt["status"] = "incomplete"
    receipt["receipt_id"] = receipt_id(receipt)

    assert validate_firmware_receipt(receipt)["status"] == "incomplete"
