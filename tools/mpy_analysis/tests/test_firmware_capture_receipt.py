import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import fake_elf
from mpy_analysis import sbom, scope
from mpy_analysis.firmware_capture import capture_make
from mpy_analysis.firmware_capture_receipt import (
    _bindings,
    _repositories,
    merge_receipts,
    receipt_from_journal,
)
from mpy_analysis.firmware_receipt import validate_firmware_receipt

SECTIONS = [(".text", 0x08000000, 0x10), (".init", 0x08000100, 0x4)]


def _record(path: Path, content: bytes, role: str) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest(), "role": role}


def test_captured_gnu_build_emits_exact_incomplete_receipt(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    source = _record(root / "main.c", b"int main(void) { return 0; }\n", "source")
    subprocess.run(["git", "-C", str(root), "add", "main.c"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)

    build = tmp_path / "build"
    executable_path = tmp_path / "cc"
    executable_path.write_text(f"#!{sys.executable}\nimport sys\nprint('test cc 1') if '--version' in sys.argv else None\n")
    executable_path.chmod(0o755)
    compiler = _record(executable_path, executable_path.read_bytes(), "tool")
    obj = _record(build / "main.o", b"object", "object")
    system_obj = _record(tmp_path / "toolchain" / "crtbegin.o", b"implicit toolchain input", "object")
    elf = _record(build / "firmware.elf", fake_elf(sections=SECTIONS), "elf")
    map_text = (
        f"GNU ld (GNU Binutils) 2.40\nLOAD {obj['path']}\n"
        f"LOAD {system_obj['path']}\n"
        "As-needed library included to satisfy reference by file (symbol)\n"
        "libc.so.6                      /tmp/source/main.c (exit)\n\n"
        "Discarded input sections\n\n"
        "Linker script and memory map\n"
        ".text 0x08000000 0x00000010\n"
        f" .text.main 0x08000000 0x00000010 {obj['path']}\n"
        ".init 0x08000100 0x00000004\n"
        f" .init 0x08000100 0x00000004 {system_obj['path']}\n"
    )
    map_file = _record(build / "firmware.map", map_text.encode(), "map")
    build_id = "a" * 64
    events = [
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": "application",
            "kind": "compile",
            "role": "cc",
            "argv": [compiler["path"], "-c", source["path"], "-o", obj["path"]],
            "cwd": str(root),
            "started_ns": 1,
            "finished_ns": 2,
            "exit_code": 0,
            "executable": compiler,
            "inputs": [source],
            "outputs": [obj],
            "changed_inputs_during_action": [],
            "dependencies_discovered_after_action": [],
        },
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": "application",
            "kind": "link",
            "role": "cc",
            "argv": [compiler["path"], "-o", elf["path"], f"-Wl,-Map={map_file['path']}", obj["path"]],
            "cwd": str(root),
            "started_ns": 3,
            "finished_ns": 4,
            "exit_code": 0,
            "executable": compiler,
            "inputs": [{**obj, "role": "link-operand"}],
            "outputs": [elf, map_file],
            "changed_inputs_during_action": [],
            "dependencies_discovered_after_action": [],
        },
    ]

    stripped_elf = _record(Path(elf["path"]), fake_elf(b"stripped", SECTIONS), "elf")
    events.append(
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": "application",
            "kind": "transform",
            "role": "strip",
            "argv": [compiler["path"], elf["path"]],
            "cwd": str(root),
            "started_ns": 5,
            "finished_ns": 6,
            "exit_code": 0,
            "executable": compiler,
            "inputs": [elf],
            "outputs": [stripped_elf],
            "changed_inputs_during_action": [],
            "dependencies_discovered_after_action": [],
            "dependency_evidence": [],
        }
    )
    firmware_bin = _record(build / "firmware.bin", b"firmware binary", "deployment")
    firmware_dfu = _record(build / "firmware.dfu", b"firmware DFU", "deployment")
    events.append(
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": "application",
            "kind": "generate",
            "role": "python",
            "argv": [compiler["path"], "dfu.py", firmware_dfu["path"]],
            "cwd": str(root),
            "started_ns": 7,
            "finished_ns": 8,
            "exit_code": 0,
            "executable": compiler,
            "inputs": [firmware_bin],
            "outputs": [firmware_dfu],
            "changed_inputs_during_action": [],
            "dependencies_discovered_after_action": [],
            "dependency_evidence": [],
        }
    )

    journal = tmp_path / "actions.jsonl"
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))

    receipt = receipt_from_journal(
        journal,
        build_id=build_id,
        image_id="application",
        image_role="application",
        port="unix",
        target="unix",
        variant="minimal",
        variables={"LTO": "0"},
        source_roots={"firmware": root},
        build_dir=build,
        elf=Path(elf["path"]),
        map_file=Path(map_file["path"]),
    )

    assert receipt["status"] == "incomplete"
    image = receipt["images"][0]
    assert image["link"]["status"] == "incomplete"
    assert image["link"]["operands"][0]["disposition"] == "contributed"
    assert image["link"]["linker_format"] == "gnu-ld-map"
    assert any(
        item["path"] == system_obj["path"]
        and item["reason"].startswith("GNU map LOAD input contributes but is not identified")
        for item in image["link"]["unresolved"]
    )
    assert image["link"]["dynamic_needed"] == []
    assert image["object_accounting"][0]["source_bindings"] == [
        {"root": "firmware", "path": "main.c"}
    ]
    assert image["link"]["elf"]["sha256"] == stripped_elf["sha256"]
    assert validate_firmware_receipt(receipt)["receipt_id"] == receipt["receipt_id"]
    assert firmware_dfu in image["artifacts"]

    mboot_obj = _record(build / "mboot.o", b"bootloader object", "object")
    mboot_elf = _record(build / "mboot.elf", fake_elf(b"bootloader", [(".text", 0x08000000, 0x10)]), "elf")
    mboot_map = _record(
        build / "mboot.map",
        (
            f"GNU ld (GNU Binutils) 2.40\nLOAD {mboot_obj['path']}\n\n"
            "Discarded input sections\n\n"
            "Linker script and memory map\n"
            ".text 0x08000000 0x00000010\n"
            f" .text.start 0x08000000 0x00000010 {mboot_obj['path']}\n"
        ).encode(),
        "map",
    )
    mboot_events = [
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": "bootloader",
            "kind": "compile",
            "role": "cc",
            "argv": [compiler["path"], "-c", source["path"], "-o", mboot_obj["path"]],
            "cwd": str(root),
            "started_ns": 5,
            "finished_ns": 6,
            "exit_code": 0,
            "executable": compiler,
            "inputs": [source],
            "outputs": [mboot_obj],
            "changed_inputs_during_action": [],
            "dependencies_discovered_after_action": [],
        },
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": "bootloader",
            "kind": "link",
            "role": "cc",
            "argv": [
                compiler["path"], "-o", mboot_elf["path"],
                f"-Wl,-Map={mboot_map['path']}", mboot_obj["path"],
            ],
            "cwd": str(root),
            "started_ns": 7,
            "finished_ns": 8,
            "exit_code": 0,
            "executable": compiler,
            "inputs": [{**mboot_obj, "role": "link-operand"}],
            "outputs": [mboot_elf, mboot_map],
            "changed_inputs_during_action": [],
            "dependencies_discovered_after_action": [],
        },
    ]
    with journal.open("a") as stream:
        stream.write("".join(json.dumps(event) + "\n" for event in mboot_events))
    mboot_receipt = receipt_from_journal(
        journal,
        build_id=build_id,
        image_id="bootloader",
        image_role="bootloader",
        port="unix",
        target="unix",
        variant="minimal",
        variables={"LTO": "0", "BUILD": str(build / "bootloader")},
        source_roots={"firmware": root},
        build_dir=build,
        elf=Path(mboot_elf["path"]),
        map_file=Path(mboot_map["path"]),
    )
    combined = merge_receipts([receipt, mboot_receipt])
    assert {image["id"] for image in combined["images"]} == {"application", "bootloader"}
    assert combined["images"][1]["configuration"]["variables"]["BUILD"].endswith("bootloader")
    assert len({action["id"] for action in combined["actions"]}) == len(combined["actions"])
    assert combined["status"] == "incomplete"
    assert validate_firmware_receipt(combined)["receipt_id"] == combined["receipt_id"]


