#!/usr/bin/env python3
"""Fail a cppcheck run whose SARIF shows code it could not analyse, unless the gap is accepted.

cppcheck reports some conditions as ordinary results although each one means part of a translation
unit was never analysed: a preprocessor #error, a parse it gave up on, an internal failure, a file
or forced include it cannot open. Uploaded as findings, they let a run look clean because the
analyser stopped early. Its "Checking <file>" progress lines cannot show this either: they are
printed for a file that then aborts at #error.

So the assertion is on the results: no coverage-failure result outside the project's accepted gaps
(analysis/coverage-gaps.json). An accepted entry matches on id, repository-relative file and line,
and applies to the configurations it names. An accepted entry that matched nothing fails as stale,
so a gap a tool upgrade closed is removed rather than left asserting a limitation that no longer
exists.

An entry also accepts only the translation units it lists. A gap in a header is reached by every
unit that includes it, and cppcheck abandons each such unit whole, so matching on the location
alone would accept a unit added later, first-party included, without analysing any of it. Native
SARIF reports an identical result once however many units hit it, so the units come from cppcheck's
own build directories: files.txt names the unit behind each record, <basename>.a1 for the first unit
of a basename in the group and .a2, .a3 for the next, and each record holds the errors analysing that
unit produced. Only the run's own groups are read, <stem>-<n> for each compile_commands-<n>.json in
--inputs, and a missing one fails, so a directory left by an earlier run cannot supply the
attribution. A coverage-failure result that no build record attributes to a unit fails too, since
the entry could not then be held to its units. A coverage failure in a build record but not in the
SARIF, which is what a cppcheck suppression of it produces, is held to the accepted gaps the same
way, so suppressing a coverage failure does not hide it.
"""

import argparse
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The results that end the analysis of the unit they occur in. cppcheck's own critical errors,
# ErrorLogger::mCriticalErrorIds in lib/errorlogger.cpp of cppcheck 2.22.0, without its three
# premium-* ids, which only Cppcheck Premium reports; and the preprocessor outputs that
# Preprocessor::reportOutput in lib/preprocessor.cpp returns as the unit's error, of which only
# missingIncludeExplicit, a forced include cppcheck cannot open, is not also critical. Recheck both
# when the cppcheck pin moves.
COVERAGE_FAILURE_IDS = {"cppcheckError", "cppcheckLimit", "directiveAsMacroParameter",
                        "includeNestedTooDeeply", "internalAstError", "instantiationError",
                        "internalError", "missingFile", "missingIncludeExplicit",
                        "preprocessorErrorDirective", "syntaxError", "unhandledChar",
                        "unknownMacro"}


def location_of(result):
    try:
        physical = result["locations"][0]["physicalLocation"]
    except (KeyError, IndexError, TypeError):
        return None, None
    return physical.get("artifactLocation", {}).get("uri"), physical.get("region", {}).get("startLine")


def matches(entry, rule, uri, line):
    return (entry["id"] == rule and entry["file"] == uri
            and ("line" not in entry or entry["line"] == line))


RECORD = re.compile(r".+\.a[0-9]+")


def unit_errors(inputs, cache_stem, root):
    """({(rule, file, line): {unit, ...}}, failures) from the build directories <cache_stem>-<n>,
    one per group compile_commands-<n>.json in INPUTS.

    file is as cppcheck reports it (repository-relative under -rp); unit is repository-relative when
    it lies inside ROOT, and a relative unit is taken relative to ROOT, cppcheck's working directory."""
    found, failures = {}, []
    root = os.path.abspath(root)
    groups = sorted(Path(db).name[len("compile_commands-"):-len(".json")]
                    for db in glob.glob(os.path.join(glob.escape(inputs), "compile_commands-*.json")))
    for group in groups:
        directory = f"{cache_stem}-{group}"
        listing = Path(directory, "files.txt")
        try:
            lines = listing.read_text().splitlines()
        except OSError as exc:
            failures.append(f"{listing}: unreadable ({exc}), so the coverage of group {group}'s "
                            f"units is unknown")
            continue
        for record in lines:
            name, _, _, unit = (record.split(":", 3) + ["", "", ""])[:4]
            if not RECORD.fullmatch(name) or not unit:
                continue
            unit = os.path.normpath(os.path.join(root, unit))
            if unit.startswith(root + os.sep):
                unit = unit[len(root) + 1:].replace(os.sep, "/")
            path = Path(directory, name)
            try:
                errors = ET.parse(path).getroot().iter("error")
            except (OSError, ET.ParseError) as exc:
                failures.append(f"{path}: unreadable ({exc}), so the coverage of {unit} is unknown")
                continue
            for error in errors:
                rule = error.get("id")
                if rule not in COVERAGE_FAILURE_IDS:
                    continue
                found.setdefault((rule, *record_location(error)), set()).add(unit)
    return found, failures


