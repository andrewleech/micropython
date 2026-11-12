import json
from pathlib import Path

from mpy_analysis import coverage

CFG = "stm32-BOARD"


def sarif(path, results):
    path.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {},
                        "results": [
                            {
                                "ruleId": rule,
                                "message": {"text": "m"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": uri},
                                            "region": {"startLine": line},
                                        }
                                    }
                                ],
                            }
                            for rule, uri, line in results
                        ],
                    }
                ],
            }
        )
    )
    return str(path)


def accepted(path, entries):
    path.write_text(json.dumps({"accepted": entries}))
    return str(path)


GAP = {
    "id": "internalAstError",
    "file": "py/grammar.h",
    "line": 84,
    "translation_units": ["py/parse.c"],
    "configurations": [CFG],
}


def cache(tmp_path, errors):
    """A run's inputs directory with one group, and that group's cppcheck build directory recording,
    per unit, the (rule, file, line) errors it produced, or an <error> element as written. Records
    are named as cppcheck names them: <basename>.a1 for the first unit of a basename, .a2 for the
    next. Returns (inputs, stem)."""
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "compile_commands-0.json").write_text("[]")
    directory = tmp_path / "cache-0"
    directory.mkdir(exist_ok=True)
    listing, seen = [], {}
    for unit, unit_errors in errors.items():
        stem = Path(unit).stem
        seen[stem] = seen.get(stem, 0) + 1
        name = f"{stem}.a{seen[stem]}"
        listing.append(f"{name}:::{tmp_path / unit}")
        body = "".join(
            error
            if isinstance(error, str)
            else f'<error id="{error[0]}" severity="error" msg="m" file0="{unit}">'
            f'<location file="{error[1]}" line="{error[2]}" column="1"/></error>'
            for error in unit_errors
        )
        (directory / name).write_text(f'<?xml version="1.0"?><analyzerinfo>{body}</analyzerinfo>')
    (directory / "files.txt").write_text("".join(line + "\n" for line in listing))
    return str(inputs), str(tmp_path / "cache")


def run(tmp_path, results, entries, *flags, units=None):
    """results as (rule, uri, line); units maps a unit to the results its build record holds, and
    defaults to each result recorded against the unit an entry names, or against src/x.c."""
    if units is None:
        units = {}
        for result in results:
            owner = next(
                (
                    e["translation_units"][0]
                    for e in entries
                    if (e["id"], e["file"], e.get("line")) == result
                ),
                "src/x.c",
            )
            units.setdefault(owner, []).append(result)
    inputs, stem = cache(tmp_path, units)
    return coverage.main(
        [
            "--accepted",
            accepted(tmp_path / "gaps.json", entries),
            "--configuration",
            CFG,
            "--inputs",
            inputs,
            "--cache-stem",
            stem,
            "--root",
            str(tmp_path),
            *flags,
            sarif(tmp_path / "r.sarif", results),
        ]
    )


def test_unaccepted_coverage_failure_fails(tmp_path):
    assert run(tmp_path, [("syntaxError", "src/a.c", 3)], [GAP]) == 1


def test_accepted_coverage_failure_passes(tmp_path):
    assert run(tmp_path, [("internalAstError", "py/grammar.h", 84)], [GAP]) == 0


def test_accepted_entry_matches_only_its_line_and_configuration(tmp_path):
    assert run(tmp_path, [("internalAstError", "py/grammar.h", 85)], [GAP]) == 1
    other = dict(GAP, configurations=["unix-standard"])
    assert run(tmp_path, [("internalAstError", "py/grammar.h", 84)], [other]) == 1


def test_ordinary_findings_are_not_coverage_failures(tmp_path):
    assert run(tmp_path, [("nullPointer", "src/a.c", 3)], []) == 0


def test_accepted_entry_not_observed_is_stale(tmp_path):
    assert run(tmp_path, [], [GAP]) == 1


def test_another_configurations_entry_is_not_stale_here(tmp_path):
    # This configuration may not compile the file, so it cannot reproduce the gap.
    assert run(tmp_path, [], [dict(GAP, configurations=["unix-standard"])]) == 0


def test_accepted_gap_fails_for_a_unit_the_entry_does_not_list(tmp_path):
    # A header gap is reached by every includer; a new one must not be accepted unanalysed.
    result = ("internalAstError", "py/grammar.h", 84)
    assert (
        run(tmp_path, [result], [GAP], units={"py/parse.c": [result], "src/board.c": [result]})
        == 1
    )
    assert run(tmp_path, [result], [GAP], units={"py/parse.c": [result]}) == 0


def test_unlisted_unit_sharing_a_listed_units_basename_fails(tmp_path):
    # cppcheck records the second unit of a basename as <basename>.a2.
    result = ("internalAstError", "py/grammar.h", 84)
    assert (
        run(tmp_path, [result], [GAP], units={"py/parse.c": [result], "board/parse.c": [result]})
        == 1
    )


