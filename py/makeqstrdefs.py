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
import hashlib
import errno  # Import errno for directory creation check


# Extract MP_QSTR_FOO macros.
_MODE_QSTR = "qstr"

# Extract MP_COMPRESSED_ROM_TEXT("") macros.  (Which come from MP_ERROR_TEXT)
_MODE_COMPRESS = "compress"

# Extract MP_REGISTER_(EXTENSIBLE_)MODULE(...) macros.
_MODE_MODULE = "module"

# Extract MP_REGISTER_ROOT_POINTER(...) macros.
_MODE_ROOT_POINTER = "root_pointer"

# Extract MP_REGISTER_DEINIT_FUNCTION(...) macros.
_MODE_DEINIT_FUN = "mp_deinit_funcs"


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
        # Use exist_ok=True for Python 3.2+
        output_dir = os.path.dirname(args.output)
        if output_dir:
            if sys.version_info.major >= 3 and sys.version_info.minor >= 2:
                os.makedirs(output_dir, exist_ok=True)
            else:
                # Manual check for older Python versions
                if not os.path.isdir(output_dir):
                    os.makedirs(output_dir)
    except OSError as e:
        # Only raise error if it wasn't just because the directory already exists
        if e.errno != errno.EEXIST:
            print(f"Error creating directory {output_dir}: {e}", file=sys.stderr)
            sys.exit(1)
    except NameError:
        # Handle case where errno is not defined (older Python versions?)
        if output_dir and not os.path.isdir(output_dir):
            print(
                f"Error creating directory {output_dir}: Directory does not exist and cannot check errno.",
                file=sys.stderr,
            )
            sys.exit(1)

    def pp(flags):
        def run(files):
            try:
                # Ensure flags is a list of strings
                flags_list = flags if isinstance(flags, list) else list(flags)
                files_list = files if isinstance(files, list) else list(files)
                cmd = args.pp + flags_list + files_list
                # print(f"Running preprocessor: {' '.join(cmd)}", file=sys.stderr)
                return subprocess.check_output(cmd)
            except subprocess.CalledProcessError as er:
                # Ensure er.cmd is a list of strings before joining
                cmd_str = (
                    ' '.join(map(str, er.cmd))
                    if hasattr(er, 'cmd') and er.cmd
                    else "Unknown command"
                )
                output_str = (
                    er.output.decode('utf-8', errors='replace')
                    if hasattr(er, 'output') and er.output
                    else "No output"
                )
                print(f"Preprocessor command failed: {cmd_str}", file=sys.stderr)
                print(f"Output: {output_str}", file=sys.stderr)
                raise PreprocessorError(str(er))
            except Exception as e:
                print(f"Error running preprocessor: {e}", file=sys.stderr)
                raise

        return run

    try:
        cpus = multiprocessing.cpu_count()
    except NotImplementedError:
        cpus = 1
    p = multiprocessing.dummy.Pool(cpus)
    with open(args.output, "wb") as out_file:
        for flags, sources in (
            (args.cflags, csources),
            (args.cxxflags, cxxsources),
        ):
            # Only run if there are sources for this type
            if not sources:
                continue
            batch_size = (len(sources) + cpus - 1) // cpus
            chunks = [sources[i : i + batch_size] for i in range(0, len(sources), batch_size or 1)]
            for output in p.imap(pp(flags), chunks):
                out_file.write(output)


