import hashlib
import json
import sys
from pathlib import Path

import pytest
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
        "import pathlib, sys\nif sys.argv[1:] == ['--version']:\n    raise SystemExit(0)\nsource = pathlib.Path(sys.argv[-1])\nsource.write_text('after')\npathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes(b'object')",
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


def _compiler(tmp_path: Path, depfile_text: str | None) -> Path:
    """A compiler that writes the object and, when given, the dependency file named by -MF."""
    body = (
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "pathlib.Path(args[args.index('-o') + 1]).write_bytes(b'object')\n"
    )
    if depfile_text is not None:
        body += f"pathlib.Path(args[args.index('-MF') + 1]).write_text({depfile_text!r})\n"
    return fake_tool(tmp_path / "cc", body)


def _compile(tmp_path: Path, tool: Path, *flags: str) -> dict:
    source = tmp_path / "input.c"
    source.write_text("int main(void) { return 0; }\n")
    journal = tmp_path / "actions.jsonl"
    assert run_tool(
        "cc", tool, ["-c", str(source), "-o", str(tmp_path / "input.o"), *flags],
        journal=journal, image_id="application", build_id="a" * 64,
    ) == 0
    [event] = read_events(journal)
    return event


def test_compile_without_a_dependency_file_request_is_reported(tmp_path):
    event = _compile(tmp_path, _compiler(tmp_path, None))
    assert event["dependency_capture"]["status"] == "not-requested"
    assert event["dependency_evidence"] == []


def test_dependency_file_that_was_not_written_is_reported(tmp_path):
    event = _compile(tmp_path, _compiler(tmp_path, None), "-MD", "-MF", str(tmp_path / "input.d"))
    assert event["dependency_capture"]["status"] == "failed"
    assert "was not written" in event["dependency_capture"]["reason"]
    assert event["dependency_evidence"] == []


@pytest.mark.parametrize(
    "text, reason",
    [
        ("", "no make rule"),
        ("input.o\n", "no make rule"),
        ("input.o:\n", "no prerequisites"),
        ("input.o: input.c missing-header.h\n", "not a readable file"),
    ],
)
def test_unusable_dependency_file_yields_no_partial_evidence(tmp_path, text, reason):
    event = _compile(tmp_path, _compiler(tmp_path, text), "-MD", "-MF", str(tmp_path / "input.d"))
    assert event["dependency_capture"]["status"] == "failed"
    assert reason in event["dependency_capture"]["reason"]
    assert event["dependency_evidence"] == []
    assert [item["role"] for item in event["inputs"]] == ["source"]


def test_dependency_file_name_defaults_to_the_output_name_with_a_d_suffix(tmp_path):
    header = tmp_path / "config.h"
    header.write_text("#define VALUE 1\n")
    tool = fake_tool(
        tmp_path / "cc",
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "out.write_bytes(b'object')\n"
        f"out.with_suffix('.d').write_text(str(out) + ': ' + {str(tmp_path / 'input.c')!r} + ' ' + {str(header)!r} + '\\n')",
    )
    event = _compile(tmp_path, tool, "-MMD")
    assert event["dependency_capture"] == {"status": "collected", "system_headers": "omitted"}
    [evidence] = event["dependency_evidence"]
    assert evidence["path"] == str((tmp_path / "input.d").resolve())
    assert str(header.resolve()) in {item["path"] for item in evidence["dependencies"]}


def test_mmd_with_isystem_directories_cannot_be_complete(tmp_path):
    tool = _compiler(tmp_path, f"input.o: {tmp_path / 'input.c'}\n")
    event = _compile(tmp_path, tool, "-MMD", "-MF", str(tmp_path / "input.d"), "-isystem", str(tmp_path))
    assert event["dependency_capture"]["status"] == "failed"
    assert "-isystem" in event["dependency_capture"]["reason"]


def test_dependency_reached_through_a_symlink_keeps_the_written_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    header = real / "config.h"
    header.write_text("#define VALUE 1\n")
    alias = tmp_path / "alias.h"
    alias.symlink_to(header)
    tool = _compiler(tmp_path, f"input.o: {tmp_path / 'input.c'} {alias}\n")
    event = _compile(tmp_path, tool, "-MD", "-MF", str(tmp_path / "input.d"))
    [evidence] = event["dependency_evidence"]
    entry = next(item for item in evidence["dependencies"] if item["path"] == str(header.resolve()))
    assert entry["written_path"] == str(alias)
    assert str(header.resolve()) in {item["path"] for item in event["inputs"]}


def test_generator_outputs_are_the_build_files_it_created_or_rewrote(tmp_path):
    build = tmp_path / "build"
    (build / "genhdr").mkdir(parents=True)
    untouched = build / "genhdr" / "untouched.h"
    untouched.write_text("same\n")
    rewritten = build / "genhdr" / "rewritten.h"
    rewritten.write_text("old\n")
    script = tmp_path / "gen.py"
    script.write_text("print('generator')\n")
    tool = fake_tool(
        tmp_path / "python",
        "import pathlib, sys\n"
        f"root = pathlib.Path({str(build)!r})\n"
        "(root / 'genhdr' / 'created.h').write_text('new\\n')\n"
        "(root / 'genhdr' / 'rewritten.h').write_text('rewritten with a different size\\n')\n",
    )
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "python", tool, [str(script), str(rewritten)], journal=journal, image_id="application",
        build_id="a" * 64, build_dir=build,
    ) == 0

    [event] = read_events(journal)
    assert event["kind"] == "generate"
    assert {Path(item["path"]).name for item in event["outputs"]} == {"created.h", "rewritten.h"}
    assert event["changed_inputs_during_action"] == []
    assert {Path(item["path"]).name: item["sha256"] for item in event["inputs"]}["rewritten.h"] == hashlib.sha256(
        b"old\n"
    ).hexdigest()


def test_capture_wrappers_named_by_a_generator_are_not_inputs(tmp_path, monkeypatch):
    wrappers = tmp_path / "wrappers"
    wrappers.mkdir()
    compiler_wrapper = wrappers / "cc"
    compiler_wrapper.write_text("#!/bin/sh\n")
    script = tmp_path / "gen.py"
    script.write_text("print('generator')\n")
    build = tmp_path / "build"
    build.mkdir()
    tool = fake_tool(tmp_path / "python", f"open({str(build / 'out.h')!r}, 'w').write('x')")
    monkeypatch.setenv("MPY_CAPTURE_WRAPPER_DIR", str(wrappers))
    journal = tmp_path / "actions.jsonl"

    assert run_tool(
        "python", tool, [str(script), str(compiler_wrapper)], journal=journal, image_id="application",
        build_id="a" * 64, build_dir=build,
    ) == 0

    [event] = read_events(journal)
    assert [Path(item["path"]).name for item in event["inputs"]] == ["gen.py"]
