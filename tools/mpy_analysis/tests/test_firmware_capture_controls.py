"""Negative controls and linker option forms exercised through the captured Make adapter."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import fake_elf
from mpy_analysis.firmware_capture import capture_make
from mpy_analysis.firmware_capture_receipt import merge_receipts, receipt_from_journal, receipt_id
from mpy_analysis.firmware_receipt import FirmwareReceiptError, validate_firmware_receipt

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("make", "gcc", "ld", "ar", "objcopy", "strip", "python3")),
    reason="native GNU build tools are unavailable",
)

PREAMBLE = """ROOT := $(abspath ..)
.PHONY: all
all: $(BUILD)/firmware.elf
$(BUILD):
\tmkdir -p $@
$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) $(CFLAGS) -c $< -o $@
"""
MAIN_C = "int main(void) { return 0; }\n"


def _git(root, *arguments):
    subprocess.run(["git", "-C", str(root), *arguments], check=True)


def _capture(tmp_path, link_rule, *, main_c=MAIN_C, variables=(), build_id="a" * 64, name="fixture", extra=None):
    """Run capture_make over a committed fixture repository; return (exit code, paths)."""
    root = tmp_path / name
    src = root / "src"
    make_dir = root / "make"
    src.mkdir(parents=True)
    make_dir.mkdir()
    (src / "main.c").write_text(main_c)
    for filename, content in (extra or {}).items():
        (src / filename).write_text(content)
    (make_dir / "Makefile").write_text(PREAMBLE + link_rule)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "sbom-test@example.invalid")
    _git(root, "config", "user.name", "SBOM Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    build = tmp_path / f"{name}-build"
    paths = {
        "root": root,
        "src": src,
        "build": build,
        "receipt": tmp_path / f"{name}-receipt.json",
        "journal": tmp_path / f"{name}-actions.jsonl",
    }
    code = capture_make(
        micropython_root=root,
        make_dir=make_dir,
        build_dir=build,
        journal=paths["journal"],
        receipt_path=paths["receipt"],
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id=name,
        image_role="application",
        build_id=build_id,
        port="native",
        target="native",
        variant="controls",
        source_roots={"fixture": src},
        cross_compile="",
        variables=list(variables),
        targets=["all"],
    )
    return code, paths


def _receipt(paths):
    return json.loads(paths["receipt"].read_text())


LINK = "$(BUILD)/firmware.elf: $(BUILD)/main.o\n\t$(CC) -nostdlib -Wl,-e,main {map} -o $@ $<\n"


def test_failed_compile_leaves_no_receipt(tmp_path):
    code, paths = _capture(tmp_path, LINK.format(map="-Wl,-Map=$(BUILD)/firmware.map"), main_c="int main(void) {\n")
    assert code != 0
    assert not paths["receipt"].exists()


def test_failed_link_leaves_no_receipt(tmp_path):
    code, paths = _capture(
        tmp_path,
        LINK.format(map="-Wl,-Map=$(BUILD)/firmware.map"),
        main_c="extern int missing(void); int main(void) { return missing(); }\n",
    )
    assert code != 0
    assert not paths["receipt"].exists()


def test_build_without_a_captured_link_action_is_rejected(tmp_path):
    rule = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
        "\tcp $< $@\n\ttouch $(BUILD)/firmware.map\n"
    )
    with pytest.raises(ValueError, match="exactly one link"):
        _capture(tmp_path, rule)


def test_unknown_prebuilt_archive_keeps_the_receipt_incomplete(tmp_path):
    prebuilt = tmp_path / "vendor"
    prebuilt.mkdir()
    (prebuilt / "vendor.c").write_text("int vendor(void) { return 1; }\n")
    subprocess.run(["gcc", "-c", str(prebuilt / "vendor.c"), "-o", str(prebuilt / "vendor.o")], check=True)
    subprocess.run(["ar", "rcs", str(prebuilt / "libvendor.a"), str(prebuilt / "vendor.o")], check=True)
    rule = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
        f"\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $< {prebuilt}/libvendor.a\n"
    )
    code, paths = _capture(
        tmp_path, rule, main_c="extern int vendor(void); int main(void) { return vendor(); }\n"
    )
    assert code == 0
    receipt = _receipt(paths)
    link = receipt["images"][0]["link"]
    assert receipt["status"] == "incomplete" and link["status"] == "incomplete"
    assert any("libvendor.a" in item["path"] for item in link["unresolved"])
    assert any(operand["disposition"] == "unsupported" for operand in link["operands"])


@pytest.mark.parametrize("through_response_file", [False, True])
def test_lto_is_never_claimed_complete(tmp_path, through_response_file):
    if through_response_file:
        rule = (
            "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
            "\techo '-flto' > $(BUILD)/link.rsp\n"
            "\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $< @$(BUILD)/link.rsp\n"
        )
        cflags = ""
    else:
        rule = LINK.format(map="-flto -Wl,-Map=$(BUILD)/firmware.map")
        cflags = "-flto"
    code, paths = _capture(tmp_path, rule, variables=[f"CFLAGS={cflags}"] if cflags else [])
    assert code == 0
    receipt = _receipt(paths)
    assert receipt["status"] == "incomplete"
    assert receipt["images"][0]["link"]["lto"] == "unsupported"
    receipt["status"] = "complete"
    receipt["images"][0]["link"]["status"] = "complete"
    receipt["receipt_id"] = receipt_id(receipt)
    with pytest.raises(FirmwareReceiptError, match="unresolved|LTO|unsupported"):
        validate_firmware_receipt(receipt)


MAP_FORMS = {
    "wl-equals": "-Wl,-Map=$(BUILD)/firmware.map",
    "wl-comma": "-Wl,-Map,$(BUILD)/firmware.map",
    "xlinker-equals": "-Xlinker -Map=$(BUILD)/firmware.map",
    "xlinker-pairs": "-Xlinker -Map -Xlinker $(BUILD)/firmware.map",
    "double-dash": "-Wl,--Map=$(BUILD)/firmware.map",
}


@pytest.mark.parametrize("form", sorted(MAP_FORMS))
def test_map_option_forms_bind_the_map_to_the_link(tmp_path, form):
    code, paths = _capture(tmp_path, LINK.format(map=MAP_FORMS[form]))
    assert code == 0
    receipt = _receipt(paths)
    link = receipt["images"][0]["link"]
    assert link["linker_format"] == "gnu-ld-map"
    assert link["map"]["path"] == str((paths["build"] / "firmware.map").resolve())
    assert receipt["status"] == "complete"


def test_map_option_in_a_response_file_binds_the_map_to_the_link(tmp_path):
    rule = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
        "\techo '-Wl,-Map=$(BUILD)/firmware.map' > $(BUILD)/link.rsp\n"
        "\t$(CC) -nostdlib -Wl,-e,main -o $@ $< @$(BUILD)/link.rsp\n"
    )
    code, paths = _capture(tmp_path, rule)
    assert code == 0
    receipt = _receipt(paths)
    assert receipt["images"][0]["link"]["linker_format"] == "gnu-ld-map"
    roles = {
        item["role"]
        for action in receipt["actions"]
        if action["kind"] == "link"
        for item in action["inputs"]
    }
    assert "response-file" in roles
    assert receipt["status"] == "complete"


def test_direct_ld_map_option_binds_the_map_to_the_link(tmp_path):
    rule = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
        "\t$(LD) -e main -Map=$(BUILD)/firmware.map -o $@ $<\n"
    )
    code, paths = _capture(tmp_path, rule)
    assert code == 0
    receipt = _receipt(paths)
    assert receipt["images"][0]["link"]["linker_format"] == "gnu-ld-map"
    assert receipt["status"] == "complete"


def test_linker_script_passed_through_wl_is_a_bound_operand(tmp_path):
    script = (
        "ENTRY(main)\nSECTIONS { . = 0x1000; .text : { *(.text*) }"
        " /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) } }\n"
    )
    rule = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o $(ROOT)/src/link.ld\n"
        "\t$(CC) -nostdlib -static -Wl,-T,$(ROOT)/src/link.ld -Wl,-Map=$(BUILD)/firmware.map -o $@ $(BUILD)/main.o\n"
    )
    code, paths = _capture(tmp_path, rule, extra={"link.ld": script})
    assert code == 0
    receipt = _receipt(paths)
    scripts = [
        operand for operand in receipt["images"][0]["link"]["operands"] if operand["kind"] == "script"
    ]
    assert [Path(item["input"]["path"]).name for item in scripts] == ["link.ld"]
    assert scripts[0]["disposition"] == "contributed"
    assert receipt["status"] == "complete"


def _synthetic_receipt(
    tmp_path, map_text, *, name="synthetic", build_id="a" * 64, variant="controls", repository="repository"
):
    root = tmp_path / repository
    source = root / "main.c"
    if not root.exists():
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        _git(root, "config", "user.email", "sbom-test@example.invalid")
        _git(root, "config", "user.name", "SBOM Test")
        source.write_text(MAIN_C)
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "source")
    build = tmp_path / f"{name}-build"
    build.mkdir()
    tool = tmp_path / f"{name}-cc"
    tool.write_text(f"#!{sys.executable}\nprint('test cc 1')\n")
    tool.chmod(0o755)

    def record(path, content, role):
        path.write_bytes(content)
        return {"path": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest(), "role": role}

    compiler = record(tool, tool.read_bytes(), "tool")
    source_record = {**record(source, source.read_bytes(), "source")}
    obj = record(build / "main.o", b"object", "object")
    elf = record(build / "firmware.elf", fake_elf(sections=[(".text", 0x1000, 0x20)]), "elf")
    map_file = record(build / "firmware.map", map_text.format(obj=obj["path"]).encode(), "map")
    base = {
        "schema": "mpy-firmware-action-event/v1",
        "build_id": build_id,
        "image_id": name,
        "role": "cc",
        "cwd": str(root),
        "exit_code": 0,
        "executable": compiler,
        "changed_inputs_during_action": [],
        "dependencies_discovered_after_action": [],
    }
    events = [
        {**base, "kind": "compile", "argv": [compiler["path"], "-c", source["path"] if (source := source_record) else "", "-o", obj["path"]], "started_ns": 1, "finished_ns": 2, "inputs": [source_record], "outputs": [obj]},
        {
            **base,
            "kind": "link",
            "argv": [compiler["path"], "-o", elf["path"], f"-Wl,-Map={map_file['path']}", obj["path"]],
            "started_ns": 3,
            "finished_ns": 4,
            "inputs": [{**obj, "role": "link-operand"}],
            "outputs": [elf, map_file],
        },
    ]
    journal = tmp_path / f"{name}.jsonl"
    journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    return receipt_from_journal(
        journal,
        build_id=build_id,
        image_id=name,
        image_role="application",
        port="native",
        target="native",
        variant=variant,
        variables={},
        source_roots={"fixture": root},
        build_dir=build,
        elf=Path(elf["path"]),
        map_file=Path(map_file["path"]),
    )


GOOD_MAP = (
    "GNU ld (GNU Binutils) 2.40\nLOAD {obj}\n\nDiscarded input sections\n\n"
    "Linker script and memory map\n.text 0x00001000 0x20\n .text 0x00001000 0x10 {obj}\n"
)


def test_synthetic_baseline_is_complete(tmp_path):
    receipt = _synthetic_receipt(tmp_path, GOOD_MAP)
    assert receipt["status"] == "complete"


def test_map_that_does_not_describe_the_elf_is_rejected(tmp_path):
    moved = GOOD_MAP.replace(" .text 0x00001000 0x10", " .text 0x00005000 0x10")
    with pytest.raises(FirmwareReceiptError, match="does not describe the final ELF"):
        _synthetic_receipt(tmp_path, moved)
    renamed = GOOD_MAP.replace(".text 0x00001000 0x20\n", ".other 0x00001000 0x20\n")
    with pytest.raises(FirmwareReceiptError, match="does not describe the final ELF"):
        _synthetic_receipt(tmp_path, renamed, name="second", repository="second-repository")


def test_dynamic_requirements_must_match_the_final_elf(tmp_path):
    receipt = _synthetic_receipt(tmp_path, GOOD_MAP)
    assert receipt["images"][0]["link"]["dynamic_needed"] == []
    receipt["images"][0]["link"]["dynamic_needed"] = ["libmissing.so.1"]
    receipt["receipt_id"] = receipt_id(receipt)
    with pytest.raises(FirmwareReceiptError, match="dynamic_needed does not match"):
        validate_firmware_receipt(receipt)


@pytest.mark.parametrize(
    "map_text",
    [
        "this is not a linker map\n",
        "LLD map\n  Address  Size Align Out In Symbol\n",
        GOOD_MAP + " .text.part 0x00001010 0x4 /tmp/lib.a(broken.o\n",
    ],
    ids=["garbage", "lld-dialect", "malformed-identity"],
)
def test_unsupported_or_malformed_maps_cannot_be_complete(tmp_path, map_text):
    receipt = _synthetic_receipt(tmp_path, map_text)
    link = receipt["images"][0]["link"]
    assert receipt["status"] == "incomplete"
    assert link["status"] == "incomplete"
    assert any(item["reason"].startswith("unsupported map evidence") for item in link["unresolved"])


def test_linker_generated_stub_sections_are_recorded_without_blocking_closure(tmp_path):
    map_text = (
        "GNU ld (GNU Binutils) 2.40\nLOAD {obj}\nLOAD linker stubs\n\nDiscarded input sections\n\n"
        "Linker script and memory map\n.text 0x00001000 0x20\n .text 0x00001000 0x10 {obj}\n"
        " .text.stub 0x00001010 0x8 linker stubs\n"
    )
    receipt = _synthetic_receipt(tmp_path, map_text)
    link = receipt["images"][0]["link"]
    assert [item["section"] for item in link["linker_generated_sections"]] == [".text.stub"]
    assert link["linker_generated_sections"][0]["size"] == 8
    assert receipt["status"] == "complete"
    assert not link["unresolved"]


def test_composition_rejects_mismatched_build_ids_and_conflicting_configuration(tmp_path):
    first = _synthetic_receipt(tmp_path, GOOD_MAP, name="first")
    second = _synthetic_receipt(tmp_path, GOOD_MAP, name="second")
    merged = merge_receipts([first, second])
    assert [image["id"] for image in merged["images"]] == ["first", "second"]

    other_build = _synthetic_receipt(tmp_path, GOOD_MAP, name="third", build_id="b" * 64)
    with pytest.raises(ValueError, match="different build IDs"):
        merge_receipts([first, other_build])

    other_variant = _synthetic_receipt(tmp_path, GOOD_MAP, name="fourth", variant="other-variant")
    with pytest.raises(ValueError, match="different variant"):
        merge_receipts([first, other_variant])

    with pytest.raises(ValueError, match="duplicate image IDs"):
        merge_receipts([first, first])


def test_stale_transform_output_invalidates_the_receipt(tmp_path):
    rule = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
        "\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $<\n"
        "\t$(OBJCOPY) -O binary $@ $(BUILD)/firmware.bin\n"
    )
    code, paths = _capture(tmp_path, rule)
    assert code == 0
    receipt = _receipt(paths)
    binary = paths["build"] / "firmware.bin"
    deployed = [
        artifact
        for artifact in receipt["images"][0]["artifacts"]
        if artifact["path"] == str(binary.resolve())
    ]
    assert deployed and deployed[0]["sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    original = binary.read_bytes()
    binary.write_bytes(original + b"stale")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(receipt)


def test_object_from_an_uncaptured_compiler_blocks_completeness(tmp_path):
    # The later rule replaces the wrapped CC recipe, so the object has no captured compile action.
    rule = (
        "$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)\n\tgcc -c $< -o $@\n"
        "$(BUILD)/firmware.elf: $(BUILD)/main.o\n"
        "\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $<\n"
    )
    code, paths = _capture(tmp_path, rule)
    assert code == 0
    receipt = _receipt(paths)
    link = receipt["images"][0]["link"]
    assert receipt["status"] == "incomplete"
    assert any(
        item["reason"] == "contributing object source is outside declared roots"
        for item in link["unresolved"]
    )
