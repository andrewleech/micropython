#!/usr/bin/env python3
"""Compilation database completeness check.

The database itself comes from compiledb, run over the build's own make line. What compiledb does
not do is prove the database covers the build, and a database that looks valid while silently
omitting translation units yields a passing analysis over a fraction of the code. So this checks it
against the build's own output: every object file under the build directory is explained, either by
a database entry whose -o names it or by an assembly source the build compiled, and every entry's
object exists. An object left behind by a source that has since been deleted is reported as stale
rather than failing, because the build can no longer make or link it. Exits non-zero on any other
discrepancy.
"""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

ASM_SUFFIXES = (".s", ".S")


def arguments_of(entry):
    return entry.get("arguments") or shlex.split(entry["command"])


def entry_file(entry):
    return os.path.normpath(os.path.join(entry["directory"], entry["file"]))


def output_of(entry):
    """The object an entry writes, absolute, or None when the invocation names none."""
    argv = arguments_of(entry)
    out = entry.get("output")
    for i, a in enumerate(argv):
        if a == "-o" and i + 1 < len(argv):
            out = argv[i + 1]
        elif a.startswith("-o") and len(a) > 2:
            out = a[2:]
    return os.path.normpath(os.path.join(entry["directory"], out)) if out else None


def _make_tokens(line):
    """Split a make rule line on whitespace, honouring the escapes GCC writes (`\\ ` and `$$`)."""
    if "\\" not in line and "$" not in line:
        return line.split()
    tokens, current, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line) and line[i + 1] in " #":
            current.append(line[i + 1])
            i += 2
            continue
        if c == "$" and line[i + 1:i + 2] == "$":
            current.append("$")
            i += 2
            continue
        if c.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(c)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def first_rule(text):
    """The prerequisites of the first rule in a dependency file, as written, or None.

    Only the first rule is read: it is the one the compiler wrote. Later rules in a .P are the phony
    targets mkrules.mk appends, one per header, which list the same headers again.
    """
    for line in text.replace("\\\n", " ").splitlines():
        tokens = _make_tokens(line)
        for i, token in enumerate(tokens):
            if token.endswith(":"):
                return tokens[i + 1:]
    return None


def objects_on_disk(build_dir, exclude):
    """Object files belonging to this configuration. A configuration's build directory can contain
    another's: the bootloader builds into build-$(BOARD)/mboot, nested inside the firmware's own
    build directory, so an unscoped scan makes the firmware check's result depend on whether the
    bootloader happens to have been built."""
    out = set()
    for p in Path(build_dir).rglob("*.o"):
        rel = p.relative_to(build_dir).parts
        if any(x in rel for x in exclude):
            continue
        out.add(os.path.normpath(os.path.abspath(p)))
    return out


def source_roots(entries, build_dir):
    """The directories the build resolves sources against, recovered from its own C entries.

    MicroPython's mkrules.mk puts <build>/<stem>.o for a source <root>/<stem>.c found on its vpath,
    with the port directory and $(TOP) as roots. Each entry whose object sits under the build
    directory therefore gives away one root: its source path minus the object's stem.
    """
    base = os.path.normpath(os.path.abspath(build_dir))
    roots = set()
    for entry in entries:
        obj = output_of(entry)
        if obj is None or not obj.startswith(base + os.sep):
            continue
        stem = os.path.splitext(os.path.relpath(obj, base))[0]
        source = os.path.splitext(entry_file(entry))[0]
        if source.endswith(os.sep + stem):
            roots.add(source[:-len(stem) - 1] or os.sep)
    return sorted(roots)


def assembly_source(obj, build_dir, roots):
    """The .s or .S the build compiled into obj, or None.

    Assembly is compiled without -MD (mkrules.mk's %.s and %.S rules), so no dependency file names
    its source, and an assembler invocation is not one compiledb records. The source is found the
    way make found it: the object's stem under each root the C entries show the build using.
    """
    stem = os.path.splitext(os.path.relpath(obj, os.path.abspath(build_dir)))[0]
    for root in roots:
        for suffix in ASM_SUFFIXES:
            candidate = os.path.join(root, stem + suffix)
            if os.path.isfile(candidate):
                return candidate
    return None


