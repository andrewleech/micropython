"""Raw binary content embedded in an image: objcopy and linker inputs, and assembler .incbin."""

import hashlib
import json
import platform
from pathlib import Path

import pytest
from firmware_build import (
    LINK,
    PREAMBLE,
    capture,
    components,
    fake_toolchain,
    gap_kinds,
    generate,
    requires_gnu_build,
    spdx,
)
from mpy_analysis.firmware_receipt import (
    FirmwareReceiptError,
    receipt_id,
    validate_firmware_receipt,
)

pytestmark = [
    requires_gnu_build,
    pytest.mark.skipif(platform.machine() != "x86_64", reason="the objcopy binary format is x86-64 specific"),
]

BLOB = b"\x00\x01blob bytes\xff"
OBJCOPY_FORMAT = "-I binary -O elf64-x86-64 -B i386"
MAIN_USING_BLOB = "extern const char _binary_blob_bin_start[];\nint main(void) { return _binary_blob_bin_start[0]; }\n"


def _objcopy_rule(name="blob", directory="$(ROOT)/src", flags=OBJCOPY_FORMAT):
    return (
        f"$(BUILD)/{name}.o: {directory}/{name}.bin | $(BUILD)\n"
        f"\tcd {directory} && $(OBJCOPY) {flags} {name}.bin $@\n"
    )


def _makefile(*rules, objects="$(BUILD)/main.o $(BUILD)/blob.o", link=LINK):
    return PREAMBLE + "".join(rules) + link.format(objects=objects)


def _capture_with_blob_bytes(tmp_path, makefile, **options):
    """Capture with src/blob.bin holding BLOB and a main.c that uses it unless one is given."""
    files = {"src/main.c": MAIN_USING_BLOB, "src/blob.bin": BLOB, **options.pop("files", {})}
    built = capture(tmp_path, files=files, makefile=makefile, **options)
    assert built.exit_code == 0
    return built


def test_objcopy_binary_object_is_attributed_to_its_blob(tmp_path):
    built = _capture_with_blob_bytes(tmp_path, _makefile(_objcopy_rule()))
    assert built.receipt["status"] == "complete"
    [row] = built.image["embedded_inputs"]
    assert row["origin"] == "objcopy-input-binary" and row["disposition"] == "contributed"
    assert row["blob"]["sha256"] == hashlib.sha256(BLOB).hexdigest()
    assert row["source_bindings"] == [{"root": "fixture", "path": "blob.bin"}]
    operand = next(item for item in built.image["link"]["operands"] if item["kind"] == "embedded-binary")
    assert Path(operand["input"]["path"]).name == "blob.o" and operand["disposition"] == "contributed"
    action = next(item for item in built.receipt["actions"] if item["id"] == row["action"])
    assert action["kind"] == "transform"
    assert [item["role"] for item in action["inputs"] if item["path"] == row["blob"]["path"]] == ["embedded-input"]

    model, summary = generate(built)
    [resource] = components(model, role="embedded-resource", path="blob.bin")
    firmware = next(item for item in model.components.values() if item.kind == "firmware")
    assert resource.ref in model.depends_on[firmware.ref]
    assert summary["link_closure"]["images"][0]["embedded_inputs"]["status"] == "attributed"
    assert summary["firmware_completeness"] == "established"
    document = spdx(model)
    assert any(
        item["fileName"] == "blob.bin" and any(c["algorithm"] == "SHA1" for c in item["checksums"])
        for item in document["files"]
    )


def test_changed_blob_invalidates_the_receipt(tmp_path):
    built = _capture_with_blob_bytes(tmp_path, _makefile(_objcopy_rule()))
    validate_firmware_receipt(built.receipt)
    (built.roots["fixture"] / "blob.bin").write_bytes(BLOB + b"changed")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)


def test_blob_that_no_linked_object_holds_is_not_listed(tmp_path):
    spare = _objcopy_rule("spare")
    built = _capture_with_blob_bytes(
        tmp_path, _makefile(_objcopy_rule(), spare), files={"src/spare.bin": "never linked"}
    )
    assert [Path(row["blob"]["path"]).name for row in built.image["embedded_inputs"]] == ["blob.bin"]
    model, _ = generate(built)
    assert not components(model, path="spare.bin")


def test_blob_garbage_collected_by_the_linker_does_not_enter_the_sbom(tmp_path):
    link = "$(BUILD)/firmware.elf: {objects}\n\t$(CC) -nostdlib -Wl,-e,main -Wl,--gc-sections -Wl,-Map=$(BUILD)/firmware.map -o $@ $^\n"
    built = _capture_with_blob_bytes(
        tmp_path,
        _makefile(_objcopy_rule(), link=link),
        files={"src/main.c": "int main(void) { return 0; }\n"},
    )
    [row] = built.image["embedded_inputs"]
    assert row["disposition"] == "no-contribution"
    assert built.receipt["status"] == "complete"
    model, summary = generate(built)
    assert not components(model, path="blob.bin")
    assert summary["firmware_completeness"] == "established"


