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

"""Host power-cut sweep driver.

Builds signed test images with imgtool, runs the host_sweep executable (real
bootutil over tests/mcuboot/host/fake_flash.c) for each scenario, image size
and cut mode, labels every flash operation with phases.py and prints the table
of report.py. Exit status 0 means every gated cut point converged.

  sweep.py --layout build/h5-local/gen/mcuboot_layout.json --binary build/h5-local/host_sweep \\
      --key tests/mcuboot/keys/test-ecdsa-p256.pem

Scenarios: swap, revert, swap_confirm, perm for the swap policy and overwrite-external
(whose update is always permanent), install for the single slot policy (the harness writes the
new image over the only slot, block by block, as a DFU session does). The single slot policy has
no old image to revert to: a cut install ends without a valid image, which is the allowed
outcome (fail closed: the bootloader starts no image that fails validation and ends in the
recovery front end). The report lists how many cut points ended that way. Modes: before, after,
between (a cut inside a multi-unit operation between two units) and tear (a cut
inside one program/erase unit, which leaves a torn unit). The gated modes are
before, after and between; tear is reported but only gated with --gate-tear,
because an unmodified bootutil has no defence against a torn flag word.
"""

import argparse
import hashlib
import json
import os
import random
import shlex
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import fi_log
import layout as layout_mod
import report

HERE = os.path.dirname(os.path.abspath(__file__))
IMGTOOL = os.path.join(HERE, "..", "..", "..", "lib", "mcuboot", "scripts", "imgtool.py")

DEFAULT_SCENARIOS = {
    "swap": ("swap", "revert"),
    "overwrite-external": ("perm",),
    "single": ("install",),
}
DEFAULT_MODES = ("before", "after", "between")
ALL_MODES = ("before", "after", "between", "tear")


def parse_image_total(data):
    """Bytes of header, body and TLV areas of a signed image."""
    _magic, _load, hdr_size, prot, img_size = struct.unpack_from("<IIHHI", data, 0)
    off = hdr_size + img_size + prot
    _tlv_magic, tlv_tot = struct.unpack_from("<HH", data, off)
    return off + tlv_tot


def imgtool(*args):
    cmd = [sys.executable, IMGTOOL] + [str(a) for a in args]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def make_image(lay, key, path, version, n_sectors, seed, trailer_flag, slot_size=None):
    """Write a signed image of about n_sectors erase units (header, body, TLVs) to path.

    trailer_flag is "--confirm" or "--test"; the file is padded to the slot size with the
    trailer magic as imgtool writes it. None writes the image alone (single slot policy).
    slot_size overrides the slot size of the layout arguments.
    """
    rng = random.Random(seed)
    target = n_sectors * lay.erase - 16
    payload_len = target - lay.header_size - 160
    raw = path + ".raw"
    for _ in range(12):
        with open(raw, "wb") as f:
            f.write(bytes(rng.getrandbits(8) for _ in range(payload_len)))
        # The arguments the generator derived for the board (layout id TLV, security counter);
        # the later --version and trailer flag override the generated ones.
        extra = ["--slot-size", hex(slot_size)] if slot_size else []
        extra += [trailer_flag] if trailer_flag else []
        imgtool("sign", *lay.sign_args, "--version", version, "-k", key, *extra, raw, path)
        with open(path, "rb") as f:
            total = parse_image_total(f.read())
        if 0 <= target - total <= 8:
            os.remove(raw)
            return total
        payload_len += target - total - 4
    raise RuntimeError("could not size image to %d bytes" % target)


def image_path(lay, key, work, name, n_sectors):
    """File name that changes with everything the signed image depends on."""
    with open(key, "rb") as f:
        stamp = json.dumps(
            [
                lay.sign_args,
                lay.header_size,
                lay.erase,
                lay.write,
                lay.max_align,
                lay.max_img_sectors,
                lay.areas["primary"]["size"],
                lay.new_slot_size,
                lay.policy,
                lay.swap_mode,
                hashlib.sha1(f.read()).hexdigest(),
            ]
        )
    return os.path.join(
        work, "%s_%ds_%s.bin" % (name, n_sectors, hashlib.sha1(stamp.encode()).hexdigest()[:8])
    )


def build_images(lay, key, work, n_sectors):
    """Returns the paths (old, new_test, new_perm) for an n-sector image pair."""
    os.makedirs(work, exist_ok=True)
    paths = {}
    single = lay.policy == "single"
    for name, ver, flag, seed in (
        ("old", "1.0.0", "--confirm", 1),
        ("new", "2.0.0", "--test", 2),
        ("perm", "2.0.0", "--confirm", 2),
    ):
        p = image_path(lay, key, work, name, n_sectors)
        if not os.path.exists(p):
            # The images of the update slot are padded for that slot, which for swap using move
            # is one erase unit smaller than the primary slot.
            make_image(
                lay,
                key,
                p,
                ver,
                n_sectors,
                seed * 1000 + n_sectors,
                None if single else flag,
                None if name == "old" else lay.new_slot_size,
            )
        paths[name] = p
    return paths


