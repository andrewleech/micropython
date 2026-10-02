#!/usr/bin/env python3
"""Per compiler-flag group, the inputs cppcheck needs derived from the build's own compiler.

The compilation database names the compiler and flags of every translation unit. Four
things are derived from that compiler rather than chosen by hand, because each silently changes what
gets analysed when it is wrong:

  predefines  cppcheck defines neither __GNUC__ nor __arm__. Real code notices: CMSIS ends at
              "#error Unknown compiler." and tinyusb at "#error Compiler attribute porting is
              required", which aborts preprocessing of every translation unit that includes them.
              The macros the compiler itself predefines are extracted with -dM -E and force
              included, so the analyser preprocesses the branches the compiler took.
  has-builtins  __has_attribute and friends are compiler builtins that cppcheck's preprocessor
              does not provide, and an #if that invokes one fails to evaluate. Each query the
              source makes is put to the real compiler and the answers are emitted as a token
              pasting macro, so an unlisted query expands to an undefined identifier and evaluates
              to 0, which is what the compiler does for an attribute it does not support.
  limits      cppcheck's std.cfg does not define some C library limit macros (see
              limit_macro_fallbacks), so they are emitted from the compiler's own predefines.
  platform    type sizes and char signedness come from the compiler, not from a builtin platform
              name matched by eye. Getting this wrong produces both spurious findings and silent
              misses, and the two are indistinguishable from the output.

Translation units are grouped by the flags that affect any of the above, and each group is analysed
with its own derived set, as one plain cppcheck invocation over that group's database. This is the
recorded gap cppcheck itself does not close; everything else
about the run is cppcheck's own.

usage: sast-cppcheck-inputs DB OUTDIR

Writes OUTDIR/groups.json ([{index, compiler, flags, entries}], largest group first) and, per group,
compile_commands-<i>.json, predefines-<i>.h and platform-<i>.xml.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# Flags that can change the predefined macro set or the type model. Grouping on anything else would
# split the build into groups that derive identically.
PREDEFINE_FLAG_PREFIXES = ("-m", "-std=", "-f", "-O", "-ansi", "-pthread", "-march", "-mcpu",
                           "-target", "--target=", "-nostdinc")

# Emitted by -dM but meaningless or actively wrong to force include: their value depends on the
# file being compiled, not on the configuration.
POSITIONAL_MACROS = {"__FILE__", "__LINE__", "__DATE__", "__TIME__", "__TIMESTAMP__",
                     "__COUNTER__", "__BASE_FILE__", "__INCLUDE_LEVEL__"}

HAS_FAMILIES = ("__has_attribute", "__has_builtin", "__has_c_attribute", "__has_feature",
                "__has_extension")

SIZEOF_TYPES = {
    "bool": "_Bool",
    "short": "short",
    "int": "int",
    "long": "long",
    "long-long": "long long",
    "float": "float",
    "double": "double",
    "long-double": "long double",
    "pointer": "void *",
    "size_t": "__SIZE_TYPE__",
    "wchar_t": "__WCHAR_TYPE__",
}
CANDIDATE_SIZES = (1, 2, 4, 8, 12, 16)


class DerivationError(Exception):
    pass


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def resolved_file(entry):
    # Resolved, not merely normalised, so that a path compared against one a probe produced matches
    # when a directory in the checkout is a symlink.
    return Path(os.path.join(entry["directory"], entry["file"])).resolve()


def group_key(entry):
    return tuple(a for a in entry["arguments"][1:] if a.startswith(PREDEFINE_FLAG_PREFIXES))


def compiler_of(entry):
    return entry["arguments"][0]


def predefined_macros(cc, flags):
    """The macro set the compiler itself starts from, as #define lines."""
    proc = run([cc, *flags, "-dM", "-E", "-x", "c", "-"], input="")
    if proc.returncode != 0:
        raise DerivationError(
            f"{cc} could not report its predefined macros: {proc.stderr.strip()[:400]}")
    lines = []
    for line in proc.stdout.splitlines():
        name = line.split()[1].split("(")[0] if line.startswith("#define ") else None
        if name and name not in POSITIONAL_MACROS:
            lines.append(line)
    if not any(line.startswith("#define __STDC__") for line in lines):
        raise DerivationError(f"{cc} reported no __STDC__; the flags are probably not accepted")
    return sorted(lines)


