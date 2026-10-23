import json
import zipfile

from mpy_analysis import codechecker_coverage

CFG = "stm32-BOARD"
NAKED = (
    "{root}/src/m.c:12:5: error: non-ASM statement in naked function is not supported\n"
    " 12 |     MP_UNREACHABLE;\n"
    "{root}/py/mpconfig.h:7:24: note: expanded from macro 'MP_UNREACHABLE'\n"
)
GAP = {
    "id": "clangsa",
    "file": "src/m.c",
    "line": 12,
    "translation_units": ["src/m.c"],
    "configurations": [CFG],
}


def database(tmp_path, units):
    """A compilation database naming each unit, with its directory as compiledb writes it."""
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path / "src"),
                    "file": f"../{u}",
                    "arguments": ["gcc", "-c", f"../{u}"],
                }
                for u in units
            ]
        )
    )
    return str(db)


def record(tmp_path, succeeded, failed, stderr=None):
    """CodeChecker's output directory for a clangsa run: metadata.json and, for each failed unit, the
    zip it keeps, holding the build action and clang's output (NAKED unless STDERR gives one)."""
    out = tmp_path / "reports"
    (out / "failed").mkdir(parents=True)
    statistics = {
        "successful": len(succeeded),
        "failed": len(failed),
        "successful_sources": [str(tmp_path / u) for u in succeeded],
        "failed_sources": [str(tmp_path / u) for u in failed],
    }
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "version": 2,
                "tools": [
                    {
                        "name": "codechecker",
                        "analyzers": {"clangsa": {"analyzer_statistics": statistics}},
                    }
                ],
            }
        )
    )
    for index, unit in enumerate(failed):
        with zipfile.ZipFile(out / "failed" / f"{index}-{unit.replace('/', '_')}.zip", "w") as zf:
            zf.writestr(
                "compilation_database.json",
                json.dumps([{"directory": str(tmp_path / "src"), "file": str(tmp_path / unit)}]),
            )
            zf.writestr("stderr", (stderr or NAKED).format(root=tmp_path))
    return str(out / "metadata.json")


def run(tmp_path, units, succeeded, failed, entries, stderr=None):
    accepted = tmp_path / "coverage-gaps.json"
    accepted.write_text(json.dumps({"accepted": [], "codechecker": entries}))
    return codechecker_coverage.check(
        str(accepted),
        CFG,
        record(tmp_path, succeeded, failed, stderr),
        database(tmp_path, units),
        str(tmp_path),
    )


def test_every_unit_analysed_passes(tmp_path):
    assert run(tmp_path, ["src/a.c", "src/b.c"], ["src/a.c", "src/b.c"], [], []) == ([], [], [])


def test_assembly_codechecker_skips_is_listed_not_failed(tmp_path):
    failures, _, assembly = run(tmp_path, ["src/a.c", "src/start.S"], ["src/a.c"], [], [])
    assert failures == [] and assembly == ["src/start.S: assembly, not analysed"]


def test_a_unit_codechecker_skipped_fails(tmp_path):
    failures, _, _ = run(tmp_path, ["src/a.c", "src/b.c"], ["src/a.c"], [], [])
    assert len(failures) == 1 and failures[0].startswith("src/b.c:")


def test_a_failed_unit_without_an_accepted_gap_fails_naming_clangs_error(tmp_path):
    failures, _, _ = run(tmp_path, ["src/a.c", "src/m.c"], ["src/a.c"], ["src/m.c"], [])
    assert failures == [
        "src/m.c:12: non-ASM statement in naked function is not supported "
        "(costs src/m.c) is not an accepted gap"
    ]


def test_an_accepted_gap_accepts_its_failed_unit(tmp_path):
    failures, matched, _ = run(tmp_path, ["src/a.c", "src/m.c"], ["src/a.c"], ["src/m.c"], [GAP])
    assert failures == [] and len(matched) == 1


