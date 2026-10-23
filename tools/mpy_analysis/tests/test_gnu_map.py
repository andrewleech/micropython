"""Focused GNU ld map evidence parser tests."""

import pytest
from mpy_analysis.gnu_map import parse_gnu_map

MAP = """GNU ld (GNU Binutils) 2.40
LOAD /work/build/main.o
LOAD "/work/vendor libs/libselected.a"
LOAD /work/vendor/libnot-extracted.a

Archive member included to satisfy reference by file (symbol)
/work/vendor libs/libselected.a(selected.o)  /work/build/main.o (selected_api)

Discarded input sections
 .text.unused 0x00000000 0x00000008 /work/build/unused.o
 .rodata.dead 0x00000000 0x00000004 /work/vendor libs/libselected.a(selected.o)

Linker script and memory map
.text 0x08000000 0x00000020
 .text.main 0x08000000 0x00000010 /work/build/main.o
 .text.selected 0x08000010 0x00000010 /work/vendor libs/libselected.a(selected.o)
"""


def test_returns_raw_and_resolved_load_operands_and_distinguishes_evidence(tmp_path):
    result = parse_gnu_map(MAP, base_dir=tmp_path)

    assert [load["raw"] for load in result["loads"]] == [
        "/work/build/main.o", "/work/vendor libs/libselected.a", "/work/vendor/libnot-extracted.a"
    ]
    assert result["loads"][2]["raw"] == "/work/vendor/libnot-extracted.a"
    assert all(item["archive_path"] != "/work/vendor/libnot-extracted.a" for item in result["extracted_archive_members"])
    assert all(load["resolved"].startswith("/") for load in result["loads"])
    selected = result["extracted_archive_members"]
    assert [(item["archive_path"], item["member"]) for item in selected] == [
        ("/work/vendor libs/libselected.a", "selected.o")
    ]
    assert selected[0]["references"] == [{"file": "/work/build/main.o", "symbol": "selected_api"}]
    assert [(row["identity"]["path"], row["section"]) for row in result["discarded_sections"]] == [
        ("/work/build/unused.o", ".text.unused"),
        ("/work/vendor libs/libselected.a", ".rodata.dead"),
    ]
    allocated = {group["identity"]["path"]: group["sections"] for group in result["allocated_sections"]}
    assert allocated["/work/build/main.o"][0]["section"] == ".text.main"
    assert allocated["/work/vendor libs/libselected.a"][0]["identity"]["member"] == "selected.o"
    assert not any(item["member"] == "not-extracted.o" for item in selected)

def test_actual_gnu_map_wrapping_and_archive_reference_layout():
    text = """GNU ld (GNU Binutils) 2.43
LOAD /opt/toolchain/lib/libgcc.a
LOAD linker stubs
Archive member included to satisfy reference by file (symbol)

/opt/toolchain/lib/libgcc.a(_arm_addsubdf3.o)
                              /tmp/build/py/nativeglue.o (__aeabi_f2d)

Discarded input sections

 .text.Reset_Handler
                0x00000000       0x50 /tmp/build/startup.o

Linker script and memory map
 .text.entry 0x08000000 0x00000010 /tmp/build/main.o
"""
    result = parse_gnu_map(text)

    assert result["loads"][1] == {"raw": "linker stubs", "kind": "linker-stubs"}
    assert result["extracted_archive_members"][0]["references"] == [
        {"file": "/tmp/build/py/nativeglue.o", "symbol": "__aeabi_f2d"}
    ]
    assert result["discarded_sections"][0]["section"] == ".text.Reset_Handler"
    assert result["allocated_sections"][0]["sections"][0]["section"] == ".text.entry"


def test_gnu_map_separate_properties_and_ignored_as_needed_sections():
    text = """Archive member included to satisfy reference by file (symbol)
/usr/lib/libgcc.a(member.o)
                              /tmp/build/main.o (__aeabi_f2d)

Merging program properties

As-needed library included to satisfy reference by file (symbol)
libc.so.6                     /tmp/build/main.o (exit)

Discarded input sections
 .text.dead 0x00000000 0x00000004 /tmp/build/dead.o

Linker script and memory map
 .text.main 0x00000000 0x00000010 /tmp/build/main.o
"""
    result = parse_gnu_map(text)
    assert result["extracted_archive_members"][0]["member"] == "member.o"
    assert "as_needed_libraries" not in result
    assert result["discarded_sections"][0]["identity"]["path"] == "/tmp/build/dead.o"
    assert result["allocated_sections"][0]["identity"]["path"] == "/tmp/build/main.o"


def test_duplicate_basenames_remain_distinct_archive_identities():
    text = """Archive member included to satisfy reference by file (symbol)
/a/libparts.a(common.o)  app.o (first)
/b/libparts.a(common.o)  app.o (second)
"""
    selected = parse_gnu_map(text)["extracted_archive_members"]
    assert [(item["archive_path"], item["member"]) for item in selected] == [
        ("/a/libparts.a", "common.o"), ("/b/libparts.a", "common.o")
    ]


def test_duplicate_exact_extraction_identity_is_reported_as_ambiguous():
    text = """Archive member included to satisfy reference by file (symbol)
/a/lib.a(x.o)  app.o (first)
/a/lib.a(x.o)  other.o (second)
"""
    result = parse_gnu_map(text)
    assert len(result["extracted_archive_members"]) == 1
    assert result["ambiguities"] == [{
        "kind": "duplicate_extraction", "archive_path": "/a/lib.a", "member": "x.o", "line": 3
    }]


@pytest.mark.parametrize("text", [
    "not a linker map\n",
    "LLD 18.0\nLOAD app.o\n",
    "Archive member included to satisfy reference by file (symbol)\nmalformed record\n",
    "Discarded input sections\n broken-section-row\n",
])
def test_unknown_and_malformed_formats_fail_closed(text):
    with pytest.raises(ValueError):
        parse_gnu_map(text)


def test_ambiguous_quoted_load_operand_fails_closed():
    with pytest.raises(ValueError, match="ambiguous GNU ld map LOAD operand"):
        parse_gnu_map('LOAD "/tmp/file with spaces.o" trailing\n')


def test_section_name_without_a_leading_dot_wraps_like_any_other():
    text = (
        "Discarded input sections\n\n"
        " custom_section\n"
        "                0x0000000000000000       0x10 lib.a(x.o)\n"
    )
    [record] = parse_gnu_map(text)["discarded_sections"]
    assert (record["section"], record["size"]) == ("custom_section", 0x10)


def test_common_symbol_listing_and_cross_reference_table_are_not_section_records():
    text = (
        "Archive member included to satisfy reference by file (symbol)\n"
        "/w/libv.a(v.o)  /w/main.o (api)\n\n"
        "Allocating common symbols\nCommon symbol       size              file\n\n"
        "shared_buf          0x14              /w/libv.a(v.o)\n\n"
        "Linker script and memory map\n.text 0x08000000 0x10\n"
        " .text.api 0x08000000 0x10 /w/libv.a(v.o)\n"
        "OUTPUT(app elf32-littlearm)\n\nCross Reference Table\n\n"
        "Symbol                                            File\n"
        "api                                               /w/libv.a(v.o)\n"
        "                                                  /w/main.o\n"
    )
    result = parse_gnu_map(text, base_dir="/")
    assert [group["identity"]["member"] for group in result["allocated_sections"]] == ["v.o"]
    assert result["discarded_sections"] == []
