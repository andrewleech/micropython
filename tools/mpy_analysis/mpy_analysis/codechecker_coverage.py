#!/usr/bin/env python3
"""Fail a CodeChecker run that did not analyse every unit of its database, unless the gap is accepted.

CodeChecker keeps its own record of a run in <output>/metadata.json: for each analyzer, the source of
every build action it analysed and of every one that failed, and for each failure the compiler's
output in <output>/failed/<unit>_<analyzer>_<hash>.plist_compile_error.zip. It exits 3 when a unit
failed, but that status says nothing of a unit it never attempted: an entry it skips counts as
neither, and a database whose every entry it skips is "No analysis is required", exit 0, with no
metadata.json at all. So the assertion is on the record against the database the run was given:
every unit in it is recorded, and every recorded failure is an accepted gap.

Assembly is the exception. CodeChecker treats an entry whose source it cannot give a language, such
as a .s or .S file, as a link step and skips it, and no C analyser reads assembly, so an assembly
unit is listed as not analysed rather than failed.

The Clang Static Analyzer analyses only what clang can compile, so a failed unit is usually one
whose code clang rejects where GCC accepts it. The accepted gaps are the "codechecker" list of the
project's analysis/coverage-gaps.json, with the fields and meaning of cppcheck's "accepted" list
there: an entry names the analyzer as its id ("clangsa"), the location of the first error clang
reported in the failed unit as repository-relative file and line, the translation units that error
costs, and the configurations it applies to. A unit whose output holds no error location, a crash
for example, matches no entry. A failed unit an entry does not list fails, since an error in a
header is reached by every unit that includes it. An entry for the configuration that matched
nothing fails as stale, so a gap a tool upgrade closed is removed rather than left asserting a
limitation that no longer exists.
"""

import argparse
import collections
import glob
import json
import os
import re
import sys
import zipfile
from pathlib import Path

ANALYZER = "clangsa"
# GCC's assembler source extensions.
ASSEMBLY = {".s", ".S", ".sx"}
# clang's diagnostic format, file:line:column: [fatal ]error: message.
ERROR = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):\d+: (?:fatal )?error: (?P<message>.*)$")


def relative(path, root):
    """PATH made repository-relative when it lies inside ROOT, otherwise left absolute."""
    path = os.path.realpath(path)
    if path.startswith(root + os.sep):
        return path[len(root) + 1:].replace(os.sep, "/")
    return path


def database_units(db_path, root):
    """The units a compilation database names, repository-relative, with their multiplicity."""
    entries = json.loads(Path(db_path).read_text())
    return collections.Counter(relative(os.path.join(e["directory"], e["file"]), root)
                               for e in entries)


def first_error(archive, root):
    """(unit, file, line, message) of a failed unit, from CodeChecker's record of the failure: the
    unit its build action compiled and the first error in the compiler's output, None where the
    output has none. A relative location is relative to the build action's directory."""
    with zipfile.ZipFile(archive) as zf:
        action = json.loads(zf.read("compilation_database.json"))[0]
        stderr = zf.read("stderr").decode("utf-8", "replace")
    directory = action.get("directory", "")
    unit = relative(os.path.join(directory, action["file"]), root)
    for line in stderr.splitlines():
        match = ERROR.match(line)
        if match:
            return (unit, relative(os.path.join(directory, match["file"]), root),
                    int(match["line"]), match["message"])
    return unit, None, None, None


