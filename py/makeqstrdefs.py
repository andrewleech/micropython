"""
This script processes the output from the C preprocessor and extracts all
qstr. Each qstr is transformed into a qstr definition of the form 'Q(...)'.

This script works with Python 2.6, 2.7, 3.3 and 3.4.
"""

from __future__ import print_function

import io
import os
import re
import subprocess
import sys
import multiprocessing, multiprocessing.dummy


# Extract MP_QSTR_FOO macros.
_MODE_QSTR = "qstr"

# Extract MP_COMPRESSED_ROM_TEXT("") macros.  (Which come from MP_ERROR_TEXT)
_MODE_COMPRESS = "compress"

# Extract MP_REGISTER_(EXTENSIBLE_)MODULE(...) macros.
_MODE_MODULE = "module"

# Extract MP_REGISTER_ROOT_POINTER(...) macros.
_MODE_ROOT_POINTER = "root_pointer"

# Extract MP_REGISTER_DEINIT_FUNCTION(...) macros.
_MODE_DEINIT_FUN = "modue_deinit_function"


class PreprocessorError(Exception):
    pass


def is_c_source(fname):
    return os.path.splitext(fname)[1] in [".c"]


def is_cxx_source(fname):
    return os.path.splitext(fname)[1] in [".cc", ".cp", ".cxx", ".cpp", ".CPP", ".c++", ".C"]


def preprocess():
    if any(src in args.dependencies for src in args.changed_sources):
        sources = args.sources
    elif any(args.changed_sources):
        sources = args.changed_sources
    else:
        sources = args.sources
    csources = []
    cxxsources = []
    for source in sources:
        if is_cxx_source(source):
            cxxsources.append(source)
        elif is_c_source(source):
            csources.append(source)
    try:
        os.makedirs(os.path.dirname(args.output[0]))
    except OSError:
        pass

    def pp(flags):
        def run(files):
            try:
                return subprocess.check_output(args.pp + flags + files)
            except subprocess.CalledProcessError as er:
                raise PreprocessorError(str(er))

        return run

    try:
        cpus = multiprocessing.cpu_count()
    except NotImplementedError:
        cpus = 1
    p = multiprocessing.dummy.Pool(cpus)
    with open(args.output[0], "wb") as out_file:
        for flags, sources in (
            (args.cflags, csources),
            (args.cxxflags, cxxsources),
        ):
            batch_size = (len(sources) + cpus - 1) // cpus
            chunks = [sources[i : i + batch_size] for i in range(0, len(sources), batch_size or 1)]
            for output in p.imap(pp(flags), chunks):
                out_file.write(output)


def write_out(fname, output):
    if output:
        for m, r in [("/", "__"), ("\\", "__"), (":", "@"), ("..", "@@")]:
            fname = fname.replace(m, r)
        with open(args.output_dir + "/" + fname + "." + args.mode, "w") as f:
            f.write("\n".join(output) + "\n")


def process_file(f):
    # match gcc-like output (# n "file") and msvc-like output (#line n "file")
    re_line = re.compile(r"^#(?:line)?\s+\d+\s\"([^\"]+)\"")
    if args.mode == _MODE_QSTR:
        re_match = re.compile(r"MP_QSTR_[_a-zA-Z0-9]+")
    elif args.mode == _MODE_COMPRESS:
        re_match = re.compile(r'MP_COMPRESSED_ROM_TEXT\("([^"]*)"\)')
    elif args.mode == _MODE_MODULE:
        re_match = re.compile(
            r"(?:MP_REGISTER_MODULE|MP_REGISTER_EXTENSIBLE_MODULE|MP_REGISTER_MODULE_DELEGATION)\(.*?,\s*.*?\);"
        )
    elif args.mode == _MODE_ROOT_POINTER:
        re_match = re.compile(r"MP_REGISTER_ROOT_POINTER\(.*?\);")
    elif args.mode == _MODE_DEINIT_FUN:
        # Match the whole macro invocation string during the split phase
        # MP_REGISTER_DEINIT_FUNCTION(name, func) or MP_REGISTER_DEINIT_FUNCTION(name, func, dependency)
        re_match = re.compile(r"MP_REGISTER_DEINIT_FUNCTION\s*\([^)]+\)")
    output = []
    last_fname = None
    for line in f:
        if line.isspace():
            continue
        m = re_line.match(line)
        if m:
            fname = m.group(1)
            if not is_c_source(fname) and not is_cxx_source(fname):
                continue
            if fname != last_fname:
                write_out(last_fname, output)
                output = []
                last_fname = fname
            continue
        for match in re_match.findall(line):
            if args.mode == _MODE_QSTR:
                name = match.replace("MP_QSTR_", "")
                output.append("Q(" + name + ")")
            elif args.mode in (_MODE_COMPRESS, _MODE_MODULE, _MODE_ROOT_POINTER, _MODE_DEINIT_FUN):
                output.append(match)  # Append the full matched string

    if last_fname:
        write_out(last_fname, output)
    return ""