def test_nested_source_root_owns_files_over_parent_root(tmp_path):
    parent = tmp_path / "firmware"
    nested = parent / "lib" / "component"
    source = nested / "source.c"
    source.parent.mkdir(parents=True)
    source.write_text("int source;\n")

    assert _bindings(str(source), {"firmware": parent, "component": nested}) == [
        {"root": "component", "path": "source.c"}
    ]

def test_unassessed_submodule_gitlink_is_recorded(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    (root / "main.c").write_text("int main(void) { return 0; }\n")
    subprocess.run(["git", "-C", str(root), "add", "main.c"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(root), "update-index", "--add", "--cacheinfo", f"160000,{revision},modules/example"],
        check=True,
    )

    [record] = _repositories({"firmware": root})

    assert record["unverified_submodules"] == ["modules/example"]


def test_unavailable_declared_gitlink_root_keeps_parent_revision(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    (root / "main.c").write_text("int main(void) { return 0; }\n")
    subprocess.run(["git", "-C", str(root), "add", "main.c"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    submodule = root / "modules" / "example"
    submodule.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(root), "update-index", "--add", "--cacheinfo", f"160000,{revision},modules/example"],
        check=True,
    )

    [record] = _repositories({"component": submodule})

    assert record["revision"] == revision
    assert record["dirty"] is True
    assert record["unverified_submodules"] == ["."]
    assert record["provenance_status"] == "unverified-gitlink-checkout"

    (submodule / ".git").write_text("gitdir: missing\n")
    [record] = _repositories({"component": submodule})

    assert record["revision"] == revision
    assert record["dirty"] is True
    assert record["unverified_submodules"] == ["."]
    assert record["provenance_status"] == "unverified-gitlink-checkout"



@pytest.mark.skipif(
    not all(
        shutil.which(tool)
        for tool in ("make", "gcc", "g++", "as", "ld", "ar", "objcopy", "strip", "python3")
    ),
    reason="native GNU build tools are unavailable",
)
def test_captured_native_archive_inclusion_uses_map_and_member_bytes(tmp_path):
    root = tmp_path / "source"
    source_root = root / "source"
    make_dir = root / "make"
    (source_root / "left").mkdir(parents=True)
    (source_root / "right").mkdir()
    make_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    (source_root / "manifest.py").write_text('module("main.py")\n')
    (source_root / "main.py").write_text("main = True\n")
    (source_root / "main.c").write_text(
        "extern int used(void), left(void), right(void); "
        "int main(void) { return used() + left() + right(); }\n"
    )
    (source_root / "used.c").write_text("int used(void) { return 0; }\n")
    (source_root / "unused.c").write_text("int unused(void) { return 1; }\n")
    (source_root / "discarded.c").write_text("int discarded(void) { return 2; }\n")
    (source_root / "left" / "shared.c").write_text("int left(void) { return 3; }\n")
    (source_root / "right" / "shared.c").write_text("int right(void) { return 4; }\n")
    subprocess.run(["git", "-C", str(root), "add", "source"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    (make_dir / "Makefile").write_text(
        """ROOT := $(abspath ..)
CFLAGS := -ffunction-sections -fdata-sections -fno-asynchronous-unwind-tables -fno-unwind-tables

.PHONY: all
all: $(BUILD)/firmware.bin

$(BUILD):
\tmkdir -p $@

$(BUILD)/%.o: $(ROOT)/source/%.c | $(BUILD)
\tmkdir -p $(dir $@)
\t$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/libfixture.a: $(BUILD)/used.o $(BUILD)/unused.o
\t$(AR) rcs $@ $^

$(BUILD)/firmware.elf: $(BUILD)/main.o $(BUILD)/discarded.o $(BUILD)/left/shared.o $(BUILD)/right/shared.o $(BUILD)/libfixture.a
\t$(CC) -nostdlib -Wl,-e,main -Wl,--gc-sections -Wl,-Map=$(BUILD)/firmware.map -o $@ $(BUILD)/main.o $(BUILD)/discarded.o $(BUILD)/left/shared.o $(BUILD)/right/shared.o $(BUILD)/libfixture.a

$(BUILD)/firmware.bin: $(BUILD)/firmware.elf
\t$(OBJCOPY) -O binary $< $@
"""
    )
    build = tmp_path / "build"
    receipt_path = tmp_path / "receipt.json"
    journal = tmp_path / "actions.jsonl"
    build_id = "b" * 64
    assert capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=journal,
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="native",
        image_role="application",
        build_id=build_id,
        port="native",
        target="native",
        variant="archive-inclusion",
        source_roots={"fixture": source_root},
        cross_compile="",
        variables=[],
        targets=["all"],
    ) == 0

    receipt = json.loads(receipt_path.read_text())
    image = receipt["images"][0]
    archive = next(item for item in image["link"]["operands"] if item["kind"] == "archive")
    assert archive["disposition"] == "contributed"
    assert [(member["name"], member["disposition"]) for member in archive["members"]] == [
        ("used.o", "contributed"),
        ("unused.o", "not-extracted"),
    ]
    objects = {
        Path(item["object"]["path"]).relative_to(build).as_posix(): (
            item["disposition"],
            item["source_bindings"],
        )
        for item in image["object_accounting"]
    }
    assert objects == {
        "discarded.o": ("discarded", [{"root": "fixture", "path": "discarded.c"}]),
        "left/shared.o": ("contributed", [{"root": "fixture", "path": "left/shared.c"}]),
        "main.o": ("contributed", [{"root": "fixture", "path": "main.c"}]),
        "right/shared.o": ("contributed", [{"root": "fixture", "path": "right/shared.c"}]),
        "unused.o": ("not-linked", [{"root": "fixture", "path": "unused.c"}]),
        "used.o": ("contributed", [{"root": "fixture", "path": "used.c"}]),
    }
    assert validate_firmware_receipt(receipt)["receipt_id"] == receipt["receipt_id"]
    mpy_root = Path(__file__).resolve().parents[3]
    inventory = scope.collect_manifest_scope(
        mpy_root,
        [source_root / "manifest.py"],
        roots={"fixture": str(source_root)},
        path_vars={},
        owners={"fixture": "application"},
        target="native",
        variant="archive-inclusion",
        port="native",
    )
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    compdb = tmp_path / "compile_commands.json"
    compdb.write_text(
        json.dumps(
            [
                {
                    "directory": event["cwd"],
                    "file": next(item["path"] for item in event["inputs"] if item["role"] == "source"),
                    "arguments": event["argv"],
                    "output": next(
                        item["path"]
                        for item in event["outputs"]
                        if Path(item["path"]).suffix.lower() == ".o"
                    ),
                }
                for event in events
                if event["kind"] == "compile"
            ]
        )
    )
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        compdb, roots=inventory["roots"], owners={"fixture": "application"}
    )
    document, summary = sbom.generate_sbom(
        inventory, micropython_root=mpy_root, build_receipt=receipt
    )
    firmware = next(item for item in document["components"] if item["type"] == "firmware")
    dependencies = {item["ref"]: set(item["dependsOn"]) for item in document["dependencies"]}
    file_components = {
        next(prop["value"] for prop in item["properties"] if prop["name"] == "mpy:path"): item["bom-ref"]
        for item in document["components"]
        if item["type"] == "file"
        and any(prop["name"] == "mpy:root" and prop["value"] == "fixture" for prop in item["properties"])
    }
    assert dependencies[firmware["bom-ref"]] == {
        file_components["main.c"],
        file_components["used.c"],
        file_components["left/shared.c"],
        file_components["right/shared.c"],
    }
    assert summary["link_closure"]["status"] == "incomplete"
    assert summary["firmware_completeness"] == "not-established"
    for record in (
        image["link"]["elf"],
        image["link"]["map"],
        archive["input"],
    ):
        path = Path(record["path"])
        original = path.read_bytes()
        path.write_bytes(original + b"stale")
        with pytest.raises(ValueError, match="stale"):
            validate_firmware_receipt(receipt)
        path.write_bytes(original)

    mismatched = json.loads(json.dumps(receipt))
    mismatched["configuration"]["target"] = "wrong-target"
    from mpy_analysis.firmware_capture_receipt import receipt_id

    mismatched["receipt_id"] = receipt_id(mismatched)
    with pytest.raises(ValueError, match="does not match SBOM scope"):
        sbom.generate_sbom(inventory, micropython_root=mpy_root, build_receipt=mismatched)
@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("make", "gcc", "ld")),
    reason="native GNU build tools are unavailable",
)
def test_toolchain_library_with_map_contribution_is_a_component(tmp_path):
    root = tmp_path / "source"
    source_root = root / "src"
    make_dir = root / "make"
    source_root.mkdir(parents=True)
    make_dir.mkdir()
    (source_root / "manifest.py").write_text('module("main.py")\n')
    (source_root / "main.py").write_text("main = True\n")
    (source_root / "main.c").write_text(
        "unsigned __int128 divide(unsigned __int128 a, unsigned __int128 b) { return a / b; }\n"
        "int main(void) { return (int)divide(100, 7); }\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    (make_dir / "Makefile").write_text(
        """ROOT := $(abspath ..)
LIBGCC := $(shell $(CC) -print-libgcc-file-name)

.PHONY: all
all: $(BUILD)/firmware.elf

$(BUILD):
\tmkdir -p $@

$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) -c $< -o $@

$(BUILD)/firmware.elf: $(BUILD)/main.o
\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $< $(LIBGCC)
"""
    )
    build = tmp_path / "build"
    receipt_path = tmp_path / "receipt.json"
    assert capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=tmp_path / "actions.jsonl",
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="native",
        image_role="application",
        build_id="d" * 64,
        port="native",
        target="native",
        variant="toolchain-library",
        source_roots={"fixture": source_root},
        cross_compile="",
        variables=[],
        targets=["all"],
    ) == 0
    receipt = json.loads(receipt_path.read_text())
    image = receipt["images"][0]
    library = next(item for item in image["link"]["operands"] if item["kind"] == "library")
    assert library["disposition"] == "contributed"
    assert library["contributing_members"]
    assert not any("libgcc" in item["path"] for item in image["link"]["unresolved"])
    assert validate_firmware_receipt(receipt)["receipt_id"] == receipt["receipt_id"]


