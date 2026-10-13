"""Header ownership of a captured build: attribution, exclusion, staleness and alias handling."""

import hashlib
import json
from pathlib import Path

import pytest
from firmware_build import (
    LINK,
    PREAMBLE,
    capture,
    components,
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

pytestmark = requires_gnu_build

MAIN = '#include "config.h"\nint main(void) { return VALUE; }\n'


def _makefile(extra_rules=""):
    return PREAMBLE + extra_rules + LINK.format(objects="$(BUILD)/main.o")


def test_selected_header_is_attributed_to_its_boundary_and_unused_header_is_absent(tmp_path):
    built = capture(
        tmp_path,
        files={
            "src/main.c": MAIN,
            "src/config.h": "#define VALUE 3\n",
            "src/unused.h": "#define NEVER 1\n",
        },
        makefile=_makefile(),
    )
    assert built.exit_code == 0 and built.receipt["status"] == "complete"
    assert {name for name, item in built.headers().items() if item["disposition"] != "toolchain"} == {"config.h"}
    entry = built.headers()["config.h"]
    assert entry["disposition"] == "source"
    assert entry["source_bindings"] == [{"root": "fixture", "path": "config.h"}]

    model, summary = generate(built)
    [header] = components(model, role="header", path="config.h")
    assert not components(model, path="unused.h")
    assert header.properties["selection"] == "header" and header.properties["root"] == "fixture"
    owners = [
        item
        for item in model.components.values()
        if "boundary" in item.properties and header.ref in model.depends_on.get(item.ref, set())
    ]
    assert len(owners) == 1
    image = summary["link_closure"]["images"][0]
    assert image["headers"]["status"] == "attributed" and image["headers"]["source"] == 1
    assert summary["firmware_completeness"] == "established"

    document = spdx(model)
    [element] = [item for item in document["files"] if item["fileName"] == "config.h"]
    assert any(checksum["algorithm"] == "SHA1" for checksum in element["checksums"])


def test_changed_selected_header_invalidates_the_receipt(tmp_path):
    built = capture(
        tmp_path, files={"src/main.c": MAIN, "src/config.h": "#define VALUE 3\n"}, makefile=_makefile()
    )
    validate_firmware_receipt(built.receipt)
    (built.roots["fixture"] / "config.h").write_text("#define VALUE 4\n")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)
    with pytest.raises(FirmwareReceiptError, match="stale"):
        generate(built)


def test_headers_of_the_captured_compiler_are_toolchain_inputs_not_source_gaps(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": '#include <stdint.h>\nint main(void) { return (int32_t)0; }\n'},
        makefile=_makefile(),
    )
    entry = built.headers()["stdint.h"]
    assert entry["disposition"] == "toolchain"
    assert entry["toolchain"]["role"] == "cc"
    assert Path(entry["path"]).is_relative_to(entry["toolchain"]["include_dir"])
    assert built.receipt["status"] == "complete"

    model, summary = generate(built)
    [library] = components(model, selection="compiler-header-directories")
    assert library.version and "toolchain-compiler-sha256" in library.properties
    children = [item for item in model.components.values() if item.parent == library.ref]
    assert any(item.name == entry["path"] and item.properties["role"] == "header" for item in children)
    assert "linked-header-not-in-selected-scope" not in gap_kinds(summary)
    assert summary["link_closure"]["images"][0]["headers"]["toolchain"] >= 1
    assert summary["firmware_completeness"] == "established"
    spdx(model)


def test_header_outside_roots_build_directory_and_toolchain_is_a_named_gap(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "vendor.h").write_text("#define VALUE 5\n")
    built = capture(
        tmp_path,
        files={"src/main.c": '#include "vendor.h"\nint main(void) { return VALUE; }\n'},
        makefile=_makefile(),
        variables=(f"CFLAGS=-I{vendor}",),
    )
    entry = built.headers()["vendor.h"]
    assert entry["disposition"] == "unowned"
    assert built.receipt["status"] == "incomplete"
    assert any("vendor.h" in row["path"] for row in built.image["link"]["unresolved"])

    model, summary = generate(built)
    assert "linked-header-not-in-selected-scope" in gap_kinds(summary)
    assert summary["firmware_completeness"] == "not-established"
    assert not components(model, path="vendor.h")