def check(accepted_path, configuration, metadata_path, db_path, root="."):
    """(failures, matched, assembly): each a list of printable lines, assembly naming the units no
    C analyser reads."""
    root = os.path.realpath(root)
    try:
        accepted = [e for e in json.loads(Path(accepted_path).read_text()).get("codechecker", [])
                    if configuration in e["configurations"]]
        for entry in accepted:
            missing = {"id", "file", "line", "translation_units"} - set(entry)
            if missing:
                raise ValueError(f"an entry is missing {', '.join(sorted(missing))}: {entry}")
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
        return [f"{accepted_path}: unusable accepted-gaps file: {exc}"], [], []
    try:
        expected = database_units(db_path, root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"{db_path}: unusable compilation database: {exc}"], [], []
    assembly = [f"{unit}: assembly, not analysed" for unit in sorted(expected)
                if os.path.splitext(unit)[1] in ASSEMBLY]
    for unit in [u for u in expected if os.path.splitext(u)[1] in ASSEMBLY]:
        del expected[unit]
    metadata_path = Path(metadata_path)
    if not metadata_path.exists():
        return [f"{metadata_path} is missing, so CodeChecker analysed none of the "
                f"{sum(expected.values())} units in {db_path}"], [], assembly
    try:
        tool = json.loads(metadata_path.read_text())["tools"][0]
        statistics = tool["analyzers"][ANALYZER]["analyzer_statistics"]
        succeeded = collections.Counter(relative(s, root) for s in statistics["successful_sources"])
        failed = collections.Counter(relative(s, root) for s in statistics["failed_sources"])
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        return [f"{metadata_path}: not a CodeChecker record of a {ANALYZER} run: {exc}"], [], assembly

    failures, matched = [], []
    for unit, count in sorted((expected - (succeeded + failed)).items()):
        failures.append(f"{unit}: in the database {count} time(s) more than CodeChecker analysed "
                        f"it; it was skipped or never attempted")
    for unit, count in sorted(((succeeded + failed) - expected).items()):
        failures.append(f"{unit}: recorded by CodeChecker but not in {db_path}, so {metadata_path} "
                        f"is not this run's record")

    errors = {}
    for archive in sorted(glob.glob(os.path.join(glob.escape(str(metadata_path.parent)),
                                                 "failed", "*.zip"))):
        try:
            unit, *error = first_error(archive, root)
        except (OSError, ValueError, KeyError, IndexError, TypeError, zipfile.BadZipFile) as exc:
            failures.append(f"{archive}: unreadable ({exc}), so a failure cannot be attributed")
            continue
        errors[unit] = error
    used = set()
    for unit in sorted(failed):
        if unit not in errors:
            failures.append(f"{unit}: failed, and CodeChecker kept no record of why under "
                            f"{metadata_path.parent / 'failed'}")
            continue
        file, line, message = errors[unit]
        if file is None:
            failures.append(f"{unit}: failed with no compiler error location, a crash or timeout; "
                            f"not an accepted gap")
            continue
        hit = next((i for i, e in enumerate(accepted)
                    if e["id"] == ANALYZER and e["file"] == file and e["line"] == line), None)
        if hit is None:
            failures.append(f"{file}:{line}: {message} (costs {unit}) is not an accepted gap")
        elif unit not in accepted[hit]["translation_units"]:
            failures.append(f"{file}:{line}: costs {unit}, which the accepted entry does not list; "
                            f"an entry accepts only the translation units it names")
        else:
            used.add(hit)
            matched.append(f"{file}:{line}: {message} (costs {unit}, accepted)")
    for i, entry in enumerate(accepted):
        if i not in used:
            failures.append(f"{entry['file']}:{entry['line']}: {entry['id']}: accepted for "
                            f"{configuration} but not observed; remove the entry if the gap has "
                            f"closed")
    return failures, matched, assembly


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sast-codechecker-coverage", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accepted", required=True, metavar="FILE",
                    help="the project's accepted coverage gaps (analysis/coverage-gaps.json)")
    ap.add_argument("--configuration", required=True, metavar="NAME")
    ap.add_argument("--db", required=True, metavar="FILE",
                    help="the compilation database the run analysed")
    ap.add_argument("--root", default=".",
                    help="repository the units are made relative to (default: .)")
    ap.add_argument("metadata", metavar="METADATA",
                    help="the run's metadata.json, in CodeChecker's output directory")
    args = ap.parse_args(argv)
    failures, matched, assembly = check(args.accepted, args.configuration, args.metadata, args.db,
                                        args.root)
    for line in assembly + matched:
        print(line)
    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    print(f"{args.configuration}: {len(assembly)} assembly unit(s) not analysed, {len(matched)} "
          f"accepted gap(s), {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
