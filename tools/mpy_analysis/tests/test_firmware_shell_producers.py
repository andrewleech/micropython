"""Generated headers written by shell recipe lines that run no wrapped tool."""

import pytest
from firmware_build import (
    LINK,
    PREAMBLE,
    capture,
    gap_kinds,
    generate,
    requires_gnu_build,
)
from mpy_analysis.firmware_receipt import (
    FirmwareReceiptError,
    receipt_id,
    validate_firmware_receipt,
)

pytestmark = requires_gnu_build

MAIN = '#include "gen.h"\nint main(void) { return VALUE; }\n'


def _makefile(recipe):
    return (
        PREAMBLE
        + "CFLAGS := -I$(BUILD)\n$(BUILD)/main.o: $(BUILD)/gen.h\n$(BUILD)/gen.h: | $(BUILD)\n"
        + recipe
        + LINK.format(objects="$(BUILD)/main.o")
    )


def _generated(built):
    return built.headers()["gen.h"]


def test_header_written_by_an_echo_redirect_has_a_captured_command_only_producer(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": MAIN},
        makefile=_makefile("\techo '#define VALUE 7' > $@\n"),
    )
    assert built.exit_code == 0 and built.receipt["status"] == "complete", built.image["link"]["unresolved"]
    entry = _generated(built)
    assert entry["disposition"] == "generated"
    assert entry["source_bindings"] == [] and entry["derivation"] == "shell-command"
    [producer] = [action for action in built.receipt["actions"] if action["id"] == entry["producer"]]
    assert producer["kind"] == "generate" and producer["derivation"] == "shell-command"
    assert producer["argv"][-2] == "-c" and "#define VALUE 7" in producer["argv"][-1]
    assert producer["inputs"] == []
    assert [item["path"] for item in producer["outputs"]] == [str(built.build / "gen.h")]
    _, summary = generate(built)
    assert summary["firmware_completeness"] == "established", summary["gaps"]
    assert summary["link_closure"]["images"][0]["headers"]["generated"] == 1


def test_header_assembled_from_a_selected_file_is_bound_to_that_file(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": MAIN, "src/part.h": "#define VALUE 9\n"},
        makefile=_makefile("\tcat $(ROOT)/src/part.h > $@\n"),
    )
    entry = _generated(built)
    assert entry["disposition"] == "generated"
    assert entry["source_bindings"] == [{"root": "fixture", "path": "part.h", "via": "generator"}]
    assert "derivation" not in entry
    _, summary = generate(built)
    assert summary["firmware_completeness"] == "established", summary["gaps"]


def test_command_only_attribution_needs_a_producer_without_inputs(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": MAIN, "src/part.h": "#define VALUE 9\n"},
        makefile=_makefile("\tcat $(ROOT)/src/part.h > $@\n"),
    )
    receipt = built.receipt
    gen = next(item for item in receipt["images"][0]["header_accounting"] if item["path"].endswith("gen.h"))
    gen["source_bindings"] = []
    gen["derivation"] = "shell-command"
    receipt["receipt_id"] = receipt_id(receipt)
    with pytest.raises(FirmwareReceiptError, match="must identify at least one source boundary"):
        validate_firmware_receipt(receipt)


def test_header_written_where_the_command_does_not_name_it_stays_a_gap(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": MAIN},
        makefile=_makefile("\tcd $(BUILD) && echo '#define VALUE 7' > gen.h\n"),
    )
    assert built.exit_code == 0 and built.receipt["status"] == "incomplete"
    entry = _generated(built)
    assert entry["disposition"] == "unowned"
    assert entry["reason"] == "generated header has no captured producing action"
    _, summary = generate(built)
    assert summary["firmware_completeness"] == "not-established"
    assert "linked-header-not-in-selected-scope" in gap_kinds(summary)


def test_dependency_file_post_processing_does_not_enter_the_receipt(tmp_path):
    makefile = (
        "ROOT := $(abspath ..)\n.PHONY: all\nall: $(BUILD)/firmware.elf\n$(BUILD):\n\tmkdir -p $@\n"
        "$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)\n"
        "\t$(CC) -MD -MF $(@:.o=.d) -c $< -o $@\n"
        "\tcp $(@:.o=.d) $(@:.o=.P); rm -f $(@:.o=.d)\n"
    ) + LINK.format(objects="$(BUILD)/main.o")
    built = capture(tmp_path, files={"src/main.c": "int main(void) { return 0; }\n"}, makefile=makefile)
    assert built.exit_code == 0 and built.receipt["status"] == "complete", built.image["link"]["unresolved"]
    assert (built.build / "main.P").is_file() and not (built.build / "main.d").exists()
    assert not [action for action in built.receipt["actions"] if action.get("derivation")]
    validate_firmware_receipt(built.receipt)
