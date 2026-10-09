import json
import shutil

import pytest

from conftest import write, write_db
from mpy_analysis import cppcheck_inputs

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="needs a host gcc")


def unit(tmp_path, name, *flags):
    source = write(tmp_path, f"src/{name}.c", "#if __has_attribute(packed)\n#endif\n")
    return {
        "directory": str(tmp_path),
        "file": str(source),
        "arguments": ["gcc", *flags, "-Iinc", "-DUNUSED_FOR_GROUPING", "-c", str(source)],
    }


def test_units_are_grouped_by_the_flags_that_change_the_derived_inputs(tmp_path):
    db = write_db(
        tmp_path / "db.json",
        [
            unit(tmp_path, "a", "-std=c99"),
            unit(tmp_path, "b", "-std=c11"),
            unit(tmp_path, "c", "-std=c99"),
        ],
    )
    out = tmp_path / "inputs"
    assert cppcheck_inputs.main([str(db), str(out)]) == 0
    groups = json.loads((out / "groups.json").read_text())
    assert [(g["flags"], g["entries"]) for g in groups] == [(["-std=c99"], 2), (["-std=c11"], 1)]
    assert [
        len(json.loads((out / f"compile_commands-{g['index']}.json").read_text())) for g in groups
    ] == [2, 1]
    predefines = (out / "predefines-1.h").read_text()
    assert "#define __STDC_VERSION__ 201112L" in predefines
    assert "#define CPPCHECK_HAS_ATTRIBUTE_packed 1" in predefines
    assert "<char_bit>8</char_bit>" in (out / "platform-0.xml").read_text()


def test_group_files_from_an_earlier_larger_grouping_are_removed(tmp_path):
    out = tmp_path / "inputs"
    for stale in ("compile_commands-5.json", "predefines-5.h", "platform-5.xml"):
        write(out, stale, "{}")
    db = write_db(tmp_path / "db.json", [unit(tmp_path, "a", "-std=c99")])
    assert cppcheck_inputs.main([str(db), str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "compile_commands-0.json",
        "groups.json",
        "platform-0.xml",
        "predefines-0.h",
    ]


def test_compiler_that_rejects_the_flags_fails(tmp_path):
    db = write_db(tmp_path / "db.json", [unit(tmp_path, "a", "-std=not-a-standard")])
    assert cppcheck_inputs.main([str(db), str(tmp_path / "inputs")]) == 2


def test_shared_output_preserves_consumer_database_and_header(tmp_path):
    db = write_db(tmp_path / "compile_commands-unix.json", [unit(tmp_path, "a")])
    header = write(tmp_path, "predefines-consumer.h", "#define CONSUMER 1\n")
    original = db.read_text()
    assert cppcheck_inputs.main([str(db), str(tmp_path)]) == 0
    assert db.read_text() == original
    assert header.read_text() == "#define CONSUMER 1\n"


def test_database_cannot_be_an_owned_output(tmp_path):
    db = write_db(tmp_path / "compile_commands-0.json", [unit(tmp_path, "a")])
    original = db.read_text()
    assert cppcheck_inputs.main([str(db), str(tmp_path)]) == 2
    assert db.read_text() == original