def record_location(error):
    """(file, line) of a build record's error, keyed as cppcheck's SARIF keys the same result.

    The record writes an error's locations in reverse and its line as at least 0
    (ErrorMessage::toXML, lib/errorlogger.cpp:611-618 of cppcheck 2.22.0); the SARIF writes them in
    order and its line as at least 1 (SarifReport::serializeLocations, lib/sarifreport.cpp:122-128),
    and location_of takes the first. So the key is the record's last location, its line raised to
    1: a bail-out cppcheck builds without a token is at line 0 in the record and line 1 in the SARIF.
    An error with no location is (None, None); the SARIF leaves such a result out
    (lib/sarifreport.cpp:144-146), so it is checked from the record alone."""
    locations = error.findall("location")
    if not locations:
        return None, None
    try:
        line = max(int(locations[-1].get("line")), 1)
    except (TypeError, ValueError):
        line = None
    return locations[-1].get("file"), line


def where(uri, line):
    return f"{uri}:{line}" if uri else "(no location)"


def check(accepted_path, configuration, sarif_paths, inputs, cache_stem, root="."):
    """(failures, matched): each failure a printable line."""
    failures = []
    try:
        accepted = [e for e in json.loads(Path(accepted_path).read_text()).get("accepted", [])
                    if configuration in e["configurations"]]
        for entry in accepted:
            missing = {"id", "file"} - set(entry)
            if missing:
                raise ValueError(f"an entry is missing {', '.join(sorted(missing))}: {entry}")
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        return [f"{accepted_path}: unusable accepted-gaps file: {exc}"], []
    units, unreadable = unit_errors(inputs, cache_stem, root)
    failures += unreadable
    used, matched, accepted_keys, reported = set(), [], {}, set()
    for sarif in sarif_paths:
        try:
            runs = json.loads(Path(sarif).read_text())["runs"]
            results = [r for run in runs for r in run.get("results", [])]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(f"{sarif}: missing or not SARIF ({exc}), so the run's coverage is "
                            f"unknown")
            continue
        for result in results:
            rule = result.get("ruleId")
            if rule not in COVERAGE_FAILURE_IDS:
                continue
            uri, line = location_of(result)
            reported.add((rule, uri, line))
            hit = next((i for i, e in enumerate(accepted) if matches(e, rule, uri, line)), None)
            place = where(uri, line)
            if hit is None:
                message = (result.get("message") or {}).get("text", "")
                failures.append(f"{place}: {rule}: {message} [{sarif}] is not an accepted gap")
            else:
                used.add(hit)
                matched.append(f"{place}: {rule} (accepted)")
                accepted_keys[(rule, uri, line)] = accepted[hit]
    # A suppression removes a result from the SARIF but not from the unit's build record, so the
    # records are checked as well: a suppression does not accept a coverage gap.
    for (rule, uri, line), reached in sorted(units.items(), key=str):
        if (rule, uri, line) in reported:
            continue
        place = where(uri, line)
        hit = next((i for i, e in enumerate(accepted) if matches(e, rule, uri, line)), None)
        if hit is None and uri is None:
            failures.append(f"{place}: {rule}: in the build record of {', '.join(sorted(reached))} "
                            f"without a location, which cppcheck's SARIF leaves out; not an "
                            f"accepted gap")
        elif hit is None:
            failures.append(f"{place}: {rule}: in the build record of {', '.join(sorted(reached))} "
                            f"but not in the SARIF and not an accepted gap; a suppression does not "
                            f"accept a coverage gap")
        else:
            used.add(hit)
            matched.append(f"{place}: {rule} (accepted, build record only)")
            accepted_keys[(rule, uri, line)] = accepted[hit]
    for (rule, uri, line), entry in sorted(accepted_keys.items(), key=str):
        reached = units.get((rule, uri, line))
        place = where(uri, line)
        if not reached:
            failures.append(f"{place}: {rule}: no build record of this run's cppcheck groups "
                            f"({cache_stem}-<n>) attributes it to a unit, so the entry cannot be "
                            f"held to its translation_units")
            continue
        for unit in sorted(reached - set(entry.get("translation_units", []))):
            failures.append(f"{place}: {rule}: costs {unit}, which the accepted entry does not "
                            f"list; an entry accepts only the translation units it names")
    for i, entry in enumerate(accepted):
        if i not in used:
            place = f"{entry['file']}:{entry['line']}" if "line" in entry else entry["file"]
            failures.append(f"{place}: {entry['id']}: accepted for {configuration} but not "
                            f"observed; remove the entry if the gap has closed")
    return failures, matched


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sast-cppcheck-coverage", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accepted", required=True, metavar="FILE",
                    help="the project's accepted coverage gaps (analysis/coverage-gaps.json)")
    ap.add_argument("--configuration", required=True, metavar="NAME")
    ap.add_argument("--inputs", required=True, metavar="DIR",
                    help="the run's inputs directory; its compile_commands-<n>.json name the groups")
    ap.add_argument("--cache-stem", required=True, metavar="STEM",
                    help="the run's cppcheck build directories are STEM-<n>; they attribute each "
                         "coverage failure to the translation units it cost")
    ap.add_argument("--root", default=".",
                    help="repository the units are made relative to (default: .)")
    ap.add_argument("sarif", nargs="+", metavar="SARIF")
    args = ap.parse_args(argv)
    failures, matched = check(args.accepted, args.configuration, args.sarif,
                              args.inputs, args.cache_stem, args.root)
    for line in matched:
        print(line)
    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    print(f"{args.configuration}: {len(args.sarif)} SARIF file(s), {len(matched)} accepted gap "
          f"result(s), {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