def host_command(binary, lay, scenario, images, modes, tears, out, extra):
    cmd = (
        [binary]
        + lay.host_args()
        + [
            "--scenario",
            scenario,
            "--old-image",
            images["old"],
            "--new-image",
            images["perm"] if scenario == "perm" else images["new"],
            "--modes",
            ",".join(modes),
            "--out",
            out,
        ]
    )
    if tears:
        cmd += ["--tears", ",".join(str(t) for t in tears)]
    return cmd + extra


def run_job(job):
    cmd, out = job["cmd"], job["out"]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            "host_sweep failed (%d): %s\n%s"
            % (proc.returncode, " ".join(shlex.quote(c) for c in cmd), proc.stderr)
        )
    records = []
    with open(out) as f:
        for line in f:
            records.append(json.loads(line))
    job["records"] = records
    job["stderr"] = proc.stderr
    return job


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--layout", required=True, help="mcuboot_layout.json written by tools/mcuboot_gen.py"
    )
    ap.add_argument("--binary", required=True, help="host_sweep executable built for this layout")
    ap.add_argument(
        "--key", required=True, help="signing key whose public half is built into host_sweep"
    )
    ap.add_argument("--work", default="build/work", help="directory for images and result files")
    ap.add_argument("--scenarios", help="default: by slot policy")
    ap.add_argument("--sectors", default="3,15", help="image sizes in erase units")
    ap.add_argument("--modes", default=",".join(DEFAULT_MODES))
    ap.add_argument(
        "--tears",
        default="64,128,192",
        help="tear positions in 1/256 for the between and tear modes",
    )
    ap.add_argument("--stride-body", type=int, default=1, help="run every Nth multi-unit write")
    ap.add_argument("--gate-tear", action="store_true", help="fail on tear mode failures")
    ap.add_argument(
        "--reps",
        type=int,
        default=1,
        help="random bit patterns of a torn unit tried at each cut point of the tear mode",
    )
    ap.add_argument(
        "--nor-scatter",
        action="store_true",
        help="a cut inside a SPI NOR command leaves the whole page or sector undefined",
    )
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument(
        "--dry-run", action="store_true", help="print the host_sweep commands and exit"
    )
    ap.add_argument(
        "--only", help="rerun one trial K:MODE:TEAR[:REP] with bootutil logging on stderr"
    )
    ap.add_argument(
        "--write-fi-log",
        metavar="FILE",
        help="write the operations of the first scenario as FI trace lines "
        "(the log format fi_sweep.py reads)",
    )
    args = ap.parse_args()

    lay = layout_mod.Layout.load(args.layout)
    if not lay.sign_args:
        ap.error("layout has no imgtool.sign_args")
    scenarios = [
        s
        for s in (args.scenarios.split(",") if args.scenarios else DEFAULT_SCENARIOS[lay.policy])
        if s
    ]
    sectors = [int(s) for s in args.sectors.split(",") if s]
    modes = [m for m in args.modes.split(",") if m]
    for m in modes:
        if m not in ALL_MODES:
            ap.error("unknown mode %s" % m)
    tears = [int(t) for t in args.tears.split(",") if t]
    max_sectors = lay.areas["primary"]["size"] // lay.erase
    for n in sectors:
        if n < 1 or n > max_sectors - 1:
            ap.error("image of %d sectors does not fit the primary slot with its trailer" % n)

    key = os.path.abspath(args.key)
    work = os.path.abspath(args.work)
    jobs = []
    for scenario in scenarios:
        for n in sectors:
            images = {k: image_path(lay, key, work, k, n) for k in ("old", "new", "perm")}
            if not args.dry_run:
                images = build_images(lay, key, work, n)
            for mode in modes:
                out = os.path.join(work, "result-%s-%ds-%s.jsonl" % (scenario, n, mode))
                extra = ["--stride-body", str(args.stride_body)]
                if args.reps != 1:
                    extra += ["--reps", str(args.reps)]
                if args.nor_scatter:
                    extra.append("--nor-scatter")
                if args.only:
                    extra += ["--only", args.only, "-v"]
                cmd = host_command(args.binary, lay, scenario, images, [mode], tears, out, extra)
                jobs.append(
                    {"scenario": scenario, "sectors": n, "mode": mode, "cmd": cmd, "out": out}
                )

    if args.dry_run:
        for job in jobs:
            print(" ".join(shlex.quote(c) for c in job["cmd"]))
        return 0

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        done = list(pool.map(run_job, jobs))
    if args.only:
        for job in done:
            sys.stderr.write(job["stderr"])

    if args.write_fi_log:
        trace = next(r for j in done for r in j["records"] if r["type"] == "trace")
        with open(args.write_fi_log, "w") as f:
            f.write(fi_log.trace_to_log(trace["ops"]))
    status = 0
    for scenario in scenarios:
        for n in sectors:
            group = [j for j in done if j["scenario"] == scenario and j["sectors"] == n]
            records = [r for j in group for r in j["records"]]
            title = "scenario %s, %d-sector image, layout %s" % (
                scenario,
                n,
                lay.board or os.path.basename(args.layout),
            )
            rep = report.build(
                lay, records, gate_modes=[m for m in modes if m != "tear" or args.gate_tear]
            )
            print(report.render(title, rep))
            if not rep["pass"]:
                status = 1
    print("RESULT: %s" % ("PASS" if status == 0 else "FAIL"))
    return status


if __name__ == "__main__":
    sys.exit(main())
