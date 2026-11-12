#!/usr/bin/env python3
"""Replay a compilation database under GCC's -fanalyzer with an analysis-only GCC, SARIF per unit.

The build's own compiler is too old: the MicroPython build container carries arm-none-eabi-gcc
12.2.1, which has no SARIF output. GCC 13 and 14 write SARIF only to <source>.sarif in the working
directory. GCC 15 takes -fdiagnostics-add-output=sarif:file=PATH, so each unit gets its own named
file beside the normal diagnostics. The pinned Arm GNU Toolchain 15.2.Rel1 is used for this and
nothing else; the firmware is still built by the build's own compiler.

Each entry is compiled again with its own flags, minus the object and dependency-file outputs,
which would overwrite the build's, plus -o /dev/null, -Wno-error and the analyser. The SARIF is
GCC's own and is not rewritten.

The replay runs in ROOT rather than in the entry's directory, with every path argument rebased from
the entry's directory onto ROOT. GCC records a file in SARIF as the path it opened, relative to its
working directory (uriBaseId PWD), and code scanning reads a relative URI as repository-relative, so
a unit compiled in ports/stm32 would report ../../py/obj.c. Rebased, it reports
src/micropython/py/obj.c. Paths outside ROOT, such as the toolchain's, stay absolute. The working
directory changes nothing else the compiler resolves: a quoted #include searches the including
file's own directory first, then the -iquote and -I directories, which are rebased with the rest.

A unit that exits non-zero or writes no SARIF fails the run: its absence would read as a clean
result.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MIN_GCC = 15
DROP_WITH_VALUE = ("-o", "-MF", "-MT", "-MQ")
DROP = ("-MD", "-MMD")
# Options whose value is a path, in both the separate and the joined form. Checked in this order
# so that a joined value is split after the longest option name that matches.
PATH_OPTIONS = (
    "-idirafter",
    "-isysroot",
    "-isystem",
    "-imacros",
    "-include",
    "-iquote",
    "-iprefix",
    "-I",
)
# Options whose separate value is not a path relative to the working directory, and must not be
# mistaken for a source file. -iwithprefix values are relative to -iprefix, not rebased.
VALUE_OPTIONS = (
    "-D",
    "-U",
    "-x",
    "-A",
    "-Xpreprocessor",
    "-Xassembler",
    "-Xlinker",
    "-aux-info",
    "-dumpbase",
    "-dumpdir",
    "-l",
    "-u",
    "-e",
    "-T",
    "-L",
    "-B",
    "-z",
    "--param",
    "-iwithprefix",
    "-iwithprefixbefore",
)


def rebase(path, directory, root):
    """path, taken relative to directory, as ROOT-relative when inside ROOT, else absolute."""
    absolute = os.path.normpath(os.path.join(directory, path))
    rel = os.path.relpath(absolute, root)
    return absolute if rel == ".." or rel.startswith(".." + os.sep) else rel


def analysis_command(gcc, entry, sarif, root):
    argv = entry.get("arguments") or shlex.split(entry["command"])
    directory = entry["directory"]
    cmd, i = [gcc], 1
    while i < len(argv):
        a = argv[i]
        i += 1
        if a in DROP_WITH_VALUE:
            i += 1
            continue
        # The joined forms (-MFfile, -ofile) too, which GCC accepts as the same options.
        if (
            a in DROP
            or (a.startswith(("-MF", "-MT", "-MQ")) and len(a) > 3)
            or (a.startswith("-o") and len(a) > 2)
        ):
            continue
        if a in PATH_OPTIONS and i < len(argv):
            cmd += [a, rebase(argv[i], directory, root)]
            i += 1
            continue
        joined = next((o for o in PATH_OPTIONS if a.startswith(o) and len(a) > len(o)), None)
        if joined:
            cmd.append(joined + rebase(a[len(joined) :], directory, root))
        elif a.startswith("--sysroot="):
            cmd.append("--sysroot=" + rebase(a[len("--sysroot=") :], directory, root))
        elif a in VALUE_OPTIONS and i < len(argv):
            cmd += [a, argv[i]]
            i += 1
        elif a.startswith("-"):
            cmd.append(a)
        else:
            # Anything else not an option is an input file: the source.
            cmd.append(rebase(a, directory, root))
    return cmd + [
        "-o",
        "/dev/null",
        "-Wno-error",
        "-fanalyzer",
        f"-fdiagnostics-add-output=sarif:file={sarif}",
    ]


def gcc_major(gcc):
    proc = subprocess.run([gcc, "-dumpversion"], capture_output=True, text=True)
    match = re.match(r"(\d+)", proc.stdout.strip()) if proc.returncode == 0 else None
    return int(match.group(1)) if match else None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sast-fanalyzer",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--db", required=True, help="the configuration's compilation database")
    ap.add_argument("--gcc", required=True, help="the analysis compiler, GCC 15 or later")
    ap.add_argument(
        "--root",
        required=True,
        help="the repository root; each unit is compiled from here so that the SARIF "
        "names files repository-relative",
    )
    ap.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="directory for <n>.sarif per database entry and summary.json",
    )
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    args = ap.parse_args(argv)

    gcc = shutil.which(args.gcc)
    if gcc is None:
        print(f"sast-fanalyzer: {args.gcc} is not an executable compiler", file=sys.stderr)
        return 2
    major = gcc_major(gcc)
    if major is None or major < MIN_GCC:
        print(
            f"sast-fanalyzer: {gcc} reports version {major}; GCC {MIN_GCC} or later is needed "
            f"for -fdiagnostics-add-output=sarif:file=",
            file=sys.stderr,
        )
        return 2
    try:
        entries = json.loads(Path(args.db).read_text())
    except (OSError, ValueError) as exc:
        print(f"sast-fanalyzer: cannot read {args.db}: {exc}", file=sys.stderr)
        return 2

    root = os.path.abspath(args.root)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Other analyzers may share the report directory.
    for stale in out.glob("*.sarif"):
        if stale.stem.isascii() and stale.stem.isdecimal():
            stale.unlink()

    def run(item):
        n, entry = item
        sarif = out / f"{n}.sarif"
        proc = subprocess.run(
            analysis_command(gcc, entry, sarif, root), cwd=root, capture_output=True, text=True
        )
        return n, entry["file"], proc.returncode, proc.stderr[-2000:], sarif

    started = time.monotonic()
    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        rows = list(pool.map(run, enumerate(entries)))
    seconds = round(time.monotonic() - started, 1)

    rules, sarif_files, failures = Counter(), [], []
    for n, source, rc, stderr, sarif in rows:
        if not sarif.exists():
            failures.append(
                {
                    "unit": source,
                    "exit": rc,
                    "reason": "no SARIF written",
                    "stderr": stderr[-2000:],
                }
            )
            continue
        sarif_files.append(sarif.name)
        if rc != 0:
            failures.append(
                {"unit": source, "exit": rc, "reason": "non-zero exit", "stderr": stderr[-2000:]}
            )
        try:
            for run_ in json.loads(sarif.read_text())["runs"]:
                for result in run_.get("results", []):
                    rules[result.get("ruleId")] += 1
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({"unit": source, "exit": rc, "reason": f"unreadable SARIF: {exc}"})
    summary = {
        "gcc": gcc,
        "gcc_version": subprocess.run(
            [gcc, "--version"], capture_output=True, text=True
        ).stdout.splitlines()[0],
        "units": len(entries),
        "sarif_files": len(sarif_files),
        "failures": failures,
        "seconds": seconds,
        "results": sum(rules.values()),
        "results_by_rule": dict(rules.most_common()),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(
        f"units {summary['units']}, SARIF {summary['sarif_files']}, results {summary['results']}, "
        f"{seconds} s"
    )
    for rule, count in rules.most_common():
        print(f"  {count:5d} {rule}")
    for f in failures:
        print(f"FAIL {f['unit']}: {f['reason']} (exit {f['exit']})", file=sys.stderr)
        if f.get("stderr"):
            print("  " + f["stderr"].strip().replace("\n", "\n  "), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