@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("make", "gcc", "ld", "python3")),
    reason="native GNU build tools are unavailable",
)
def test_generated_source_binds_to_generator_inputs(tmp_path):
    root = tmp_path / "source"
    source_root = root / "src"
    make_dir = root / "make"
    source_root.mkdir(parents=True)
    make_dir.mkdir()
    (source_root / "main.c").write_text("extern int generated(void); int main(void) { return generated(); }\n")
    (source_root / "gen.py").write_text(
        "import sys\n"
        "data = open(sys.argv[2]).read().strip()\n"
        "open(sys.argv[4], 'w').write('int generated(void) { return %s; }\\n' % data)\n"
    )
    (source_root / "value.txt").write_text("7\n")
    (source_root / "manifest.py").write_text('module("main.py")\n')
    (source_root / "main.py").write_text("main = True\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    (make_dir / "Makefile").write_text(
        """ROOT := $(abspath ..)

.PHONY: all
all: $(BUILD)/firmware.elf

$(BUILD):
\tmkdir -p $@

$(BUILD)/generated.c: $(ROOT)/src/gen.py $(ROOT)/src/value.txt | $(BUILD)
\t$(PYTHON) $(ROOT)/src/gen.py --input $(ROOT)/src/value.txt --output-source $@

$(BUILD)/%.o: $(BUILD)/%.c
\t$(CC) -c $< -o $@

$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) -c $< -o $@

$(BUILD)/firmware.elf: $(BUILD)/main.o $(BUILD)/generated.o
\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $^
"""
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "build rules"], check=True)
    build = tmp_path / "build"
    receipt_path = tmp_path / "receipt.json"
    assert capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=tmp_path / "actions.jsonl",
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="native",
        image_role="application",
        build_id="c" * 64,
        port="native",
        target="native",
        variant="generated-source",
        source_roots={"fixture": source_root},
        cross_compile="",
        variables=[],
        targets=["all"],
    ) == 0
    image = json.loads(receipt_path.read_text())["images"][0]
    objects = {
        Path(item["object"]["path"]).name: (item["disposition"], item["source_bindings"])
        for item in image["object_accounting"]
    }
    assert objects["generated.o"][0] == "contributed"
    assert {binding["path"] for binding in objects["generated.o"][1]} == {"gen.py", "value.txt"}
    assert not any("outside declared roots" in item["reason"] for item in image["link"]["unresolved"])

    mpy_root = Path(__file__).resolve().parents[3]
    inventory = scope.collect_manifest_scope(
        mpy_root,
        [source_root / "manifest.py"],
        roots={"fixture": str(source_root)},
        path_vars={},
        owners={"fixture": "application"},
        target="native",
        variant="generated-source",
        port="native",
    )
    events = [json.loads(line) for line in (tmp_path / "actions.jsonl").read_text().splitlines()]
    compdb = tmp_path / "compile_commands.json"
    compdb.write_text(
        json.dumps(
            [
                {
                    "directory": event["cwd"],
                    "file": next(item["path"] for item in event["inputs"] if item["role"] == "source"),
                    "arguments": event["argv"],
                    "output": next(
                        item["path"] for item in event["outputs"] if Path(item["path"]).suffix == ".o"
                    ),
                }
                for event in events
                if event["kind"] == "compile"
                and all(
                    Path(item["path"]).is_relative_to(source_root)
                    for item in event["inputs"]
                    if item["role"] == "source"
                )
            ]
        )
    )
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        compdb, roots=inventory["roots"], owners={"fixture": "application"}
    )
    document, summary = sbom.generate_sbom(
        inventory, micropython_root=mpy_root, build_receipt=receipt_path
    )
    selections = {
        next(prop["value"] for prop in item["properties"] if prop["name"] == "mpy:path"): next(
            prop["value"] for prop in item["properties"] if prop["name"] == "mpy:selection"
        )
        for item in document["components"]
        if item["type"] == "file"
    }
    assert selections["gen.py"] == selections["value.txt"] == "generator-input"
    assert not any(gap["kind"] == "linked-source-not-in-selected-scope" for gap in summary["gaps"])
    assert summary["link_closure"]["status"] == "complete"
    assert summary["firmware_completeness"] == "established"