def write_out(fname, output):
    if output:
        # Sanitize filename
        sanitized_fname = fname
        for m, r in [("/", "__"), ("\\", "__"), (":", "@"), ("..", "@@")]:
            sanitized_fname = sanitized_fname.replace(m, r)
        # Construct full path
        output_path = os.path.join(args.output_dir, sanitized_fname + "." + args.mode)
        try:
            # Ensure the directory for the split file exists (already created in __main__)
            # os.makedirs(os.path.dirname(output_path), exist_ok=True)
            # Write split files with utf-8 encoding
            with open(output_path, "w", encoding='utf-8') as f:
                f.write("\n".join(output) + "\n")
        except Exception as e:
            print(f"Error writing split file {output_path}: {e}", file=sys.stderr)
            # Optionally re-raise or exit if this is critical


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
        re_match = re.compile(r"MP_REGISTER_DEINIT_FUNCTION\s*\([^)]+\)")
    else:
        print(f"Error: Unknown mode '{args.mode}' in process_file", file=sys.stderr)
        sys.exit(1)

    output = []
    last_fname = None
    for line in f:
        if line.isspace():
            continue
        m = re_line.match(line)
        if m:
            fname = m.group(1)
            # Normalize path separators
            fname = fname.replace("\\", "/")
            # Only process C/C++ source files (heuristic)
            if not is_c_source(fname) and not is_cxx_source(fname):
                # If the line is from a non-source file, don't reset the current output,
                # just skip the line and continue associating matches with the last source file.
                continue
            if fname != last_fname:
                # If we encounter a new source file, write out the collected items for the previous one.
                if last_fname is not None:
                    write_out(last_fname, output)
                output = []
                last_fname = fname
            continue

        # Only collect matches if we are currently associated with a source file.
        if last_fname is not None:
            for match in re_match.findall(line):
                if args.mode == _MODE_QSTR:
                    name = match.replace("MP_QSTR_", "")
                    output.append("Q(" + name + ")")
                elif args.mode in (
                    _MODE_COMPRESS,
                    _MODE_MODULE,
                    _MODE_ROOT_POINTER,
                    _MODE_DEINIT_FUN,
                ):
                    output.append(match)  # Append the full matched string

    # Write out any remaining items for the last processed file.
    if last_fname is not None:
        write_out(last_fname, output)