GENERATED_HEADER = (
    "$(BUILD)/gen.h: $(ROOT)/src/gen.py | $(BUILD)\n"
    "\t$(PYTHON) $(ROOT)/src/gen.py > $@\n"
    "$(BUILD)/main.o: $(BUILD)/gen.h\n"
)


def test_generated_header_is_attributed_through_its_generator_input(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": '#include "gen.h"\nint main(void) { return VALUE; }\n', "src/gen.py": "print('#define VALUE 3')\n"},
        makefile=_makefile(GENERATED_HEADER),
        variables=(f"CFLAGS=-I{tmp_path / 'fixture-build'}",),
    )
    entry = built.headers()["gen.h"]
    assert entry["disposition"] == "generated"
    assert {item["path"] for item in entry["source_bindings"]} == {"gen.py"}
    producer = next(action for action in built.receipt["actions"] if action["id"] == entry["producer"])
    assert producer["kind"] == "generate"
    assert built.receipt["status"] == "complete"

    model, summary = generate(built)
    [generator] = components(model, selection="generator-input", path="gen.py")
    assert generator.properties["role"] == "generator-input"
    assert summary["firmware_completeness"] == "established"


def test_generated_header_without_a_captured_producer_is_a_gap(tmp_path):
    rules = (
        "$(BUILD)/gen.h: | $(BUILD)\n"
        "\tprintf '#define VALUE 3\\n' > $@\n"
        "$(BUILD)/main.o: $(BUILD)/gen.h\n"
    )
    built = capture(
        tmp_path,
        files={"src/main.c": '#include "gen.h"\nint main(void) { return VALUE; }\n'},
        makefile=_makefile(rules),
        variables=(f"CFLAGS=-I{tmp_path / 'fixture-build'}",),
    )
    entry = built.headers()["gen.h"]
    assert entry["disposition"] == "unowned" and "no captured producing action" in entry["reason"]
    assert built.receipt["status"] == "incomplete"
    _, summary = generate(built)
    assert summary["firmware_completeness"] == "not-established"


def test_modified_generated_header_and_generator_input_are_detected(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": '#include "gen.h"\nint main(void) { return VALUE; }\n', "src/gen.py": "print('#define VALUE 3')\n"},
        makefile=_makefile(GENERATED_HEADER),
        variables=(f"CFLAGS=-I{tmp_path / 'fixture-build'}",),
    )
    validate_firmware_receipt(built.receipt)
    generated = built.build / "gen.h"
    original = generated.read_text()
    generated.write_text(original + "/* edited */\n")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)
    generated.write_text(original)
    validate_firmware_receipt(built.receipt)
    generator = built.roots["fixture"] / "gen.py"
    generator.write_text("print('#define VALUE 4')\n")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)


LIBRARY_RULE = (
    "$(BUILD)/library.o: $(ROOT)/other/library.c | $(BUILD)\n"
    "\t$(CC) $(CFLAGS) -MD -MF $(@:.o=.d) -c $< -o $@\n"
)


def test_symlink_alias_resolves_to_the_canonical_owner_root(tmp_path):
    built = capture(
        tmp_path,
        files={
            "src/main.c": '#include "alias.h"\nint main(void) { return VALUE; }\n',
            "other/real.h": "#define VALUE 3\n",
            "other/library.c": "int library(void) { return 1; }\n",
        },
        symlinks={"src/alias.h": "../other/real.h"},
        makefile=PREAMBLE + LIBRARY_RULE + LINK.format(objects="$(BUILD)/main.o $(BUILD)/library.o"),
        roots={"fixture": "src", "other": "other"},
    )
    assert built.exit_code == 0
    entry = built.headers()["real.h"]
    assert entry["disposition"] == "source"
    assert entry["source_bindings"] == [{"root": "other", "path": "real.h"}]
    assert entry["aliases"] == [str(built.roots["fixture"] / "alias.h")]

    model, summary = generate(built)
    [header] = components(model, role="header", path="real.h")
    assert header.properties["root"] == "other"
    owner = next(
        item for item in model.components.values()
        if header.ref in model.depends_on.get(item.ref, set()) and "boundary" in item.properties
    )
    assert owner.properties["directory"] == str(built.roots["other"])
    assert summary["firmware_completeness"] == "established"