def deleted_source(obj, directories):
    """The source an object was compiled from, when that source no longer exists; else None.

    The port's make never removes an object whose source was deleted, so it stays in the build
    directory until a clean. The build cannot make or link it any more, since its source list can
    no longer name the file, so it is stale rather than a unit the database missed.

    The object's own dependency file names its source. A relative source is resolved only from a
    directory the database's entries compile in and from which every other relative prerequisite
    of the same rule exists, which is what identifies the directory the object was compiled in. A
    source that cannot be placed that way is not called deleted, so the object stays unexplained
    and fails the check.
    """
    stem = os.path.splitext(obj)[0]
    for dep in (stem + ".d", stem + ".P"):
        if not os.path.isfile(dep):
            continue
        try:
            prerequisites = first_rule(Path(dep).read_text(errors="surrogateescape"))
        except OSError:
            return None
        if not prerequisites:
            return None
        source, others = prerequisites[0], prerequisites[1:]
        if os.path.isabs(source):
            return None if os.path.exists(source) else source
        relative_others = [p for p in others if not os.path.isabs(p)]
        if not relative_others:
            return None
        compiled_in = [d for d in directories
                       if all(os.path.exists(os.path.join(d, p)) for p in relative_others)]
        if compiled_in and not any(os.path.exists(os.path.join(d, source)) for d in compiled_in):
            return os.path.normpath(os.path.join(compiled_in[0], source))
        return None
    return None


def completeness(entries, build_dir, exclude=()):
    """Reconcile the database against the build's own object files."""
    outputs, no_output = set(), []
    for entry in entries:
        obj = output_of(entry)
        if obj is None:
            no_output.append(entry["file"])
        else:
            outputs.add(obj)
    on_disk = objects_on_disk(build_dir, exclude)
    roots = source_roots(entries, build_dir)
    directories = sorted({os.path.normpath(e["directory"]) for e in entries})
    assembly, stale, unexplained = {}, {}, []
    for obj in sorted(on_disk - outputs):
        source = assembly_source(obj, build_dir, roots)
        if source:
            assembly[obj] = source
            continue
        gone = deleted_source(obj, directories)
        if gone:
            stale[obj] = gone
        else:
            unexplained.append(obj)
    report = {
        "build_dir": str(Path(build_dir).resolve()),
        "excluded_subdirectories": sorted(exclude),
        "translation_units": len(entries),
        "object_files_on_disk": len(on_disk),
        "explained_by_database": len(outputs & on_disk),
        "explained_by_assembly_source": len(assembly),
        "assembly": {obj: assembly[obj] for obj in sorted(assembly)},
        "stale_objects_of_deleted_sources": stale,
        "objects_unexplained": unexplained,
        "entries_with_no_object": sorted(outputs - on_disk),
        "entries_with_no_output": sorted(no_output),
    }
    report["complete"] = not (unexplained or report["entries_with_no_object"] or no_output)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sast-compdb", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check-db", required=True, metavar="DB",
                    help="the compile_commands.json to reconcile against the build directory")
    ap.add_argument("--build-dir", required=True,
                    help="the build output directory it describes")
    ap.add_argument("--exclude", action="append", default=[], metavar="SUBDIR",
                    help="build subdirectory belonging to another configuration, repeatable")
    args = ap.parse_args(argv)

    path = Path(args.check_db)
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"sast-compdb: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not entries:
        print(f"sast-compdb: {path} is empty", file=sys.stderr)
        return 2
    if not Path(args.build_dir).is_dir():
        print(f"sast-compdb: build directory {args.build_dir} does not exist", file=sys.stderr)
        return 2

    exclude = tuple(sorted(set(args.exclude)))
    report = completeness(entries, args.build_dir, exclude)
    print(f"{path}")
    print(f"  {'excluding':32s} {', '.join(exclude) if exclude else 'nothing'}")
    for key in ("translation_units", "object_files_on_disk", "explained_by_database",
                "explained_by_assembly_source"):
        print(f"  {key:32s} {report[key]}")
    for obj, source in report["assembly"].items():
        print(f"      {obj} <- {source}")
    if report["stale_objects_of_deleted_sources"]:
        print(f"  {'stale objects (source deleted)':32s} "
              f"{len(report['stale_objects_of_deleted_sources'])}; not part of this build, "
              f"removed by make clean")
        for obj, source in report["stale_objects_of_deleted_sources"].items():
            print(f"      {obj} <- {source} (deleted)")
    for key in ("objects_unexplained", "entries_with_no_object", "entries_with_no_output"):
        if report[key]:
            print(f"  {key:32s} {len(report[key])}", file=sys.stderr)
            for item in report[key]:
                print(f"      {item}", file=sys.stderr)
    print(f"  {'complete':32s} {report['complete']}")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