@pytest.mark.skipif(
    not all(
        shutil.which(tool)
        for tool in ("make", "gcc", "g++", "as", "ld", "ar", "objcopy", "strip", "python3")
    ),
    reason="native GNU build tools are unavailable",
)
def test_duplicate_archive_member_names_fail_closed_in_capture(tmp_path):
    root = tmp_path / "source"
    source_root = root / "src"
    make_dir = root / "make"
    (source_root / "left").mkdir(parents=True)
    (source_root / "right").mkdir()
    make_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    (source_root / "main.c").write_text("extern int value(void); int main(void) { return value(); }\n")
    (source_root / "left" / "same.c").write_text("int value(void) { return 1; }\n")
    (source_root / "right" / "same.c").write_text("int value(void) { return 2; }\n")
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    (make_dir / "Makefile").write_text(
        """ROOT := $(abspath ..)

.PHONY: all
all: $(BUILD)/firmware.elf

$(BUILD):
\tmkdir -p $@

$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) -c $< -o $@

$(BUILD)/left/same.o: $(ROOT)/src/left/same.c | $(BUILD)
\tmkdir -p $(dir $@)
\t$(CC) -c $< -o $@

$(BUILD)/right/same.o: $(ROOT)/src/right/same.c | $(BUILD)
\tmkdir -p $(dir $@)
\t$(CC) -c $< -o $@

$(BUILD)/libduplicate.a: $(BUILD)/left/same.o $(BUILD)/right/same.o
\t$(AR) q $@ $^
\t$(AR) s $@

$(BUILD)/firmware.elf: $(BUILD)/main.o $(BUILD)/libduplicate.a
\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $^
"""
    )
    build = tmp_path / "build"
    receipt_path = tmp_path / "receipt.json"
    assert capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=tmp_path / "actions.jsonl",
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="native",
        image_role="application",
        build_id="c" * 64,
        port="native",
        target="native",
        variant="duplicate-archive-member",
        source_roots={"fixture": source_root},
        cross_compile="",
        variables=[],
        targets=["all"],
    ) == 0

    receipt = json.loads(receipt_path.read_text())
    image = receipt["images"][0]
    archive = next(
        item for item in image["link"]["operands"] if item["input"]["path"].endswith(".a")
    )
    assert archive["disposition"] == "unsupported"
    assert archive["reason"] == "archive member inventory or source attribution could not be qualified"
    assert receipt["status"] == "incomplete"