def test_symlink_alias_to_a_file_outside_every_root_is_a_named_gap(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "real.h").write_text("#define VALUE 3\n")
    built = capture(
        tmp_path,
        files={"src/main.c": '#include "escape.h"\nint main(void) { return VALUE; }\n'},
        symlinks={"src/escape.h": str(outside / "real.h")},
        makefile=_makefile(),
    )
    entry = built.headers()["real.h"]
    assert entry["disposition"] == "unowned"
    assert "escape.h" in entry["reason"]
    assert built.receipt["status"] == "incomplete"
    _, summary = generate(built)
    assert summary["firmware_completeness"] == "not-established"
    assert "linked-header-not-in-selected-scope" in gap_kinds(summary)


def _forge(receipt, change):
    forged = json.loads(json.dumps(receipt))
    change(forged["images"][0]["header_accounting"])
    forged["receipt_id"] = receipt_id(forged)
    return forged


def test_header_accounting_must_match_the_compile_dependency_evidence(tmp_path):
    built = capture(
        tmp_path, files={"src/main.c": MAIN, "src/config.h": "#define VALUE 3\n"}, makefile=_makefile()
    )
    receipt = built.receipt

    def add_unread_header(entries):
        unread = built.roots["fixture"] / "main.py"
        digest = hashlib.sha256(unread.read_bytes()).hexdigest()
        entries.append({**entries[0], "path": str(unread), "sha256": digest})

    def drop_config(entries):
        entries[:] = [item for item in entries if not item["path"].endswith("config.h")]

    def rename_consumer(entries):
        for item in entries:
            item["consumed_by"] = ["action-99999"]

    def leave_unattributed(entries):
        for item in entries:
            if item["path"].endswith("config.h"):
                item.update(disposition="unowned", reason="forged")
                del item["source_bindings"]

    for change, message in (
        (add_unread_header, "no contributing compile read"),
        (drop_config, "omits headers"),
        (rename_consumer, "disagrees with the compile dependency evidence"),
        (leave_unattributed, "unattributed headers"),
    ):
        with pytest.raises(FirmwareReceiptError, match=message):
            validate_firmware_receipt(_forge(receipt, change))


def test_receipt_without_header_accounting_stays_valid_but_does_not_establish_firmware(tmp_path):
    built = capture(
        tmp_path, files={"src/main.c": MAIN, "src/config.h": "#define VALUE 3\n"}, makefile=_makefile()
    )
    historical = _forge(built.receipt, lambda entries: None)
    del historical["images"][0]["header_accounting"]
    historical["receipt_id"] = receipt_id(historical)
    validate_firmware_receipt(historical)

    _, summary = generate(built, receipt=historical)
    assert "header-accounting-unavailable" in gap_kinds(summary)
    assert summary["link_closure"]["images"][0]["headers"] == {"status": "unavailable"}
    assert summary["firmware_completeness"] == "not-established"


def test_merged_images_keep_their_header_accounting_bound_to_their_own_actions(tmp_path):
    from mpy_analysis.firmware_capture import capture_make
    from mpy_analysis.firmware_capture_receipt import merge_receipts

    first = capture(
        tmp_path, files={"src/main.c": MAIN, "src/config.h": "#define VALUE 3\n"}, makefile=_makefile()
    )
    build = tmp_path / "second-build"
    assert capture_make(
        micropython_root=first.root,
        make_dir=first.root / "make",
        build_dir=build,
        journal=tmp_path / "second.jsonl",
        receipt_path=tmp_path / "second-receipt.json",
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id="second",
        image_role="bootloader",
        build_id="a" * 64,
        port="native",
        target="native",
        variant="controls",
        source_roots=first.roots,
        cross_compile="",
        variables=[],
        targets=["all"],
    ) == 0
    second = json.loads((tmp_path / "second-receipt.json").read_text())
    merged = merge_receipts([first.receipt, second])
    validate_firmware_receipt(merged)
    actions = {action["id"]: action for action in merged["actions"]}
    for image in merged["images"]:
        for entry in image["header_accounting"]:
            assert {actions[item]["image"] for item in entry["consumed_by"]} == {image["id"]}
