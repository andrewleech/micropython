#!/usr/bin/env python3
# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2026 Andrew Leech
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

"""Run the MCUboot core simulator tests.

The simulator in lib/mcuboot/sim builds bootutil with its own Cargo feature set and its own flash
map. It fixes MCUBOOT_MAX_IMG_SECTORS=128 and does not read shared/mcuboot or mcuboot_layout.h. So
it checks the bootutil core under a feature set equivalent to the production configuration; the
glue code is covered by tests/mcuboot/host. Only the rt configuration of tests/mcuboot/host/boards
(433 sectors) exceeds the simulator limit, and logical-sectors-4k is the nearest equivalent there.

Three separate invocations, because the feature sets differ:
  1. swap-offset sig-ecdsa max-align-16 logical-sectors-4k, core revert/permanent/status-write tests
  2. overwrite-only sig-ecdsa max-align-16 logical-sectors-4k downgrade-prevention, filter downgrade_prevention
     (build.rs rejects downgrade-prevention together with swap-offset, "Downgrade prevention
     requires overwrite only", so this run does not exercise the swap-offset version compare)
  3. the same plus hw-rollback-protection, filters hw_prot_missing_security_cnt hw_prot_failed_security_cnt_check

Build output goes to --target-dir (outside lib/mcuboot) and --locked keeps Cargo.lock unchanged, so
the submodule working tree is not modified. The sim needs lib/mcuboot/ext/mbedtls-3.6.0
(asn1parse.c, platform_util.c); this script does not initialise submodules. Exit status: 0 all
passed, 1 test failure, 3 prerequisite missing.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.abspath(os.path.join(HERE, "..", ".."))
SIM_DIR = os.path.join(TOP, "lib", "mcuboot", "sim")

BASE_FEATURES = ["swap-offset", "sig-ecdsa", "max-align-16", "logical-sectors-4k"]

RUNS = (
    (
        "core",
        BASE_FEATURES,
        [
            "basic_revert",
            "revert_with_fails",
            "perm_with_fails",
            "perm_with_random_fails",
            "bad_secondary_slot",
            "secondary_trailer_leftover",
            "status_write_fails_complete",
            "status_write_fails_with_reset",
            "oversized_secondary_slot",
        ],
    ),
    (
        "downgrade-prevention",
        [
            "overwrite-only",
            "sig-ecdsa",
            "max-align-16",
            "logical-sectors-4k",
            "downgrade-prevention",
        ],
        ["downgrade_prevention"],
    ),
    (
        "hw-rollback-protection",
        BASE_FEATURES + ["hw-rollback-protection"],
        ["hw_prot_missing_security_cnt", "hw_prot_failed_security_cnt_check"],
    ),
)

TEST_LINE = re.compile(r"^test (\S+) \.\.\. (\w+)")


def check_prerequisites():
    missing = []
    if shutil.which("cargo") is None:
        missing.append("cargo is not on PATH")
    for f in ("asn1parse.c", "platform_util.c"):
        if not os.path.exists(
            os.path.join(TOP, "lib", "mcuboot", "ext", "mbedtls-3.6.0", "library", f)
        ):
            missing.append(
                "lib/mcuboot/ext/mbedtls-3.6.0/library/%s missing: run "
                "'git -C lib/mcuboot submodule update --init ext/mbedtls-3.6.0'" % f
            )
    return missing


def run_one(name, features, filters, target_dir, offline, verbose):
    cmd = ["cargo", "test", "--locked"]
    if offline:
        cmd.append("--offline")
    cmd += ["--features", " ".join(features), "--"] + filters
    env = dict(os.environ, CARGO_TARGET_DIR=target_dir)
    print("== %s: %s" % (name, " ".join(cmd)))
    proc = subprocess.run(
        cmd, cwd=SIM_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    results = {}
    for line in proc.stdout.splitlines():
        m = TEST_LINE.match(line)
        if m:
            results[m.group(1)] = m.group(2)
    if verbose or (proc.returncode != 0 and not results):
        print(proc.stdout)
    for t in filters:
        print("  %-36s %s" % (t, results.get(t, "NOT RUN")))
    ok = proc.returncode == 0 and all(results.get(t) == "ok" for t in filters)
    print("  %s" % ("PASS" if ok else "FAIL"))
    return ok


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--target-dir",
        default=os.path.join(HERE, "build", "sim-target"),
        help="cargo target directory (default tests/mcuboot/build/sim-target)",
    )
    ap.add_argument("--offline", action="store_true", help="pass --offline to cargo")
    ap.add_argument(
        "--only",
        help="comma separated run names: core, downgrade-prevention, hw-rollback-protection",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="print the cargo output")
    args = ap.parse_args()

    missing = check_prerequisites()
    if missing:
        for m in missing:
            print("missing: " + m, file=sys.stderr)
        return 3
    wanted = set(args.only.split(",")) if args.only else None
    ok = True
    for name, features, filters in RUNS:
        if wanted is not None and name not in wanted:
            continue
        ok &= run_one(
            name, features, filters, os.path.abspath(args.target_dir), args.offline, args.verbose
        )
    print("sim RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