def sizeof(cc, flags, ctype):
    """Ask the compiler for a type's size without needing a header or an execution environment."""
    for size in CANDIDATE_SIZES:
        src = f"int probe[(sizeof({ctype}) == {size}) ? 1 : -1];\n"
        proc = run([cc, *flags, "-w", "-fsyntax-only", "-x", "c", "-"], input=src)
        if proc.returncode == 0:
            return size
    raise DerivationError(f"{cc} accepted no candidate size for {ctype}")


def type_sizes(cc, flags):
    return {name: sizeof(cc, flags, ctype) for name, ctype in SIZEOF_TYPES.items()}


def platform_xml(cc, flags, macros, sizes):
    """A cppcheck platform description derived from the compiler, not selected by name."""
    defines = dict(
        (line.split()[1], " ".join(line.split()[2:])) for line in macros
        if line.startswith("#define ") and "(" not in line.split()[1])
    char_bit = defines.get("__CHAR_BIT__")
    if char_bit is None:
        raise DerivationError(f"{cc} did not report __CHAR_BIT__")
    sign = "unsigned" if "__CHAR_UNSIGNED__" in defines else "signed"
    body = "\n".join(f"    <{name}>{value}</{name}>" for name, value in sizes.items())
    return (
        '<?xml version="1.0"?>\n'
        "<!-- Generated by sast-cppcheck-inputs from the compilation database's own compiler.\n"
        f"     {cc} {' '.join(flags)} -->\n"
        "<platform>\n"
        f"  <char_bit>{char_bit}</char_bit>\n"
        f"  <default-sign>{sign}</default-sign>\n"
        "  <sizeof>\n"
        f"{body}\n"
        "  </sizeof>\n"
        "</platform>\n"
    )


# <limits.h> and <stdint.h> macro names, paired with the compiler predefine that carries the same
# value. Used only as a fallback, for the defect described in limit_macro_fallbacks.
LIMIT_MACRO_SOURCES = {name + "_MAX": "__" + name + "_MAX__" for name in (
    "SCHAR", "SHRT", "INT", "LONG", "INTMAX", "INTPTR", "PTRDIFF", "SIZE", "UINTMAX", "UINTPTR",
    "SIG_ATOMIC", "WCHAR", "WINT",
    "INT8", "INT16", "INT32", "INT64", "UINT8", "UINT16", "UINT32", "UINT64",
    "INT_LEAST8", "INT_LEAST16", "INT_LEAST32", "INT_LEAST64",
    "UINT_LEAST8", "UINT_LEAST16", "UINT_LEAST32", "UINT_LEAST64",
    "INT_FAST8", "INT_FAST16", "INT_FAST32", "INT_FAST64",
    "UINT_FAST8", "UINT_FAST16", "UINT_FAST32", "UINT_FAST64")}
LIMIT_MACRO_SOURCES.update({
    "LLONG_MAX": "__LONG_LONG_MAX__",
    "SIG_ATOMIC_MIN": "__SIG_ATOMIC_MIN__",
    "WCHAR_MIN": "__WCHAR_MIN__",
    "WINT_MIN": "__WINT_MIN__",
})
# Signed types whose minimum the compiler does not predefine. Two's complement, which every target
# this project builds for uses and which C23 requires.
LIMIT_MACRO_MINIMA = ("SCHAR", "SHRT", "INT", "LONG", "LLONG", "INTMAX", "INTPTR", "PTRDIFF",
                      "INT8", "INT16", "INT32", "INT64",
                      "INT_LEAST8", "INT_LEAST16", "INT_LEAST32", "INT_LEAST64",
                      "INT_FAST8", "INT_FAST16", "INT_FAST32", "INT_FAST64")