def test_an_accepted_gap_does_not_accept_a_unit_it_does_not_list(tmp_path):
    # The same error, in a header, reached by a unit added later.
    header_error = "{root}/src/m.c:12:5: error: e\n"
    failures, _, _ = run(
        tmp_path, ["src/m.c", "src/n.c"], [], ["src/m.c", "src/n.c"], [GAP], stderr=header_error
    )
    assert len(failures) == 1 and "costs src/n.c" in failures[0]


def test_an_accepted_gap_for_another_configuration_does_not_apply(tmp_path):
    gap = dict(GAP, configurations=["unix-standard"])
    failures, _, _ = run(tmp_path, ["src/m.c"], [], ["src/m.c"], [gap])
    assert len(failures) == 1 and "not an accepted gap" in failures[0]


def test_a_failure_without_an_error_location_cannot_be_accepted(tmp_path):
    failures, _, _ = run(
        tmp_path,
        ["src/m.c"],
        [],
        ["src/m.c"],
        [GAP],
        stderr="clang: error: unable to execute command: Segmentation fault\n",
    )
    assert any("no compiler error location" in f for f in failures)


def test_an_accepted_gap_not_observed_is_stale(tmp_path):
    failures, _, _ = run(tmp_path, ["src/m.c"], ["src/m.c"], [], [GAP])
    assert len(failures) == 1 and "not observed" in failures[0]


def test_a_database_with_units_and_no_record_fails(tmp_path):
    accepted = tmp_path / "coverage-gaps.json"
    accepted.write_text(json.dumps({"codechecker": []}))
    failures, _, _ = codechecker_coverage.check(
        str(accepted),
        CFG,
        str(tmp_path / "none/metadata.json"),
        database(tmp_path, ["src/a.c"]),
        str(tmp_path),
    )
    assert len(failures) == 1 and "analysed none of the 1 units" in failures[0]


def test_a_record_of_another_database_fails(tmp_path):
    failures, _, _ = run(tmp_path, ["src/a.c"], ["src/a.c", "src/z.c"], [], [])
    assert len(failures) == 1 and failures[0].startswith("src/z.c: recorded by CodeChecker")


def test_duplicate_failed_actions_check_each_error(tmp_path):
    accepted = tmp_path / "coverage-gaps.json"
    accepted.write_text(json.dumps({"codechecker": [GAP]}))
    metadata = record(tmp_path, [], ["src/m.c", "src/m.c"])
    first = tmp_path / "reports/failed/0-src_m.c.zip"
    with zipfile.ZipFile(first, "w") as archive:
        archive.writestr(
            "compilation_database.json",
            json.dumps([{"directory": str(tmp_path), "file": str(tmp_path / "src/m.c")}]),
        )
        archive.writestr("stderr", f"{tmp_path}/src/m.c:99:5: error: unaccepted failure\n")
    failures, matched, _ = codechecker_coverage.check(
        accepted, CFG, metadata, database(tmp_path, ["src/m.c", "src/m.c"]), str(tmp_path)
    )
    assert failures
    assert any("src/m.c:99:" in failure for failure in failures)
    assert len(matched) == 1


def test_duplicate_failed_actions_require_all_archives(tmp_path):
    accepted = tmp_path / "coverage-gaps.json"
    accepted.write_text(json.dumps({"codechecker": [GAP]}))
    metadata = record(tmp_path, [], ["src/m.c", "src/m.c"])
    (tmp_path / "reports/failed/0-src_m.c.zip").unlink()
    failures, _, _ = codechecker_coverage.check(
        accepted, CFG, metadata, database(tmp_path, ["src/m.c", "src/m.c"]), str(tmp_path)
    )
    assert failures


def test_duplicate_failed_actions_accept_each_matching_gap(tmp_path):
    failures, matched, _ = run(tmp_path, ["src/m.c", "src/m.c"], [], ["src/m.c", "src/m.c"], [GAP])
    assert failures == []
    assert len(matched) == 2
