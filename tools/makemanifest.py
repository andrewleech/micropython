#!/usr/bin/env python3
#
# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2019 Damien P. George
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import hashlib
import json
import sys
import os
import subprocess
import tempfile

# Always use the mpy-cross from this repo.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../mpy-cross"))
import mpy_cross

import manifestfile

VARS = {}


class FreezeError(Exception):
    pass


def system(cmd):
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return 0, output
    except subprocess.CalledProcessError as er:
        return -1, er.output


def get_timestamp(path, default=None):
    try:
        stat = os.stat(path)
        return stat.st_mtime
    except OSError:
        if default is None:
            raise FreezeError("cannot stat {}".format(path))
        return default


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mkdir(filename):
    path = os.path.dirname(filename)
    if not os.path.isdir(path):
        os.makedirs(path)


# Formerly make-frozen.py.
# This generates:
# - MP_FROZEN_STR_NAMES macro
# - mp_frozen_str_sizes
# - mp_frozen_str_content
def generate_frozen_str_content(modules):
    output = [
        b"#include <stdint.h>\n",
        b"#define MP_FROZEN_STR_NAMES \\\n",
    ]

    for _, target_path in modules:
        print("STR", target_path)
        output.append(b'"%s\\0" \\\n' % target_path.encode())
    output.append(b"\n")

    output.append(b"const uint32_t mp_frozen_str_sizes[] = { ")

    for full_path, _ in modules:
        st = os.stat(full_path)
        output.append(b"%d, " % st.st_size)
    output.append(b"0 };\n")

    output.append(b"const char mp_frozen_str_content[] = {\n")
    for full_path, _ in modules:
        with open(full_path, "rb") as f:
            data = f.read()

            # We need to properly escape the script data to create a C string.
            # When C parses hex characters of the form \x00 it keeps parsing the hex
            # data until it encounters a non-hex character.  Thus one must create
            # strings of the form "data\x01" "abc" to properly encode this kind of
            # data.  We could just encode all characters as hex digits but it's nice
            # to be able to read the resulting C code as ASCII when possible.

            data = bytearray(data)  # so Python2 extracts each byte as an integer
            esc_dict = {ord("\n"): b"\\n", ord("\r"): b"\\r", ord('"'): b'\\"', ord("\\"): b"\\\\"}
            output.append(b'"')
            break_str = False
            for c in data:
                try:
                    output.append(esc_dict[c])
                except KeyError:
                    if 32 <= c <= 126:
                        if break_str:
                            output.append(b'" "')
                            break_str = False
                        output.append(chr(c).encode())
                    else:
                        output.append(b"\\x%02x" % c)
                        break_str = True
            output.append(b'\\0"\n')

    output.append(b'"\\0"\n};\n\n')
    return b"".join(output)