def test_build_directory_of_a_group_the_run_did_not_produce_is_not_read(tmp_path):
    result = ("internalAstError", "py/grammar.h", 84)
    stale = tmp_path / "cache-7"
    stale.mkdir()
    (stale / "parse.a1").write_text(
        '<?xml version="1.0"?><analyzerinfo><error id="internalAstError" '
        'severity="error" msg="m"><location file="py/grammar.h" '
        'line="84"/></error></analyzerinfo>'
    )
    (stale / "files.txt").write_text(f"parse.a1:::{tmp_path / 'py/parse.c'}\n")
    assert run(tmp_path, [result], [GAP], units={}) == 1


def test_missing_build_directory_of_a_group_fails(tmp_path):
    inputs, stem = cache(tmp_path, {})
    (Path(inputs) / "compile_commands-1.json").write_text("[]")
    assert (
        coverage.main(
            [
                "--accepted",
                accepted(tmp_path / "gaps.json", []),
                "--configuration",
                CFG,
                "--inputs",
                inputs,
                "--cache-stem",
                stem,
                sarif(tmp_path / "r.sarif", []),
            ]
        )
        == 1
    )


def test_accepted_gap_no_build_directory_attributes_fails(tmp_path):
    result = ("internalAstError", "py/grammar.h", 84)
    assert run(tmp_path, [result], [GAP], units={}) == 1


def test_missing_or_unparseable_sarif_fails(tmp_path):
    gaps = accepted(tmp_path / "gaps.json", [])
    (tmp_path / "bad.sarif").write_text("{")
    inputs, stem = cache(tmp_path, {})
    for bad in ("absent.sarif", "bad.sarif"):
        assert (
            coverage.main(
                [
                    "--accepted",
                    gaps,
                    "--configuration",
                    CFG,
                    "--inputs",
                    inputs,
                    "--cache-stem",
                    stem,
                    str(tmp_path / bad),
                ]
            )
            == 1
        )


def test_unknown_macro_is_a_coverage_failure(tmp_path):
    # cppcheck abandons a unit at a macro it cannot see; it is one of its critical errors.
    assert run(tmp_path, [("unknownMacro", "src/a.c", 2)], []) == 1


def test_suppressed_coverage_failure_in_a_build_record_fails(tmp_path):
    # A suppression removes the result from the SARIF, not from the unit's build record.
    assert run(tmp_path, [], [], units={"src/a.c": [("syntaxError", "src/a.c", 2)]}) == 1


def test_accepted_gap_seen_only_in_a_build_record_is_held_to_its_units(tmp_path):
    result = ("internalAstError", "py/grammar.h", 84)
    assert run(tmp_path, [], [GAP], units={"py/parse.c": [result]}) == 0
    assert run(tmp_path, [], [GAP], units={"py/parse.c": [result], "src/board.c": [result]}) == 1


def test_forced_include_cppcheck_cannot_open_is_a_coverage_failure(tmp_path):
    # cppcheck abandons the unit, as it does a missing file, though it is not a critical error.
    assert run(tmp_path, [("missingIncludeExplicit", "port/u.c", 1)], []) == 1


def check(tmp_path, results, entries, units):
    inputs, stem = cache(tmp_path, units)
    return coverage.check(
        accepted(tmp_path / "gaps.json", entries),
        CFG,
        [sarif(tmp_path / "r.sarif", results)],
        inputs,
        stem,
        str(tmp_path),
    )[0]


def test_record_at_line_0_is_the_sarif_result_at_line_1(tmp_path):
    # cppcheck's SARIF clamps a line to 1, its build record to 0: one failure, not a second one
    # blaming a suppression, and an entry at line 1 accepts it.
    units = {"src/a.c": [("internalError", "src/a.c", 0)]}
    failures = check(tmp_path, [("internalError", "src/a.c", 1)], [], units)
    assert len(failures) == 1 and "not in the SARIF" not in failures[0]
    entry = {
        "id": "internalError",
        "file": "src/a.c",
        "line": 1,
        "translation_units": ["src/a.c"],
        "configurations": [CFG],
    }
    assert check(tmp_path, [("internalError", "src/a.c", 1)], [entry], units) == []


def test_record_locations_are_keyed_by_the_sarif_first(tmp_path):
    # The record lists an error's locations in reverse, so its last is the SARIF's first.
    record = (
        '<error id="syntaxError" severity="error" msg="m" file0="src/a.c">'
        '<location file="src/a.c" line="9" column="1"/>'
        '<location file="inc/h.h" line="4" column="1"/></error>'
    )
    entry = {
        "id": "syntaxError",
        "file": "inc/h.h",
        "line": 4,
        "translation_units": ["src/a.c"],
        "configurations": [CFG],
    }
    assert check(tmp_path, [("syntaxError", "inc/h.h", 4)], [entry], {"src/a.c": [record]}) == []


def test_record_error_without_a_location_fails(tmp_path):
    # cppcheck's SARIF leaves out a result with no location, so the record alone shows it.
    record = '<error id="internalError" severity="error" msg="m" file0="src/a.c"/>'
    failures = check(tmp_path, [], [], {"src/a.c": [record]})
    assert len(failures) == 1 and failures[0].startswith("(no location): internalError")