def cat_together():
    import glob
    import hashlib
    import networkx as nx  # Use networkx for topological sort
    import sys

    hasher = hashlib.md5()
    all_items = []
    for fname in glob.glob(args.output_dir + "/*." + args.mode):
        try:
            # Read as text for parsing content, regardless of original source encoding
            with open(fname, "r", encoding='utf-8', errors='ignore') as f:
                items = f.read().splitlines()
                # Filter out empty lines which might result from splitlines
                all_items.extend(item for item in items if item.strip())
        except Exception as e:
            print(f"Error reading file {fname}: {e}", file=sys.stderr)
            continue  # Skip files that can't be read

    all_items.sort()

    mode_full = "QSTR"
    if args.mode == _MODE_COMPRESS:
        mode_full = "Compressed data"
    elif args.mode == _MODE_MODULE:
        mode_full = "Module registrations"
    elif args.mode == _MODE_ROOT_POINTER:
        mode_full = "Root pointer registrations"
    elif args.mode == _MODE_DEINIT_FUN:
        mode_full = "Deinit function registrations"

    # Generate content based on mode
    if args.mode == _MODE_DEINIT_FUN:
        # Parse function and dependency pairs
        # MP_REGISTER_DEINIT_FUNCTION(func) or MP_REGISTER_DEINIT_FUNCTION(func, dependency)
        regex_deinit = re.compile(
            r"MP_REGISTER_DEINIT_FUNCTION\s*\(([^,\s)]+)(?:\s*,\s*([^)]+))?\s*\)"
        )
        funcs = {}
        malformed_lines = []
        for item in all_items:
            # Need to re-evaluate the item here since it's a string from readlines()
            # Check if the item actually contains the macro call.
            if "MP_REGISTER_DEINIT_FUNCTION" in item:
                m = regex_deinit.search(item)  # Use search instead of match
                if m:
                    func, dep = m.groups()
                    # Basic validation for function/dependency names
                    if not func or not func.isidentifier():
                        print(
                            f"Warning: Invalid C identifier '{func}' in registration: {item}",
                            file=sys.stderr,
                        )
                        continue
                    if dep and not dep.isidentifier():
                        print(
                            f"Warning: Invalid C identifier '{dep}' for dependency in registration: {item}",
                            file=sys.stderr,
                        )
                        continue
                    funcs[func] = dep if dep else None  # Store dependency (None if no dependency)
                else:
                    # Only add as malformed if it contains the macro name but didn't parse
                    malformed_lines.append(item)

        if malformed_lines:
            print("Warning: Found malformed MP_REGISTER_DEINIT_FUNCTION lines:", file=sys.stderr)
            for line in malformed_lines:
                print(f"  {line}", file=sys.stderr)

        # Build dependency graph
        G = nx.DiGraph()
        missing_deps = set()
        for func, dep in funcs.items():
            G.add_node(func)
            if dep:
                if dep not in funcs:
                    # Track missing dependencies but don't warn yet
                    missing_deps.add(dep)
                    # Add node for missing dep so graph structure is correct
                    G.add_node(dep)
                G.add_edge(
                    dep, func
                )  # Edge from dependency to function (dep must run before func)

        # Warn about missing dependencies after building the full graph
        if missing_deps:
            print(
                "Warning: The following deinit dependencies were used but not registered:",
                file=sys.stderr,
            )
            for dep in sorted(list(missing_deps)):
                print(f"  - {dep}", file=sys.stderr)
            print(
                "         Ensure these functions are either registered or defined elsewhere.",
                file=sys.stderr,
            )

        # Topological sort
        try:
            sorted_funcs = list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible as e:
            print(f"Error: Circular dependency detected in deinit functions: {e}", file=sys.stderr)
            try:
                # Attempt to find and print the cycle
                cycle = nx.find_cycle(
                    G, source=list(G.nodes)[0] if G.nodes else None
                )  # Provide a source node
                formatted_cycle = (
                    " -> ".join([str(node) for node, _ in cycle]) + f" -> {cycle[0][0]}"
                )
                print(f"       Cycle details: {formatted_cycle}", file=sys.stderr)
            except nx.NetworkXNoCycle:
                print("       Could not pinpoint the exact cycle.", file=sys.stderr)
            except Exception as cycle_err:  # Catch other potential errors during cycle finding
                print(f"       Error finding cycle details: {cycle_err}", file=sys.stderr)
            sys.exit(1)
        except nx.NetworkXError as e:
            print(f"Error during topological sort: {e}", file=sys.stderr)
            sys.exit(1)

        # Generate C code
        generated_code = [
            "// Auto-generated by makeqstrdefs.py - DO NOT EDIT",
            f"// Mode: {args.mode}",
            "",
            "#include \"py/obj.h\"",
            "",
            "// Forward declarations",
        ]

        # Add forward declarations only for functions that are actually registered
        declared_funcs = [f for f in sorted_funcs if f in funcs]

        if not declared_funcs:
            generated_code.append("// No valid registered deinit functions found.")
        else:
            for func in declared_funcs:
                # Ensure valid C identifiers before generating declarations
                if func.isidentifier():
                    generated_code.append(f"void {func}(void);")
                else:
                    # Should have been caught earlier, but double-check
                    print(
                        f"Internal Error: Invalid identifier '{func}' reached declaration stage.",
                        file=sys.stderr,
                    )

        generated_code.append("\nvoid mp_run_deinit_funcs(void) {")

        if not declared_funcs:
            generated_code.append(
                "    // No deinit functions registered or all had missing dependencies/were invalid."
            )
        else:
            # Call functions in topologically sorted order (reversed for deinit)
            # Only call functions that were actually registered (exist in funcs dict)
            # and are valid identifiers
            called_count = 0
            for func in reversed(sorted_funcs):
                if func in funcs and func.isidentifier():
                    generated_code.append(f"    {func}();")
                    called_count += 1
            if called_count == 0:
                generated_code.append("    // No valid deinit functions to call.")

        generated_code.append("}")
        generated_content = "\n".join(generated_code) + "\n"
        # Encode to bytes for hashing and writing
        output_bytes = generated_content.encode('utf-8')

    else:
        # Original logic for other modes - ensure it uses UTF-8 encoding consistently for hashing
        # Join with b'\n' as original code did for byte streams
        output_bytes = b"\n".join([item.encode('utf-8') for item in all_items])

    hasher.update(output_bytes)
    new_hash = hasher.hexdigest()

    old_hash = None
    hash_file_path = args.output_file + ".hash"
    try:
        # Read hash file as text
        with open(hash_file_path, "r", encoding='utf-8') as f:
            old_hash = f.read()
    except IOError:
        pass  # File doesn't exist, that's fine
    except Exception as e:
        print(f"Warning: Could not read hash file {hash_file_path}: {e}", file=sys.stderr)

    if old_hash != new_hash or not os.path.exists(args.output_file):
        print(mode_full, "updated")

        try:
            # Ensure output directory exists
            if args.output_file:
                os.makedirs(os.path.dirname(args.output_file), exist_ok=True)
                # Write the main output file
                with open(args.output_file, "wb") as outf:
                    outf.write(output_bytes)
                # Write the hash file as text
                with open(hash_file_path, "w", encoding='utf-8') as f:
                    f.write(new_hash)
            else:
                # Should not happen if command is 'cat', but handle defensively
                print("Error: Output file not specified for cat command.", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Error writing output files: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(mode_full, "not updated")


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("usage: %s command mode input_filename output_dir output_file" % sys.argv[0])
        sys.exit(2)

    class Args:
        pass

    args = Args()
    args.command = sys.argv[1]

    if args.command == "pp":
        named_args = {
            s: []
            for s in [
                "pp",
                "output",
                "cflags",
                "cxxflags",
                "sources",
                "changed_sources",
                "dependencies",
            ]
        }

        for arg in sys.argv[1:]:
            if arg in named_args:
                current_tok = arg
            else:
                named_args[current_tok].append(arg)

        if not named_args["pp"] or len(named_args["output"]) != 1:
            print("usage: %s %s ..." % (sys.argv[0], " ... ".join(named_args)))
            sys.exit(2)

        for k, v in named_args.items():
            setattr(args, k, v)

        try:
            preprocess()
        except PreprocessorError as er:
            print(er)
            sys.exit(1)

        sys.exit(0)

    args.mode = sys.argv[2]
    args.input_filename = sys.argv[3]  # Unused for command=cat
    args.output_dir = sys.argv[4]
    args.output_file = None if len(sys.argv) == 5 else sys.argv[5]  # Unused for command=split

    if args.mode not in (
        _MODE_QSTR,
        _MODE_COMPRESS,
        _MODE_MODULE,
        _MODE_ROOT_POINTER,
        _MODE_DEINIT_FUN,
    ):
        print("error: mode %s unrecognised" % sys.argv[2])
        sys.exit(2)

    try:
        os.makedirs(args.output_dir)
    except OSError:
        pass

    if args.command == "split":
        with io.open(args.input_filename, encoding="utf-8") as infile:
            process_file(infile)

    if args.command == "cat":
        cat_together()
