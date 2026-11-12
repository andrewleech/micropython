import json

from mpy_analysis.fanalyzer import analysis_command, main

ROOT = "/repo"
PORT = "/repo/lib/mpy/ports/stm32"


def command(arguments, directory=PORT):
    entry = {"directory": directory, "file": arguments[-1], "arguments": ["cc", *arguments]}
    return analysis_command("gcc15", entry, "/out/0.sarif", ROOT)[1:-5]


def test_paths_are_rebased_onto_root_in_joined_and_separate_forms():
    assert command(
        [
            "-I.",
            "-I../..",
            "-Iboards/x",
            "-I",
            "../../lib/cmsis",
            "-iquote",
            "inc",
            "-isystem../../sys",
            "-idirafter",
            "late",
            "-include",
            "../../cfg.h",
            "-imacrosmacros.h",
            "-c",
            "../../py/obj.c",
        ]
    ) == [
        "-Ilib/mpy/ports/stm32",
        "-Ilib/mpy",
        "-Ilib/mpy/ports/stm32/boards/x",
        "-I",
        "lib/mpy/lib/cmsis",
        "-iquote",
        "lib/mpy/ports/stm32/inc",
        "-isystemlib/mpy/sys",
        "-idirafter",
        "lib/mpy/ports/stm32/late",
        "-include",
        "lib/mpy/cfg.h",
        "-imacroslib/mpy/ports/stm32/macros.h",
        "-c",
        "lib/mpy/py/obj.c",
    ]


def test_absolute_paths_inside_root_become_relative_and_outside_stay_absolute():
    assert command(["-I/repo/build/genhdr", "-I/usr/include/newlib", "/repo/build/pins.c"]) == [
        "-Ibuild/genhdr",
        "-I/usr/include/newlib",
        "build/pins.c",
    ]


def test_option_values_that_are_not_paths_are_left_alone():
    assert command(
        ["-D", "X=../y", "-U", "_FORTIFY_SOURCE", '-DF="a/b.h"', "-x", "c", "main.c"]
    ) == [
        "-D",
        "X=../y",
        "-U",
        "_FORTIFY_SOURCE",
        '-DF="a/b.h"',
        "-x",
        "c",
        "lib/mpy/ports/stm32/main.c",
    ]


def test_build_outputs_are_dropped():
    assert command(["-c", "-MD", "-MF", "build/a.d", "-MTx", "-o", "build/a.o", "a.c"]) == [
        "-c",
        "lib/mpy/ports/stm32/a.c",
    ]


def stub_gcc(tmp_path, body):
    """A GCC 15 stand-in: reports its version, then runs BODY for an analysis command."""
    path = tmp_path / "gcc15"
    path.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in -dumpversion) echo 15.2.1; exit 0;; --version) echo 'gcc 15'; exit 0;; esac\n"
        + body
    )
    path.chmod(0o755)
    return str(path)


def run_main(tmp_path, gcc):
    db = tmp_path / "db.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "a.c",
                    "arguments": ["cc", "-c", "a.c", "-o", "a.o"],
                }
            ]
        )
    )
    out = tmp_path / "out"
    rc = main(
        ["--db", str(db), "--gcc", gcc, "--root", str(tmp_path), "--out", str(out), "--jobs", "1"]
    )
    return rc, json.loads((out / "summary.json").read_text())


def test_unit_that_writes_no_sarif_fails_the_run(tmp_path):
    rc, summary = run_main(tmp_path, stub_gcc(tmp_path, "exit 0\n"))
    assert rc == 1
    assert summary["failures"][0]["reason"] == "no SARIF written"


def test_unit_that_exits_non_zero_fails_the_run_even_with_sarif(tmp_path):
    # The SARIF path is the last argument: -fdiagnostics-add-output=sarif:file=PATH.
    write_sarif = (
        'for a; do last="$a"; done\n'
        'echo \'{"version": "2.1.0", "runs": [{"results": []}]}\' > "${last#*file=}"\n'
    )
    rc, summary = run_main(tmp_path, stub_gcc(tmp_path, write_sarif + "exit 1\n"))
    assert rc == 1
    assert summary["failures"][0]["reason"] == "non-zero exit"
    assert summary["sarif_files"] == 1


def test_every_unit_writing_sarif_passes(tmp_path):
    write_sarif = (
        'for a; do last="$a"; done\n'
        'echo \'{"version": "2.1.0", "runs": [{"results": [{"ruleId": "-Wanalyzer-x"}]}]}\' '
        '> "${last#*file=}"\n'
    )
    rc, summary = run_main(tmp_path, stub_gcc(tmp_path, write_sarif + "exit 0\n"))
    assert rc == 0
    assert summary["results_by_rule"] == {"-Wanalyzer-x": 1}


def test_gcc_older_than_15_is_refused(tmp_path):
    path = tmp_path / "gcc12"
    path.write_text("#!/bin/sh\necho 12.2.1\n")
    path.chmod(0o755)
    assert (
        main(
            [
                "--db",
                str(tmp_path / "db.json"),
                "--gcc",
                str(path),
                "--root",
                str(tmp_path),
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