@pytest.mark.skipif(
    not all(
        shutil.which(tool)
        for tool in ("make", "gcc", "g++", "as", "ld", "ar", "objcopy", "strip", "python3")
    ),
    reason="native GNU build tools are unavailable",
)
def test_failed_deployment_transform_stays_out_of_receipt(tmp_path):
    root = tmp_path / "source"
    source_root = root / "src"
    make_dir = root / "make"
    source_root.mkdir(parents=True)
    make_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    (source_root / "main.c").write_text("int main(void) { return 0; }\n")
    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    (make_dir / "Makefile").write_text(
        """ROOT := $(abspath ..)

.PHONY: all
all: $(BUILD)/firmware.bin

$(BUILD):
\tmkdir -p $@

$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) -c $< -o $@

$(BUILD)/firmware.elf: $(BUILD)/main.o
\t$(CC) -Wl,-Map=$(BUILD)/firmware.map -o $@ $^

$(BUILD)/firmware.bin: $(BUILD)/firmware.elf
\t$(OBJCOPY) --invalid-option $< $@
"""
    )
    build = tmp_path / "build"
    journal = tmp_path / "actions.jsonl"
    receipt_path = tmp_path / "receipt.json"
    result = capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=journal,
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="native",
        image_role="application",
        build_id="d" * 64,
        port="native",
        target="native",
        variant="failed-transform",
        source_roots={"fixture": source_root},
        cross_compile="",
        variables=[],
        targets=["all"],
    )

    events = [json.loads(line) for line in journal.read_text().splitlines()]
    transforms = [event for event in events if event["kind"] == "transform"]
    assert result != 0
    assert len(transforms) == 1
    assert transforms[0]["exit_code"] != 0
    assert not receipt_path.exists()


