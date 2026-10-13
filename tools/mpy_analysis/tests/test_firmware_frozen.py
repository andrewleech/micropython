"""Frozen payload accounting of a captured build: real makemanifest, mpy-cross and mpy-tool runs."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from firmware_build import (
    LINK,
    MPY_ROOT,
    PREAMBLE,
    capture,
    components,
    gap_kinds,
    generate,
    requires_gnu_build,
    spdx,
)
from mpy_analysis.firmware_frozen import find_tagged_source, frozen_strings, tagged_input_matches
from mpy_analysis.firmware_receipt import (
    FirmwareReceiptError,
    receipt_id,
    validate_firmware_receipt,
)
from mpy_analysis.sbom_spdx import validate_spdx

TOOL_FILES = (
    "tools/makemanifest.py",
    "tools/manifestfile.py",
    "tools/mpy-tool.py",
    "py/makeqstrdata.py",
    "mpy-cross/mpy_cross/__init__.py",
    "ports/unix/mpconfigport.h",
    "ports/unix/variants/standard/mpconfigvariant.h",
    "ports/unix/variants/mpconfigvariant_common.h",
    *(f"py/{name}.h" for name in (
        "bc", "cstack", "emitglue", "misc", "mpconfig", "mperrno", "mpprint", "mpstate", "mpthread", "mpz",
        "nativeglue", "nlr", "objarray", "objexcept", "obj", "objint", "objlist", "objstr", "objtuple",
        "persistentcode", "pystack", "qstr", "reader", "runtime0", "runtime", "stream",
    )),
)

MAKEFILE = """ROOT := $(abspath ..)
GEN := $(BUILD)/genhdr
CFLAGS := -I$(ROOT) -I$(ROOT)/ports/unix -I$(ROOT)/ports/unix/variants/standard -I$(BUILD) -DMPZ_DIG_SIZE=16
.PHONY: all FORCE
all: $(BUILD)/firmware.elf
FORCE:
$(GEN)/qstrdefs.preprocessed.h:
\tmkdir -p $(dir $@)
\t$(PYTHON) $(ROOT)/scripts/genfile.py $@ 'QCFG(BYTES_IN_LEN, 1)\\nQCFG(BYTES_IN_HASH, 1)\\n'
$(GEN)/qstrdefs.generated.h: $(GEN)/qstrdefs.preprocessed.h
\t$(PYTHON) $(ROOT)/py/makeqstrdata.py $< > $@
$(GEN)/root_pointers.h: $(GEN)/qstrdefs.preprocessed.h
\t$(PYTHON) $(ROOT)/scripts/genfile.py $@ ''
$(BUILD)/frozen_content.c: $(GEN)/qstrdefs.preprocessed.h FORCE
\t{freeze}
{after_freeze}
$(BUILD)/frozen_content.o: $(BUILD)/frozen_content.c $(GEN)/qstrdefs.generated.h $(GEN)/root_pointers.h
\t$(CC) $(CFLAGS) -MD -MF $(@:.o=.d) -c $< -o $@
$(BUILD)/main.o: $(ROOT)/src/main.c | $(BUILD)
\t$(CC) $(CFLAGS) -MD -MF $(@:.o=.d) -c $< -o $@
$(BUILD)/firmware.elf: $(BUILD)/main.o $(BUILD)/frozen_content.o
\t$(CC) -nostdlib -Wl,-e,main -Wl,--unresolved-symbols=ignore-all -Wl,-Map=$(BUILD)/firmware.map -o $@ $^
"""
FREEZE = (
    "$(PYTHON) $(ROOT)/tools/makemanifest.py -o $@ -v MPY_DIR=$(ROOT) -v PORT_DIR=$(ROOT)/src "
    "-b $(BUILD) $(ROOT)/manifest.py"
)
GENFILE = "import sys\nopen(sys.argv[1], 'w').write(sys.argv[2].encode().decode('unicode_escape'))\n"
APPEND = "import sys\nopen(sys.argv[1], 'ab').write(b'\\n/* appended after the generator */\\n')\n"
MANIFEST = 'module("main.py", base_path="src")\ninclude("lib/manifest.py")\n'
FILES = {
    "manifest.py": MANIFEST,
    "src/main.c": "int main(void) { return 0; }\n",
    "src/main.py": "__version__ = '0'\nVALUE = 1\n\n\ndef run():\n    return VALUE\n",
    "lib/manifest.py": 'metadata(version="1.2.3")\nmodule("util.py", opt=3)\n',
    "lib/util.py": b"def helper(x):\r\n    return x + 1\r\n",
    "scripts/genfile.py": GENFILE,
    "scripts/appendfile.py": APPEND,
}


@pytest.fixture(scope="session")
def mpy_cross(tmp_path_factory):
    build = tmp_path_factory.mktemp("mpy-cross")
    subprocess.run(
        ["make", "-C", str(MPY_ROOT / "mpy-cross"), f"BUILD={build}", "-j4"], check=True, capture_output=True
    )
    return build / "mpy-cross"


def frozen_build(tmp_path, mpy_cross, *, files=None, makefile=None, name="frozen", variables=(), freeze=FREEZE, after=""):
    contents = {rel: (MPY_ROOT / rel).read_bytes() for rel in TOOL_FILES}
    contents.update(FILES)
    contents.update(files or {})
    return capture(
        tmp_path,
        files=contents,
        makefile=makefile or MAKEFILE.format(freeze=freeze, after_freeze=after),
        roots={"fixture": "."},
        name=name,
        variables=(f"MICROPY_MPYCROSS={mpy_cross}", *variables),
    )


def accounting(built):
    return built.image["frozen_accounting"]


def elements(built):
    [payload] = accounting(built)["payloads"]
    return {item.get("target") or item["mpy"]["path"]: item for item in payload["elements"]}


def resign(receipt):
    receipt["receipt_id"] = receipt_id(receipt)
    return receipt


pytestmark = requires_gnu_build


def test_frozen_sources_are_tied_to_their_compiles_and_to_frozen_content(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    assert built.exit_code == 0
    receipt = built.receipt
    assert receipt["status"] == "complete", built.image["link"]["unresolved"]
    assert accounting(built)["status"] == "accounted"
    [payload] = accounting(built)["payloads"]
    assert payload["unresolved"] == []
    by_target = elements(built)
    assert set(by_target) == {"main.py", "util.py"}
    main, util = by_target["main.py"], by_target["util.py"]
    for item, relative in ((main, "src/main.py"), (util, "lib/util.py")):
        source = built.root / relative
        assert item["kind"] == "mpy-compiled" and item["disposition"] == "bound"
        assert item["source"] == {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        assert item["source_bindings"] == [{"root": "fixture", "path": relative}]
        mpy = Path(item["mpy"]["path"])
        assert mpy.is_relative_to(built.build / "frozen_mpy")
        assert item["mpy"]["sha256"] == hashlib.sha256(mpy.read_bytes()).hexdigest()
    actions = {action["id"]: action for action in receipt["actions"]}
    compile_main = actions[main["compile_action"]]
    assert compile_main["argv"][1:3] == ["-s", "main.py"]
    [ephemeral] = [item for item in compile_main["inputs"] if item["role"] == "ephemeral-input"]
    assert ephemeral["derived_from"]["appended"] == ""
    compile_util = actions[util["compile_action"]]
    assert "-O3" in compile_util["argv"]
    [tagged] = [item for item in compile_util["inputs"] if item["role"] == "ephemeral-input"]
    assert tagged["derived_from"] == {
        "path": str(built.root / "lib" / "util.py"),
        "sha256": util["source"]["sha256"],
        "newlines": "universal",
        "appended": "\n\n__version__ = '1.2.3'\n",
    }
    tool = actions[payload["mpy_tool"]["action"]]
    assert tool["argv"][1].endswith("mpy-tool.py") and tool["stdout"] == payload["mpy_tool"]["stdout"]
    frozen = (built.build / "frozen_content.c").read_bytes()
    assert hashlib.sha256(frozen[-tool["stdout"]["size"] :]).hexdigest() == tool["stdout"]["sha256"]
    inputs = {Path(item["path"]).relative_to(built.root).as_posix(): item for item in payload["inputs"]}
    assert {"manifest.py", "lib/manifest.py", "tools/manifestfile.py", "src/main.py", "lib/util.py"} <= set(inputs)
    assert all(item["disposition"] == "bound" for item in inputs.values())
    toolchain = {item["role"]: item for item in receipt["toolchain"]}
    assert toolchain["mpy-cross"]["executable"]["sha256"] == hashlib.sha256(mpy_cross.read_bytes()).hexdigest()

    model, summary = generate(built)
    assert summary["firmware_completeness"] == "established", summary["gaps"]
    frozen_summary = summary["link_closure"]["images"][0]["frozen_payload"]
    assert frozen_summary["status"] == "attributed" and frozen_summary["elements"] == 2
    assert frozen_summary["bound"] == 2 and frozen_summary["selected_not_in_payload"] == []
    [firmware] = components(model, **{"image-role": "application"})
    assert firmware.properties["frozen-payload"] == "attributed"
    for relative in ("src/main.py", "lib/util.py"):
        [file] = components(model, path=relative)
        assert file.properties["frozen-payload"] == "bound"
        assert file.ref in model.depends_on[firmware.ref]
    [nested] = components(model, path="lib/manifest.py")
    assert nested.ref in model.depends_on[firmware.ref]
    assert nested.properties["selection"] == "generator-input"
    validate_spdx(spdx(model))


def test_frozen_source_changed_after_the_build_invalidates_the_receipt(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    validate_firmware_receipt(built.receipt)
    (built.root / "lib" / "util.py").write_bytes(b"def helper(x):\r\n    return x + 2\r\n")
    with pytest.raises(FirmwareReceiptError, match="frozen_accounting"):
        validate_firmware_receipt(built.receipt)
    with pytest.raises(FirmwareReceiptError, match="frozen_accounting"):
        generate(built)


def test_compiled_mpy_changed_after_the_build_invalidates_the_receipt(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    mpy = built.build / "frozen_mpy" / "main.mpy"
    mpy.write_bytes(mpy.read_bytes() + b"\0")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)


def test_frozen_content_changed_after_the_build_invalidates_the_receipt(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    with (built.build / "frozen_content.c").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(FirmwareReceiptError, match="stale"):
        validate_firmware_receipt(built.receipt)


def test_a_record_that_claims_more_than_the_actions_show_is_rejected(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    receipt = built.receipt
    payload = receipt["images"][0]["frozen_accounting"]["payloads"][0]
    payload["elements"].pop()
    with pytest.raises(FirmwareReceiptError, match="does not match the captured actions"):
        validate_firmware_receipt(resign(receipt))

    receipt = built.receipt
    payload = receipt["images"][0]["frozen_accounting"]["payloads"][0]
    payload["elements"][0]["source_bindings"] = [{"root": "fixture", "path": "src/other.py"}]
    with pytest.raises(FirmwareReceiptError, match="does not match the captured actions"):
        validate_firmware_receipt(resign(receipt))


def test_complete_receipt_with_an_unbound_element_is_rejected(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    receipt = built.receipt
    frozen = receipt["images"][0]["frozen_accounting"]
    payload = frozen["payloads"][0]
    payload["elements"][0] = {"kind": "mpy-compiled", "disposition": "unbound", "reason": "forced", "mpy": payload["elements"][0]["mpy"]}
    payload["status"] = frozen["status"] = "unaccounted"
    with pytest.raises(FirmwareReceiptError, match="complete receipt contains unbound frozen payload"):
        validate_firmware_receipt(resign(receipt), verify_files=False)


def test_compiles_the_generator_ran_outside_the_wrapper_leave_elements_unbound(tmp_path, mpy_cross):
    bypass = FREEZE.replace("$(PYTHON)", f"MICROPY_MPYCROSS={mpy_cross} $(PYTHON)")
    built = frozen_build(tmp_path, mpy_cross, freeze=bypass)
    assert built.exit_code == 0 and built.receipt["status"] == "incomplete"
    [payload] = accounting(built)["payloads"]
    assert payload["status"] == "unaccounted"
    assert any("started 3 process(es) and 1 were captured" in item["reason"] for item in payload["unresolved"])
    unbound = [item for item in payload["elements"] if item["disposition"] == "unbound"]
    assert {item["reason"] for item in unbound} == {"no captured mpy-cross run wrote this .mpy"}
    assert len(unbound) == 2
    model, summary = generate(built)
    assert summary["firmware_completeness"] == "not-established"
    assert "frozen-payload-unbound" in gap_kinds(summary)
    assert summary["link_closure"]["images"][0]["frozen_payload"]["status"] == "incomplete"


def test_frozen_source_outside_the_declared_roots_is_unbound(tmp_path, mpy_cross):
    external = tmp_path / "external"
    external.mkdir()
    (external / "ext.py").write_text("EXT = 1\n")
    built = frozen_build(
        tmp_path,
        mpy_cross,
        files={"manifest.py": MANIFEST + f'module("ext.py", base_path="{external}")\n'},
    )
    assert built.exit_code == 0 and built.receipt["status"] == "incomplete"
    unbound = {
        item["source"]["path"]: item["reason"]
        for item in elements(built).values()
        if item["disposition"] == "unbound"
    }
    assert unbound == {str(external / "ext.py"): "the frozen source lies outside the declared source roots"}
    inputs = {item["path"]: item for item in accounting(built)["payloads"][0]["inputs"]}
    assert inputs[str(external / "ext.py")]["disposition"] == "unbound"
    assert elements(built)["main.py"]["disposition"] == "bound"


def test_a_rewrite_of_frozen_content_after_the_generator_breaks_the_mpy_tool_tie(tmp_path, mpy_cross):
    after = "\t$(PYTHON) $(ROOT)/scripts/appendfile.py $(BUILD)/frozen_content.c"
    built = frozen_build(tmp_path, mpy_cross, after=after)
    assert built.exit_code == 0 and built.receipt["status"] == "incomplete"
    [payload] = accounting(built)["payloads"]
    assert [item["reason"] for item in payload["unresolved"]] == [
        "frozen_content.c does not end with the captured mpy-tool output"
    ]


def test_strings_frozen_as_text_are_matched_to_the_files_the_generator_read(tmp_path, mpy_cross):
    text = b'quote " backslash \\ tab\t newline\nbyte \xff end\n'
    built = frozen_build(
        tmp_path,
        mpy_cross,
        files={"manifest.py": MANIFEST + 'freeze_as_str("strs")\n', "strs/note.py": text, "strs/two.py": b"TWO = 2\n"},
    )
    assert built.exit_code == 0 and built.receipt["status"] == "complete", built.image["link"]["unresolved"]
    strings = {item["target"]: item for item in elements(built).values() if item["kind"] == "str"}
    assert set(strings) == {"note.py", "two.py"}
    note = strings["note.py"]
    assert note["source"]["sha256"] == hashlib.sha256(text).hexdigest()
    assert note["source_bindings"] == [{"root": "fixture", "path": "strs/note.py"}]
    parsed = frozen_strings((built.build / "frozen_content.c").read_bytes())
    assert dict(parsed) == {"note.py": text, "two.py": b"TWO = 2\n"}
    model, summary = generate(built)
    assert summary["firmware_completeness"] == "established", summary["gaps"]
    (built.root / "strs" / "two.py").write_bytes(b"TWO = 3\n")
    with pytest.raises(FirmwareReceiptError, match="frozen_accounting"):
        validate_firmware_receipt(built.receipt)


def test_build_without_frozen_content_requires_nothing(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": "int main(void) { return 0; }\n"},
        makefile=PREAMBLE + LINK.format(objects="$(BUILD)/main.o"),
    )
    assert built.receipt["status"] == "complete"
    assert built.image["frozen_accounting"] == {"build_root": str(built.build), "status": "none", "payloads": []}
    _, summary = generate(built)
    assert summary["firmware_completeness"] == "established"
    assert summary["link_closure"]["images"][0]["frozen_payload"] == {"status": "none"}


def test_receipt_without_frozen_accounting_of_a_frozen_build_is_unavailable_not_complete(tmp_path, mpy_cross):
    built = frozen_build(tmp_path, mpy_cross)
    receipt = built.receipt
    del receipt["images"][0]["frozen_accounting"]
    receipt_path = tmp_path / "legacy-receipt.json"
    receipt_path.write_text(json.dumps(resign(receipt)))
    _, summary = generate(built, receipt=receipt_path)
    assert summary["firmware_completeness"] == "not-established"
    assert "frozen-accounting-unavailable" in gap_kinds(summary)


def test_tagged_source_tie_follows_the_generator_newline_and_version_handling():
    source = b"a = 1\r\nb = 2\r\n"
    tag = "\n\n__version__ = '3'\n"
    compiled = b"a = 1\nb = 2\n" + tag.encode()
    digest = hashlib.sha256(source).hexdigest()
    tie = find_tagged_source(compiled, [("/x/other.py", "0" * 64, b"zzz"), ("/x/a.py", digest, source)])
    assert tie == {"path": "/x/a.py", "sha256": digest, "newlines": "universal", "appended": tag}
    assert tagged_input_matches(source, tie, hashlib.sha256(compiled).hexdigest())
    assert not tagged_input_matches(source + b"#", tie, hashlib.sha256(compiled).hexdigest())
    assert find_tagged_source(compiled + b"extra", [("/x/a.py", digest, source)]) is None
    assert find_tagged_source(b"unrelated", [("/x/a.py", digest, source)]) is None
    unchanged = find_tagged_source(b"a = 1\n", [("/x/a.py", hashlib.sha256(b"a = 1\n").hexdigest(), b"a = 1\n")])
    assert unchanged["newlines"] == "preserved" and unchanged["appended"] == ""


def _frozen_text(size, literal):
    return (
        b'#include <stdint.h>\n#define MP_FROZEN_STR_NAMES \\\n"a.py\\0" \\\n\n'
        + b"const uint32_t mp_frozen_str_sizes[] = { %d, 0 };\n" % size
        + b'const char mp_frozen_str_content[] = {\n"' + literal + b'\\0"\n"\\0"\n};\n\n'
        + b"//\n// Content for MICROPY_MODULE_FROZEN_MPY\n//\n"
    )


def test_frozen_string_parser_decodes_the_generator_escapes_and_rejects_other_content():
    assert frozen_strings(_frozen_text(6, b'x\\n\\r\\"\\\\\\x01')) == [("a.py", b'x\n\r"\\\x01')]
    assert frozen_strings(_frozen_text(7, b'x\\n\\r\\"\\\\\\x01')) is None
    assert frozen_strings(_frozen_text(3, b"x\\q\\x01")) is None
    assert frozen_strings(_frozen_text(3, b"x\\x0")) is None
    assert frozen_strings(b"nothing") is None