def cat_together():
    import glob

    hasher = hashlib.md5()
    all_items = []
    # Ensure the pattern correctly finds files in the output directory
    glob_pattern = os.path.join(args.output_dir, "*." + args.mode)
    # print(f"Globbing pattern: {glob_pattern}", file=sys.stderr)
    found_files = glob.glob(glob_pattern)
    # if not found_files:
    #     print(f"Warning: No files found matching {glob_pattern}", file=sys.stderr)

    for fname in found_files:
        try:
            # Read split files as text using utf-8
            with open(fname, "r", encoding='utf-8', errors='ignore') as f:
                items = f.read().splitlines()
                # Filter out empty lines which might result from splitlines
                all_items.extend(item for item in items if item.strip())
        except Exception as e:
            print(f"Error reading file {fname}: {e}", file=sys.stderr)
            continue  # Skip files that can't be read

    # Sort items for consistent hash calculation
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

    # For all modes, just collect the sorted lines into the .collected file.
    # The actual processing/generation is handled by separate scripts (like makeqstrdata.py, make_root_pointers.py, make_deinit_function.py).

    # Encode items to bytes using UTF-8 for hashing and writing to the collected file
    output_bytes = b"\n".join([item.encode('utf-8') for item in all_items])
    # Add a trailing newline for consistency
    if all_items:  # Only add newline if there are items
        output_bytes += b"\n"

    hasher.update(output_bytes)
    new_hash = hasher.hexdigest()

    old_hash = None
    # Ensure output_file is defined before using it
    if not args.output_file:
        print("Error: output_file not specified for cat command.", file=sys.stderr)
        sys.exit(1)
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
            dirname = os.path.dirname(args.output_file)
            if (
                dirname
            ):  # Don't try to create if dirname is empty (e.g., output file in current dir)
                # Use exist_ok=True for Python 3.2+
                if sys.version_info.major >= 3 and sys.version_info.minor >= 2:
                    os.makedirs(dirname, exist_ok=True)
                else:
                    # Manual check for older Python versions
                    if not os.path.isdir(dirname):
                        os.makedirs(dirname)

            # Write the main collected output file as bytes
            with open(args.output_file, "wb") as outf:
                outf.write(output_bytes)
            # Write the hash file as text
            with open(hash_file_path, "w", encoding='utf-8') as f:
                f.write(new_hash)

        except OSError as e:
            # Check if the error is something other than the directory already existing
            if e.errno != errno.EEXIST:
                print(f"Error creating directory or writing output files: {e}", file=sys.stderr)
                sys.exit(1)
        except NameError:
            # Handle case where errno is not defined
            if dirname and not os.path.isdir(dirname):
                print(
                    f"Error creating directory {dirname}: Directory does not exist and cannot check errno.",
                    file=sys.stderr,
                )
                sys.exit(1)
        except Exception as e:
            print(f"Error writing output files: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(mode_full, "not updated")


if __name__ == "__main__":
    # Basic argument count check
    if len(sys.argv) < 5:
        print(
            "usage: %s pp <args...>%s       %s split mode input_filename output_dir _%s       %s cat mode _ output_dir output_file"
            % (sys.argv[0], os.linesep, sys.argv[0], os.linesep, sys.argv[0]),
            file=sys.stderr,
        )
        sys.exit(2)

    class Args:
        pass

    args = Args()
    args.command = sys.argv[1]

    if args.command == "pp":
        # Handle preprocessing command
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
        current_tok = None
        # Iterating through sys.argv correctly for the 'pp' command structure
        arg_iter = iter(sys.argv[2:])
        try:
            while True:
                arg = next(arg_iter)
                if arg in named_args:
                    current_tok = arg
                elif current_tok is not None:
                    named_args[current_tok].append(arg)
                else:
                    # This case might occur if the first arg after 'pp' is not a key
                    print(
                        f"Error: Unexpected argument '{arg}' structure for pp command",
                        file=sys.stderr,
                    )
                    sys.exit(2)
        except StopIteration:
            pass  # End of arguments

        # Validate required arguments for 'pp'
        if not named_args["pp"] or len(named_args["output"]) != 1 or not named_args["sources"]:
            usage_str = "usage: %s pp pp <pp_cmd...> output <out_file> cflags <flags...> cxxflags <flags...> sources <src...> [changed_sources <src...>] [dependencies <dep...>]"
            print(usage_str % sys.argv[0], file=sys.stderr)
            sys.exit(2)

        for k, v in named_args.items():
            setattr(args, k, v)

        # Ensure output is a single string
        args.output = args.output[0]

        try:
            preprocess()
        except PreprocessorError as er:
            print(er, file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # Handle split and cat commands - expecting exactly 6 arguments for split, 6 for cat
    if len(sys.argv) != 6:
        print(
            "usage: %s split mode input_filename output_dir _%s       %s cat mode _ output_dir output_file"
            % (sys.argv[0], os.linesep, sys.argv[0]),
            file=sys.stderr,
        )
        sys.exit(2)

    args.mode = sys.argv[2]
    args.input_filename = None
    args.output_dir = None
    args.output_file = None

    if args.command == "split":
        args.input_filename = sys.argv[3]
        args.output_dir = sys.argv[4]
        # sys.argv[5] is expected to be '_' placeholder
    elif args.command == "cat":
        # sys.argv[3] is expected to be '_' placeholder
        args.output_dir = sys.argv[4]
        args.output_file = sys.argv[5]
    else:
        print(
            f"Error: Unknown command '{args.command}'. Must be 'pp', 'split', or 'cat'.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.mode not in (
        _MODE_QSTR,
        _MODE_COMPRESS,
        _MODE_MODULE,
        _MODE_ROOT_POINTER,
        _MODE_DEINIT_FUN,
    ):
        print("error: mode %s unrecognised" % args.mode, file=sys.stderr)
        sys.exit(2)

    # Ensure output_dir exists for both split and cat
    try:
        if args.output_dir:
            # Use exist_ok=True for Python 3.2+
            if sys.version_info.major >= 3 and sys.version_info.minor >= 2:
                os.makedirs(args.output_dir, exist_ok=True)
            else:
                # Manual check for older Python versions
                if not os.path.isdir(args.output_dir):
                    os.makedirs(args.output_dir)
    except OSError as e:
        # Only raise error if it wasn't just because the directory already exists
        if e.errno != errno.EEXIST:
            print(f"Error creating directory {args.output_dir}: {e}", file=sys.stderr)
            sys.exit(1)
    except NameError:
        # Handle case where errno is not defined
        if args.output_dir and not os.path.isdir(args.output_dir):
            print(
                f"Error creating directory {args.output_dir}: Directory does not exist and cannot check errno.",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.command == "split":
        try:
            # Ensure input file exists for split command
            if not os.path.isfile(args.input_filename):
                print(f"Error: Input file not found: {args.input_filename}", file=sys.stderr)
                sys.exit(1)
            # Read input file with utf-8 encoding
            with io.open(args.input_filename, "r", encoding="utf-8", errors='ignore') as infile:
                process_file(infile)
        except Exception as e:
            print(f"Error during split operation: {e}", file=sys.stderr)
            sys.exit(1)

    if args.command == "cat":
        try:
            # Ensure output file is specified for cat command
            if not args.output_file:
                print("Error: Output file must be specified for cat command", file=sys.stderr)
                sys.exit(1)
            cat_together()
        except Exception as e:
            print(f"Error during cat operation: {e}", file=sys.stderr)
            sys.exit(1)