def test_in_place_transform_is_not_reported_as_input_mutation(tmp_path, monkeypatch):
    from mpy_analysis.firmware_capture import run_tool

    image = tmp_path / "firmware.elf"
    image.write_bytes(b"before")
    tool = tmp_path / "strip"
    tool.write_text("#!/bin/sh\nprintf after > \"$1\"\n")
    tool.chmod(0o755)
    journal = tmp_path / "actions.jsonl"
    monkeypatch.chdir(tmp_path)

    assert run_tool("strip", tool, [str(image)], journal=journal, image_id="x", build_id="a" * 64) == 0
    event = json.loads(journal.read_text())
    assert event["kind"] == "transform"
    assert event["changed_inputs_during_action"] == []
    assert event["outputs"][0]["sha256"] == hashlib.sha256(b"after").hexdigest()

    other = tmp_path / "source.c"
    other.write_text("x")
    tool.write_text("#!/bin/sh\nprintf y > \"$2\"\nprintf z > \"$1\"\n")
    run_tool("strip", tool, [str(image), str(other)], journal=journal, image_id="x", build_id="a" * 64)
    assert json.loads(journal.read_text().splitlines()[-1])["changed_inputs_during_action"] == [str(image)]


def _fake_compiler(tmp_path, runtime_dir, library_dir, *, sysroot=""):
    """A compiler stand-in that answers the queries the runtime identification makes."""
    path = tmp_path / "fake-cc"
    path.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        f'    -print-libgcc-file-name) echo "{runtime_dir}/libgcc.a"; exit 0;;\n'
        '    -dumpfullversion) echo 13.3.0; exit 0;;\n'
        f'    -print-sysroot) echo "{sysroot}"; exit 0;;\n'
        f'    -print-file-name=libc.a) echo "{library_dir}/libc.a"; exit 0;;\n'
        "    -print-file-name=*) echo \"${arg#-print-file-name=}\"; exit 0;;\n"
        "  esac\n"
        "done\n"
        'if grep -q "features.h"; then printf "#define __GLIBC__ 2\\n#define __GLIBC_MINOR__ 39\\n"; fi\n'
    )
    path.chmod(0o755)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_static_inputs_are_identified_through_the_captured_compiler(tmp_path):
    from mpy_analysis.firmware_capture_receipt import _implicit_input
    from mpy_analysis.toolchain_runtime import layout

    runtime_dir = tmp_path / "gcc" / "13"
    library_dir = tmp_path / "usr" / "lib" / "multiarch"
    other_dir = tmp_path / "elsewhere"
    for directory in (runtime_dir, library_dir, other_dir):
        directory.mkdir(parents=True)
    crtbegin = runtime_dir / "crtbegin.o"
    crt1 = library_dir / "crt1.o"
    unrelated = library_dir / "libunrelated.a"
    stray = other_dir / "stray.o"
    for file in (crtbegin, crt1, unrelated, stray):
        file.write_bytes(file.name.encode())
    compiler = _fake_compiler(tmp_path, runtime_dir, library_dir)
    layout.cache_clear()
    allocated = {str(item.resolve()) for item in (crtbegin, crt1, stray)}
    archive_members = {str(unrelated.resolve()): {"member.o"}}

    def classify(file):
        return _implicit_input(
            str(file.resolve()), allocated=allocated, archive_members=archive_members, compiler=compiler, flag_sets=[()]
        )

    entry, failure = classify(crtbegin)
    assert failure == "" and entry["toolchain"]["role"] == "cc" and "c_library" not in entry
    entry, failure = classify(crt1)
    assert failure == ""
    assert entry["c_library"]["name"] == "glibc" and entry["c_library"]["version"] == "2.39"
    entry, failure = classify(unrelated)
    assert entry is None and "not one of the C library's files" in failure
    entry, failure = classify(stray)
    assert entry is None and "neither compiler runtime" in failure

    layout.cache_clear()
    entry, failure = _implicit_input(
        str(crtbegin.resolve()), allocated=allocated, archive_members={}, compiler=None, flag_sets=[()]
    )
    assert entry is None and "no single captured compiler" in failure