def main():
    # Parse arguments
    import argparse

    cmd_parser = argparse.ArgumentParser(
        description="A tool to generate frozen content in MicroPython firmware images."
    )
    cmd_parser.add_argument("-o", "--output", help="output path")
    cmd_parser.add_argument("-b", "--build-dir", help="output path")
    cmd_parser.add_argument(
        "-f", "--mpy-cross-flags", default="", help="flags to pass to mpy-cross"
    )
    cmd_parser.add_argument("-v", "--var", action="append", help="variables to substitute")
    cmd_parser.add_argument("--mpy-tool-flags", default="", help="flags to pass to mpy-tool")
    cmd_parser.add_argument(
        "--list-c-modules", action="store_true", help="list C module paths from manifest and exit"
    )
    cmd_parser.add_argument(
        "--frozen-output", help="write resolved frozen inputs as JSON"
    )
    cmd_parser.add_argument("files", nargs="+", help="input manifest list")
    args = cmd_parser.parse_args()
    if args.list_c_modules and args.frozen_output:
        cmd_parser.error("--frozen-output cannot be used with --list-c-modules")
    if args.frozen_output:
        if not args.output or not args.build_dir:
            cmd_parser.error("--frozen-output requires --output and --build-dir")
        if os.path.realpath(args.frozen_output) == os.path.realpath(args.output):
            cmd_parser.error("--frozen-output must differ from --output")
        if os.path.commonpath((os.path.realpath(args.output), os.path.realpath(args.build_dir))) != os.path.realpath(args.build_dir):
            cmd_parser.error("--output must be inside --build-dir when exporting frozen inputs")

    # Extract variables for substitution.
    for var in args.var:
        name, value = var.split("=", 1)
        if os.path.exists(value):
            value = os.path.abspath(value)
        VARS[name] = value

    if "MPY_DIR" not in VARS or "PORT_DIR" not in VARS:
        print("MPY_DIR and PORT_DIR variables must be specified")
        sys.exit(1)

    # Use a lighter mode when only listing C modules: freeze/package/module
    # become no-ops so the listing doesn't fail on freeze targets that may not
    # exist for every variant of the board (e.g. alif's modules/$(MCU_CORE)).
    mode = manifestfile.MODE_LIST_C_MODULES if args.list_c_modules else manifestfile.MODE_FREEZE
    manifest = manifestfile.ManifestFile(mode, VARS)

    # Include top-level inputs, to generate the manifest
    for input_manifest in args.files:
        try:
            manifest.execute(input_manifest)
        except manifestfile.ManifestFileError as er:
            print(
                'manifest error executing "{}": {}'.format(input_manifest, er.args[0]),
                file=sys.stderr,
            )
            sys.exit(1)

    # If we're just listing C modules, output them and exit
    if args.list_c_modules:
        c_modules = manifest.c_modules()
        if c_modules:
            # Output one path per line to handle paths with spaces
            for module in c_modules:
                print(module)
        sys.exit(0)


    # Get paths to tools
    MPY_CROSS = VARS["MPY_DIR"] + "/mpy-cross/build/mpy-cross"
    if sys.platform == "win32":
        MPY_CROSS += ".exe"
    MPY_CROSS = os.getenv("MICROPY_MPYCROSS", MPY_CROSS)
    MPY_TOOL = VARS["MPY_DIR"] + "/tools/mpy-tool.py"

    # Ensure mpy-cross is built
    if not os.path.exists(MPY_CROSS):
        print("mpy-cross not found at {}, please build it first".format(MPY_CROSS))
        sys.exit(1)

    files = None
    frozen_content = None
    force_generate = False
    if args.frozen_output:
        kinds = {
            manifestfile.KIND_FREEZE_AS_STR: "freeze_as_str",
            manifestfile.KIND_FREEZE_AS_MPY: "freeze_as_mpy",
            manifestfile.KIND_FREEZE_MPY: "freeze_mpy",
        }
        files = [
            {
                "source": os.path.abspath(result.full_path),
                "target": result.target_path,
                "kind": kinds[result.kind],
                "package": result.metadata.require_name,
                "manifest": result.metadata.manifest_path,
                "version": result.metadata.version,
                "license": result.metadata.license,
            }
            for result in manifest.files()
        ]
        frozen_content = {
            "path": os.path.realpath(args.output),
            "build_dir": os.path.realpath(args.build_dir),
        }
        try:
            with open(args.frozen_output, encoding="utf-8") as f:
                previous = json.load(f)
            frozen_content["sha256"] = file_sha256(args.output)
            force_generate = previous != {"frozen_content": frozen_content, "files": files}
        except (OSError, ValueError, UnicodeError):
            force_generate = True

    # Process the manifest
    str_paths = []
    mpy_files = []
    ts_newest = 0
    for result in manifest.files():
        if result.kind == manifestfile.KIND_FREEZE_AS_STR:
            str_paths.append(
                (
                    result.full_path,
                    result.target_path,
                )
            )
            ts_outfile = result.timestamp
        elif result.kind == manifestfile.KIND_FREEZE_AS_MPY:
            outfile = "{}/frozen_mpy/{}.mpy".format(args.build_dir, result.target_path[:-3])
            ts_outfile = get_timestamp(outfile, 0)
            if force_generate or result.timestamp >= ts_outfile:
                print("MPY", result.target_path)
                mkdir(outfile)
                # Add __version__ to the end of the file before compiling.
                with manifestfile.tagged_py_file(result.full_path, result.metadata) as tagged_path:
                    try:
                        mpy_cross.compile(
                            tagged_path,
                            dest=outfile,
                            src_path=result.target_path,
                            opt=result.opt,
                            mpy_cross=MPY_CROSS,
                            extra_args=args.mpy_cross_flags.split(),
                        )
                    except mpy_cross.CrossCompileError as ex:
                        print("error compiling {}:".format(result.target_path))
                        print(ex.args[0])
                        raise SystemExit(1)
                ts_outfile = get_timestamp(outfile)
            mpy_files.append(outfile)
        else:
            assert result.kind == manifestfile.KIND_FREEZE_MPY
            mpy_files.append(result.full_path)
            ts_outfile = result.timestamp
        ts_newest = max(ts_newest, ts_outfile)

    # Check if output file needs generating
    if not force_generate and ts_newest < get_timestamp(args.output, 0):
        if not args.frozen_output:
            return
    else:
        # Freeze paths as strings
        output_str = generate_frozen_str_content(str_paths)

        # Freeze .mpy files
        if mpy_files:
            res, output_mpy = system(
                [
                    sys.executable,
                    MPY_TOOL,
                    "-f",
                    "-q",
                    args.build_dir + "/genhdr/qstrdefs.preprocessed.h",
                ]
                + args.mpy_tool_flags.split()
                + mpy_files
            )
            if res != 0:
                print("error freezing mpy {}:".format(mpy_files))
                print(output_mpy.decode())
                sys.exit(1)
        else:
            output_mpy = (
                b'#include "py/emitglue.h"\n'
                b"extern const qstr_pool_t mp_qstr_const_pool;\n"
                b"const qstr_pool_t mp_qstr_frozen_const_pool = {\n"
                b"    #if MICROPY_QSTR_BYTES_IN_HASH\n"
                b"    (qstr_pool_t*)&mp_qstr_const_pool, MP_QSTRnumber_of, 0, 0, 0, NULL, NULL, {},\n"
                b"    #else\n"
                b"    (qstr_pool_t*)&mp_qstr_const_pool, MP_QSTRnumber_of, 0, 0, 0, NULL, {},\n"
                b"    #endif\n"
                b"};\n"
                b'const char mp_frozen_names[] = { MP_FROZEN_STR_NAMES "\\0"};\n'
                b"const mp_raw_code_t *const mp_frozen_mpy_content[] = {NULL};\n"
            )

        # Generate output
        output = (
            b"//\n// Content for MICROPY_MODULE_FROZEN_STR\n//\n"
            + output_str
            + b"//\n// Content for MICROPY_MODULE_FROZEN_MPY\n//\n"
            + output_mpy
        )
        try:
            with open(args.output, "rb") as f:
                unchanged = f.read() == output
        except FileNotFoundError:
            unchanged = False
        if not unchanged:
            print("GEN", args.output)
            mkdir(args.output)
            with open(args.output, "wb") as f:
                f.write(output)
        else:
            os.utime(args.output, None)

    if args.frozen_output:
        frozen_content["sha256"] = file_sha256(args.output)
        data = {"frozen_content": frozen_content, "files": files}
        directory = os.path.dirname(os.path.abspath(args.frozen_output))
        mkdir(os.path.abspath(args.frozen_output))
        fd, temporary = tempfile.mkstemp(prefix=".frozen-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(temporary, args.frozen_output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


if __name__ == "__main__":
    main()