def test_blob_outside_every_declared_root_blocks_the_complete_claim(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "blob.bin").write_bytes(BLOB)
    built = _capture_with_blob_bytes(tmp_path, _makefile(_objcopy_rule(directory=str(vendor))))
    [row] = built.image["embedded_inputs"]
    assert row["disposition"] == "unsupported"
    assert built.receipt["status"] == "incomplete"
    assert any("embedded content" in item["reason"] for item in built.image["link"]["unresolved"])
    _, summary = generate(built)
    assert "linked-embedded-input-not-in-selected-scope" in gap_kinds(summary)
    assert summary["firmware_completeness"] == "not-established"


def test_linker_binary_input_is_attributed_to_its_blob(tmp_path):
    link = (
        "$(BUILD)/firmware.elf: $(BUILD)/main.o $(ROOT)/src/blob.bin\n"
        "\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $(BUILD)/main.o "
        "-Wl,-b,binary $(ROOT)/src/blob.bin\n"
    )
    built = _capture_with_blob_bytes(
        tmp_path, PREAMBLE + link, files={"src/main.c": "int main(void) { return 0; }\n"}
    )
    assert built.receipt["status"] == "complete"
    [row] = built.image["embedded_inputs"]
    assert row["origin"] == "ld-format-binary" and row["object"] == row["blob"]
    operand = next(item for item in built.image["link"]["operands"] if item["kind"] == "embedded-binary")
    assert operand["input"]["path"] == row["blob"]["path"] and operand["disposition"] == "contributed"
    model, summary = generate(built)
    assert components(model, role="embedded-resource", path="blob.bin")
    assert summary["firmware_completeness"] == "established"


def _incbin_main(blob):
    return (
        f'__asm__(".section .rodata\\n.global blob_start\\nblob_start:\\n.incbin \\"{blob}\\"\\n");\n'
        "extern const char blob_start[];\nint main(void) { return blob_start[0]; }\n"
    )


def test_incbin_in_compiled_source_is_found_through_the_assembler_dependency_file(tmp_path):
    blob = tmp_path / "fixture" / "src" / "blob.bin"
    built = _capture_with_blob_bytes(
        tmp_path, _makefile(objects="$(BUILD)/main.o", link=LINK), files={"src/main.c": _incbin_main(blob)}
    )
    assert built.receipt["status"] == "complete"
    [row] = built.image["embedded_inputs"]
    assert row["origin"] == "assembler-dependency" and row["disposition"] == "contributed"
    assert row["blob"]["sha256"] == hashlib.sha256(BLOB).hexdigest()
    compile_action = next(item for item in built.receipt["actions"] if item["kind"] == "compile")
    assert "-Wa,--MD" not in " ".join(compile_action["argv"])
    model, summary = generate(built)
    [resource] = components(model, role="assembler-input", path="blob.bin")
    assert resource.properties["selection"] == "assembler-input"
    assert summary["firmware_completeness"] == "established"


def test_changed_incbin_data_invalidates_the_receipt(tmp_path):
    blob = tmp_path / "fixture" / "src" / "blob.bin"
    built = _capture_with_blob_bytes(
        tmp_path, _makefile(objects="$(BUILD)/main.o", link=LINK), files={"src/main.c": _incbin_main(blob)}
    )
    (built.roots["fixture"] / "blob.bin").write_bytes(BLOB + b"changed")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)


def test_incbin_data_outside_every_declared_root_is_a_named_gap(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "blob.bin").write_bytes(BLOB)
    built = _capture_with_blob_bytes(
        tmp_path,
        _makefile(objects="$(BUILD)/main.o", link=LINK),
        files={"src/main.c": _incbin_main(vendor / "blob.bin")},
    )
    [row] = built.image["embedded_inputs"]
    assert row["disposition"] == "unsupported" and built.receipt["status"] == "incomplete"
    _, summary = generate(built)
    assert "linked-embedded-input-not-in-selected-scope" in gap_kinds(summary)


def test_compiler_that_reports_no_assembler_dependencies_cannot_be_complete(tmp_path, monkeypatch):
    prefix = fake_toolchain(
        tmp_path, monkeypatch, gcc_head='if [ "$1" = "--version" ]; then echo "clang version 99"; exit 0; fi'
    )
    built = _capture_with_blob_bytes(
        tmp_path,
        _makefile(objects="$(BUILD)/main.o", link=LINK),
        files={"src/main.c": "int main(void) { return 0; }\n"},
        cross_compile=prefix,
    )
    compile_action = next(item for item in built.receipt["actions"] if item["kind"] == "compile")
    assert compile_action["assembler_dependencies"]["status"] == "unavailable"
    assert built.receipt["status"] == "incomplete"
    assert any("assembler" in item["reason"] for item in built.image["link"]["unresolved"])


def test_embedded_rows_must_match_a_captured_action_input(tmp_path):
    built = _capture_with_blob_bytes(tmp_path, _makefile(_objcopy_rule()))
    receipt = json.loads(json.dumps(built.receipt))
    receipt["images"][0]["embedded_inputs"][0]["blob"]["sha256"] = "0" * 64
    receipt["receipt_id"] = receipt_id(receipt)
    with pytest.raises(FirmwareReceiptError):
        validate_firmware_receipt(receipt)
    receipt = json.loads(json.dumps(built.receipt))
    receipt["images"][0]["embedded_inputs"][0]["action"] = "action-99999"
    receipt["receipt_id"] = receipt_id(receipt)
    with pytest.raises(FirmwareReceiptError, match="unknown origin or action"):
        validate_firmware_receipt(receipt)