def test_c_library_outside_the_configured_sysroot_is_not_identified(tmp_path):
    from mpy_analysis.firmware_capture_receipt import _implicit_input
    from mpy_analysis.toolchain_runtime import layout

    runtime_dir = tmp_path / "gcc"
    library_dir = tmp_path / "usr" / "lib"
    runtime_dir.mkdir()
    library_dir.mkdir(parents=True)
    crt1 = library_dir / "crt1.o"
    crt1.write_bytes(b"crt")
    compiler = _fake_compiler(tmp_path, runtime_dir, library_dir, sysroot=tmp_path / "other-sysroot")
    layout.cache_clear()
    entry, failure = _implicit_input(
        str(crt1.resolve()), allocated={str(crt1.resolve())}, archive_members={}, compiler=compiler, flag_sets=[()]
    )
    assert entry is None and "outside the compiler's configured sysroot" in failure


def test_shared_library_is_recorded_without_package_identity(tmp_path):
    from mpy_analysis.firmware_capture_receipt import _implicit_input

    library = tmp_path / "libfixture.so.1"
    library.write_bytes(b"\x7fELF" + b"\0" * 12)
    entry, failure = _implicit_input(
        str(library.resolve()), allocated=set(), archive_members={}, compiler=None, flag_sets=[()]
    )
    assert failure == "" and entry["disposition"] == "dynamic"
    assert not {"c_library", "toolchain", "linkage"} & set(entry)


