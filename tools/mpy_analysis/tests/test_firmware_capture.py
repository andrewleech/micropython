import hashlib
import json
import sys
from pathlib import Path

from mpy_analysis.firmware_capture import run_tool


def fake_tool(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o755)
    return path


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_compiler_wrapper_records_actual_inputs_and_successful_output(tmp_path):
    source = tmp_path / "input.c"
    source.write_text("int main(void) { return 0; }\n")
    output = tmp_path / "input.o"
    tool = fake_tool(
        tmp_path / "cc",
        "import pathlib, sys\nargs = sys.argv[1:]\npathlib.Path(args[args.index('-o') + 1]).write_bytes(b'object')",
    )
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "cc", tool, ["-c", str(source), "-o", str(output)], journal=journal,
        image_id="application", build_id="a" * 64
    ) == 0

    [event] = read_events(journal)
    assert event["kind"] == "compile"
    assert event["exit_code"] == 0
    assert event["inputs"] == [
        {
            "path": str(source.resolve()),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "role": "source",
        }
    ]
    assert event["outputs"][0]["path"] == str(output.resolve())
    assert event["outputs"][0]["sha256"] == hashlib.sha256(b"object").hexdigest()

def test_compiler_wrapper_hashes_emitted_depfile_and_dependency_inputs(tmp_path):
    source = tmp_path / "input.c"
    source.write_text('#include "config.h"\nint main(void) { return 0; }\n')
    header = tmp_path / "config.h"
    header.write_text("#define VALUE 1\n")
    output = tmp_path / "input.o"
    depfile = tmp_path / "input.d"
    tool = fake_tool(
        tmp_path / "cc",
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "pathlib.Path(args[args.index('-o') + 1]).write_bytes(b'object')\n"
        "pathlib.Path(args[args.index('-MF') + 1]).write_text("
        "str(pathlib.Path(args[args.index('-o') + 1])) + ': ' + "
        "str(pathlib.Path(args[1])) + ' ' + str(pathlib.Path(args[1]).with_name('config.h')) + '\\n')",
    )
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "cc",
        tool,
        ["-c", str(source), "-o", str(output), "-MF", str(depfile)],
        journal=journal,
        image_id="application",
        build_id="c" * 64,
    ) == 0

    [event] = read_events(journal)
    [evidence] = event["dependency_evidence"]
    assert evidence["path"] == str(depfile.resolve())
    assert evidence["sha256"] == hashlib.sha256(depfile.read_bytes()).hexdigest()
    assert {item["path"] for item in evidence["dependencies"]} == {
        str(source.resolve()),
        str(header.resolve()),
    }
    assert {
        item["path"]: item["sha256"]
        for item in evidence["dependencies"]
    } == {
        item["path"]: item["sha256"]
        for item in event["inputs"]
        if item["path"] in {str(source.resolve()), str(header.resolve())}
    }


def test_failed_tool_is_recorded_without_success_outputs(tmp_path):
    source = tmp_path / "input.c"
    source.write_text("broken source\n")
    tool = fake_tool(tmp_path / "cc", "raise SystemExit(23)")
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "cc", tool, ["-c", str(source), "-o", str(tmp_path / "input.o")],
        journal=journal, image_id="application", build_id="b" * 64
    ) == 23

    [event] = read_events(journal)
    assert event["exit_code"] == 23
    assert event["outputs"] == []


def test_compiler_probe_is_not_recorded_as_a_build_action(tmp_path):
    tool = fake_tool(tmp_path / "cc", "print('probe')")
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "cc", tool, ["-dumpmachine"], journal=journal, image_id="application", build_id="c" * 64
    ) == 0
    assert not journal.exists()


def test_input_mutated_during_action_is_explicit(tmp_path):
    source = tmp_path / "input.c"
    source.write_text("before")
    tool = fake_tool(
        tmp_path / "cc",
        "import pathlib, sys\nsource = pathlib.Path(sys.argv[-1])\nsource.write_text('after')\npathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'object')",
    )
    journal = tmp_path / "actions.jsonl"

    run_tool(
        "cc", tool, ["-c", "-o", str(tmp_path / "input.o"), str(source)],
        journal=journal, image_id="application", build_id="d" * 64
    )

    [event] = read_events(journal)
    assert event["changed_inputs_during_action"] == [str(source.resolve())]


def test_ld_wrapper_records_direct_map_output_and_link_operands(tmp_path):
    obj = tmp_path / "input.o"
    obj.write_bytes(b"object")
    elf = tmp_path / "firmware.elf"
    map_file = tmp_path / "firmware.map"
    tool = fake_tool(
        tmp_path / "ld",
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "pathlib.Path(args[args.index('-o') + 1]).write_bytes(b'ELF')\n"
        "pathlib.Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('-Map='))).write_text('GNU ld map')",
    )
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "ld",
        tool,
        ["-o", str(elf), f"-Map={map_file}", str(obj)],
        journal=journal,
        image_id="bootloader",
        build_id="e" * 64,
    ) == 0

    [event] = read_events(journal)
    assert {item["path"] for item in event["inputs"]} == {str(obj.resolve())}
    assert {item["path"] for item in event["outputs"]} == {
        str(elf.resolve()),
        str(map_file.resolve()),
    }