# Unsigned maxima the compiler does not predefine, as the width of the type and the literal suffix
# the value needs. They are emitted as literals rather than as the standard's (MAX * 2 + 1) form,
# because cppcheck's preprocessor evaluates that form with signed arithmetic and gets it wrong: on
# a 64-bit target, `#if 18446744073709551615UL == (9223372036854775807L * 2UL + 1UL)` is false to
# cppcheck and true to the compiler.
LIMIT_MACRO_UNSIGNED = {"UCHAR_MAX": ("bool", ""), "USHRT_MAX": ("short", ""),
                        "UINT_MAX": ("int", "U"), "ULONG_MAX": ("long", "UL"),
                        "ULLONG_MAX": ("long-long", "ULL")}


def limit_macro_fallbacks(macros, sizes, char_bit):
    """Values for the <limits.h> and <stdint.h> macros, which cppcheck does not supply.

    The analysis is not given the C library's headers, so these macros come only from cppcheck's
    std.cfg, which does not define INTPTR_MAX, UINTPTR_MAX, SIZE_MAX, PTRDIFF_MAX or INTMAX_MAX
    (cppcheck trac #11928). MicroPython's py/mpconfig.h selects its integer representation on
    those, so without them it reaches "#error Unexpected MP_INT_MAX value" and the translation unit
    ends: 299 of the unix configuration's 318 units. Giving cppcheck the compiler's header
    directories does not help in the compiler's own search order, because simplecpp ignores
    #include_next (simplecpp#702), so GCC's <stdint.h> wrapper never reaches the C library's
    (planning/results/UP-05/cppcheck-usage-audit-2026-09-30/).

    Each value comes from the compiler's own predefine, so this supplies what the header would have
    said rather than a guess. Definitions are guarded, and a header that does resolve correctly
    redefines them with its own identical value. Remove this when std.cfg defines them; the coverage
    check will report the gap if it is removed too early.
    """
    have = {line.split()[1] for line in macros if line.startswith("#define ")}
    lines, defined = [], set()
    for name, source in sorted(LIMIT_MACRO_SOURCES.items()):
        if source in have:
            lines.append(f"#ifndef {name}\n#define {name} {source}\n#endif")
            defined.add(name)
    for name in LIMIT_MACRO_MINIMA:
        if f"{name}_MAX" in defined:
            lines.append(f"#ifndef {name}_MIN\n#define {name}_MIN (-{name}_MAX - 1)\n#endif")
    for name, (size_key, suffix) in sorted(LIMIT_MACRO_UNSIGNED.items()):
        # UCHAR_MAX is keyed on the bool entry only because both are one byte wide; the width that
        # matters is char_bit times one.
        width = char_bit * (1 if name == "UCHAR_MAX" else sizes[size_key])
        lines.append(f"#ifndef {name}\n#define {name} {(1 << width) - 1}{suffix}\n#endif")
    if "__CHAR_BIT__" in have:
        lines.append("#ifndef CHAR_BIT\n#define CHAR_BIT __CHAR_BIT__\n#endif")
    # <math.h> under GCC defines these from builtins, and py/objfloat.c stops at
    # "#error NAN macro is not defined" without them.
    lines.append('#ifndef NAN\n#define NAN (__builtin_nanf(""))\n#endif')
    lines.append("#ifndef INFINITY\n#define INFINITY (__builtin_inff())\n#endif")
    if "SCHAR_MAX" in defined:
        unsigned_char = "__CHAR_UNSIGNED__" in have
        lines.append("#ifndef CHAR_MAX\n#define CHAR_MAX "
                     + ("UCHAR_MAX" if unsigned_char else "SCHAR_MAX") + "\n#endif")
        lines.append("#ifndef CHAR_MIN\n#define CHAR_MIN "
                     + ("0" if unsigned_char else "SCHAR_MIN") + "\n#endif")
    return lines