@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("make", "gcc", "ld", "ar")),
    reason="native GNU build tools are unavailable",
)
def test_dynamic_link_records_needed_sonames_and_static_runtime_components(tmp_path):
    from mpy_analysis.toolchain_runtime import layout

    found = layout(shutil.which("gcc"), ())
    if not found["c_library"] or found["c_library"]["name"] != "glibc":
        pytest.skip("host C library is not glibc")
    root = tmp_path / "source"
    source_root = root / "src"
    make_dir = root / "make"
    source_root.mkdir(parents=True)
    make_dir.mkdir()
    (source_root / "manifest.py").write_text('module("main.py")\n')
    (source_root / "main.py").write_text("main = True\n")
    (source_root / "main.c").write_text("#include <stdio.h>\nint main(void) { return puts(\"x\") < 0; }\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "sbom-test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "SBOM Test"], check=True)
    (make_dir / "Makefile").write_text(
        """ROOT := $(abspath ..)

.PHONY: all
all: $(BUILD)/firmware.elf

$(BUILD):
\tmkdir -p $@

$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) -c $< -o $@

$(BUILD)/firmware.elf: $(BUILD)/main.o
\t$(CC) -Wl,-Map=$(BUILD)/firmware.map -o $@ $<
"""
    )
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    build = tmp_path / "build"
    receipt_path = tmp_path / "receipt.json"
    assert capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=tmp_path / "actions.jsonl",
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="native",
        image_role="application",
        build_id="c" * 64,
        port="native",
        target="native",
        variant="dynamic",
        source_roots={"fixture": source_root},
        cross_compile="",
        variables=[],
        targets=["all"],
    ) == 0
    receipt = json.loads(receipt_path.read_text())
    link = receipt["images"][0]["link"]
    assert receipt["status"] == "complete", link["unresolved"]
    assert "libc.so.6" in link["dynamic_needed"]
    static = [item for item in link["implicit_inputs"] if item["disposition"] == "contributed"]
    assert static and all(item["linkage"] == "static" for item in static)
    assert {item["kind"] for item in link["implicit_inputs"] if item["disposition"] == "dynamic"} == {"shared-library"}
    assert any("c_library" in item and item["c_library"]["name"] == "glibc" for item in static)
    assert any("toolchain" in item for item in static)

    mpy_root = Path(__file__).resolve().parents[3]
    inventory = scope.collect_manifest_scope(
        mpy_root,
        [source_root / "manifest.py"],
        roots={"fixture": str(source_root)},
        path_vars={},
        owners={"fixture": "application"},
        target="native",
        variant="dynamic",
        port="native",
    )
    events = [json.loads(line) for line in (tmp_path / "actions.jsonl").read_text().splitlines()]
    compdb = tmp_path / "compile_commands.json"
    compdb.write_text(
        json.dumps(
            [
                {
                    "directory": event["cwd"],
                    "file": next(item["path"] for item in event["inputs"] if item["role"] == "source"),
                    "arguments": event["argv"],
                    "output": next(item["path"] for item in event["outputs"] if item["path"].endswith(".o")),
                }
                for event in events
                if event["kind"] == "compile"
            ]
        )
    )
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        compdb, roots=inventory["roots"], owners={"fixture": "application"}
    )
    document, summary = sbom.generate_sbom(
        inventory, micropython_root=mpy_root, build_receipt=receipt_path
    )
    names = {item["name"] for item in document["components"]}
    firmware = next(item for item in document["components"] if item["type"] == "firmware")
    properties = {item["name"]: item["value"] for item in firmware["properties"]}
    assert json.loads(properties["mpy:dynamic-needed"]) == link["dynamic_needed"]
    assert "glibc" in names and "libc.so.6" not in names
    release = subprocess.run(["gcc", "-dumpfullversion"], capture_output=True, text=True, check=True).stdout.strip()
    assert any(item["type"] == "library" and item.get("version") == release for item in document["components"])
    assert not any(item.get("purl", "").startswith("pkg:deb/") for item in document["components"])
    assert summary["firmware_completeness"] == "established"


def test_runtime_directory_selected_by_compile_flags_is_found_when_the_link_has_none(tmp_path):
    from mpy_analysis.firmware_capture_receipt import _flag_sets, _runtime_identity

    default_dir = tmp_path / "gcc"
    multilib_dir = tmp_path / "gcc" / "multi"
    for directory in (default_dir, multilib_dir):
        directory.mkdir(parents=True, exist_ok=True)
    libgcc = multilib_dir / "libgcc.a"
    libgcc.write_bytes(b"archive")
    compiler_path = tmp_path / "multilib-cc"
    compiler_path.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *-print-libgcc-file-name*) case "$*" in *-mmulti*) echo "{multilib_dir}/libgcc.a";; *) echo "{default_dir}/libgcc.a";; esac;;\n'
        '  *-dumpfullversion*) echo 13.3.0;;\n'
        '  *-print-sysroot*) echo "";;\n'
        '  *-print-file-name=*) echo "libc.a";;\n'
        "esac\n"
    )
    compiler_path.chmod(0o755)
    compiler = {"path": str(compiler_path), "sha256": "0" * 64}
    actions = [
        {"kind": "compile", "argv": [str(compiler_path), "-mmulti", "-c", "a.c"], "cwd": str(tmp_path)},
        {"kind": "link", "argv": [str(compiler_path), "-nostdlib", "-o", "firmware.elf", "a.o"], "cwd": str(tmp_path)},
    ]
    assert _flag_sets(actions) == [("-mmulti",), ()]
    identity, failure = _runtime_identity(str(libgcc), compiler, _flag_sets(actions))
    assert failure == "" and identity["toolchain"]["role"] == "cc"
    identity, failure = _runtime_identity(str(libgcc), compiler, [()])
    assert identity is None