def has_queries(paths):
    """Every __has_* query the analysed sources and headers make, with the token each asks about."""
    pattern = re.compile(
        r"(" + "|".join(HAS_FAMILIES) + r")\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
    found = defaultdict(set)
    for path in paths:
        try:
            text = Path(path).read_text(errors="replace")
        except OSError:
            continue
        for family, argument in pattern.findall(text):
            found[family].add(argument)
    return found


def has_shims(cc, flags, queries):
    """Answer each query with the real compiler, as a token pasting macro per family.

    A family the compiler does not provide is left undefined, so that `defined(__has_feature)`
    stays false exactly where it is false for the compiler.
    """
    lines, answered = [], {}
    for family in HAS_FAMILIES:
        probe = f"#ifdef {family}\nCPPCHECK_FAMILY_PRESENT\n#endif\n"
        proc = run([cc, *flags, "-E", "-P", "-x", "c", "-"], input=probe)
        if proc.returncode != 0 or "CPPCHECK_FAMILY_PRESENT" not in proc.stdout:
            continue
        arguments = sorted(queries.get(family, ()))
        prefix = "CPPCHECK_" + family.strip("_").upper() + "_"
        probe = "".join(
            f"#if {family}({argument})\nCPPCHECK_ANSWER {argument} 1\n"
            f"#else\nCPPCHECK_ANSWER {argument} 0\n#endif\n" for argument in arguments)
        values = {}
        if arguments:
            proc = run([cc, *flags, "-E", "-P", "-x", "c", "-"], input=probe)
            if proc.returncode != 0:
                raise DerivationError(
                    f"{cc} could not evaluate {family} queries: {proc.stderr.strip()[:300]}")
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[0] == "CPPCHECK_ANSWER":
                    values[parts[1]] = parts[2]
            missing = set(arguments) - set(values)
            if missing:
                raise DerivationError(
                    f"{cc} did not answer {family} for: {', '.join(sorted(missing))}")
        lines.append(f"/* {family}: a compiler builtin cppcheck's preprocessor does not provide. "
                     f"An unlisted argument expands to an undefined identifier, which evaluates to "
                     f"0, as it does for the compiler. */")
        lines.append(f"#define {family}(x) {prefix}##x")
        for argument in arguments:
            lines.append(f"#define {prefix}{argument} {values[argument]}")
        answered[family] = values
    return lines, answered


def write_inputs(database, out):
    """Group, derive and write. Returns the groups.json manifest."""
    out.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for entry in database:
        groups[(compiler_of(entry), group_key(entry))].append(entry)
    # The queries come from the translation units themselves. A query made only in a header is not
    # answered and evaluates to 0, which is the recorded behaviour of the proving cycle.
    queries = has_queries({str(resolved_file(e)) for e in database})
    manifest = []
    # Largest group first; sorted() is stable, so equal-sized groups keep database order.
    for index, ((cc, flags), entries) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        flags = list(flags)
        macros = predefined_macros(cc, flags)
        sizes = type_sizes(cc, flags)
        char_bit = int(next(l.split()[2] for l in macros if l.startswith("#define __CHAR_BIT__ ")))
        parts = ["\n".join(macros),
                 "\n".join(has_shims(cc, flags, queries)[0]),
                 "\n".join(limit_macro_fallbacks(macros, sizes, char_bit))]
        (out / f"predefines-{index}.h").write_text("\n\n".join(parts) + "\n")
        (out / f"platform-{index}.xml").write_text(platform_xml(cc, flags, macros, sizes))
        (out / f"compile_commands-{index}.json").write_text(json.dumps(entries, indent=1) + "\n")
        manifest.append({"index": index, "compiler": cc, "flags": flags, "entries": len(entries)})
    (out / "groups.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sast-cppcheck-inputs", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", metavar="DB", help="compilation database (compile_commands.json)")
    parser.add_argument("outdir", metavar="OUTDIR", help="directory to write the group inputs into")
    args = parser.parse_args(argv)
    try:
        database = json.loads(Path(args.db).read_text())
    except (OSError, ValueError) as exc:
        print(f"sast-cppcheck-inputs: cannot read {args.db}: {exc}", file=sys.stderr)
        return 2
    out = Path(args.outdir)
    # Stale group files from an earlier, larger grouping would otherwise sit beside groups.json
    # looking like inputs, and a caller globbing for them would analyse a group that no longer
    # exists.
    for stale in ("compile_commands-*.json", "predefines-*.h", "platform-*.xml"):
        for path in out.glob(stale):
            path.unlink()
    try:
        manifest = write_inputs(database, out)
    except DerivationError as exc:
        print(f"sast-cppcheck-inputs: {exc}", file=sys.stderr)
        return 2
    print(f"{len(database)} entries in {len(manifest)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
