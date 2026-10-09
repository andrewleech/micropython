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

"""Hardware power-cut sweep with deterministic "reset at operation N".

The bootloader and the app are built with MBOOT_TEST_FI=1. The 16 bytes at
mboot_req_start + 0x3F0 (the linker symbol of the request region) hold
{ magic 0x4A4E4946, target, counter, mode }. The backend increments counter before every
flash write and erase and the port hook resets the device when counter == target:

    mode word:  bits 0-1  0 reset before the operation, 1 after it, 2 during it
                bit 7     trace: print one "FI <n> <w|e> <dev> 0x<off> 0x<len>" line per operation
                bits 8-31 delay in microseconds between starting the controller command and the
                          reset (mode 2 only)

One trial: erase and program the slots, arm the word over SWD, run until the counter reaches the
target and the device resets itself, disarm (target 0), let the boot finish, then reset repeatedly
until a boot performs no flash operation. The serial log and flash dumps give the outcome.

Every flash operation of the cuttable boot is labelled by host/phases.py. Trials are chosen per
class: every operation for trailer, status and erase operations, every Nth for body copies. The
table from host/report.py is printed at the end.

  fi_sweep.py --layout mboot_layout.json --elf firmware.elf --scenario swap \
      --probe <ST-LINK serial> --target <pyocd target> --tty /dev/serial/by-id/... \
      --old-image v1.bin --new-image v2.bin

--elf is the bootloader ELF, whose symbol table gives mboot_req_start (arm-none-eabi-nm, or the
program named by the environment variable NM). --fi-addr gives the address of the state directly.

--dry-run parses a first-run log (--log), labels the operations, selects the trials and prints the
pyocd command lines a run would issue, without touching any device. Hardware runs always address
the probe by serial number.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "host"))

import fi_log  # noqa: E402
import layout as layout_mod  # noqa: E402
import phases  # noqa: E402
import report  # noqa: E402
import sweep as host_sweep  # noqa: E402

FI_MAGIC = 0x4A4E4946
FI_OFFSET = 0x3F0
REQ_SYMBOL = "mboot_req_start"
FI_TRACE = 0x80
MODE_NAMES = {0: "before", 1: "after", 2: "tear"}
BODY_CLASSES = ("P_WRITE_PRI", "P_WRITE_SEC")
# Areas the sweep erases before each trial: only areas of the flash map, never the boot area.
ERASE_AREAS = ("primary", "secondary", "shadow", "log", "seccnt")

DEFAULT_PATTERNS = {
    "banner": r"MPY MCUboot",
    "app": r"\[APP\] v(\d+)\.(\d+)\.(\d+)",
    "halt": r"ASSERT|assert|HardFault|HARDFAULT|Hard fault|NMI stall",
    "no_image": r"no bootable image",
}


# ---- fault injection word ----


def fi_word(target, mode=0, delay_us=0, trace=False, counter=0):
    """The four 32-bit words of the fault injection state."""
    if not 0 <= delay_us < (1 << 24):
        raise ValueError("delay out of range")
    return [FI_MAGIC, target, counter, (mode & 3) | (FI_TRACE if trace else 0) | (delay_us << 8)]


def req_start(elf):
    """Address of the request region: the linker symbol mboot_req_start of an ELF."""
    nm = os.environ.get("NM", "arm-none-eabi-nm")
    try:
        proc = subprocess.run(
            [nm, elf],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot read the symbols of %s with %s: %s" % (elf, nm, exc))
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == REQ_SYMBOL:
            return int(fields[0], 16)
    raise ValueError("%s has no symbol %s" % (elf, REQ_SYMBOL))


def add_fi_arguments(ap):
    """The two ways to name the fault injection state: --elf or --fi-addr."""
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--elf",
        help="ELF of the bootloader (built with MBOOT_TEST_FI=1); the fault injection state "
        "is at its symbol %s + 0x%X" % (REQ_SYMBOL, FI_OFFSET),
    )
    group.add_argument(
        "--fi-addr", type=lambda s: int(s, 0), help="address of the fault injection state"
    )


def resolve_fi_address(ap, args):
    """Sets args.fi_addr from --elf unless --fi-addr gave it."""
    if args.fi_addr is None:
        try:
            args.fi_addr = req_start(args.elf) + FI_OFFSET
        except ValueError as exc:
            ap.error(str(exc))


# ---- trial selection ----


def select_trials(labels, modes, delays_us, body_stride=8, full=False):
    """List of (k, mode, delay_us). Body copies are thinned to every body_stride-th (first and last kept)."""
    body = [i for i, c in enumerate(labels) if c in BODY_CLASSES]
    if full or body_stride <= 1:
        keep = set(body)
    else:
        keep = set(body[::body_stride])
        if body:
            keep.add(body[0])
            keep.add(body[-1])
    trials = []
    for mode in modes:
        for i, cls in enumerate(labels):
            if cls in BODY_CLASSES and i not in keep:
                continue
            for d in delays_us if mode == 2 else [0]:
                trials.append((i + 1, mode, d))
    return trials


# ---- commands for --dry-run ----


def q(args):
    return " ".join(shlex.quote(str(a)) for a in args)


def pyocd_commands(lay, args, k, mode, delay):
    probe = args.probe or "<ST-LINK serial>"
    base = ["pyocd"]
    sel = ["-u", probe, "-t", args.target]
    cmds = []
    ranges = []
    for name in ERASE_AREAS:
        a = lay.areas.get(name)
        if a:
            ranges.append("0x%x-0x%x" % (a["addr"], a["addr"] + a["size"]))
    cmds.append(q(base + ["erase"] + sel + sum((["-s", r] for r in ranges), [])))
    cmds.append(
        q(
            base
            + ["flash"]
            + sel
            + [
                "--format",
                "bin",
                "--base-address",
                hex(lay.areas["primary"]["addr"]),
                args.old_image,
            ]
        )
    )
    cmds.append(
        q(
            base
            + ["flash"]
            + sel
            + [
                "--format",
                "bin",
                "--base-address",
                hex(lay.areas["secondary"]["addr"] + lay.erase),
                args.new_image,
            ]
        )
    )
    words = fi_word(k, mode, delay)
    fi = args.fi_addr
    if args.scenario == "revert":
        cmds.append(
            "# revert: run one boot without injection first (the swap), then halt at the reset vector again"
        )
    cmds.append(
        q(
            base
            + ["commander"]
            + sel
            + [
                "-c",
                "reset -h",
                "-c",
                "write32 0x%x %s" % (fi, " ".join("0x%08x" % w for w in words)),
                "-c",
                "go",
            ]
        )
    )
    cmds.append(
        "# wait until the counter at 0x%x reaches %d, write target 0, let the boot finish, reset until a boot has no flash operation"
        % (fi + 8, k)
    )
    return cmds


# ---- log analysis ----


def analyze(logs, patterns, new_major):
    """Facts from the captured logs of all boots of one trial."""
    pats = {k: re.compile(v) for k, v in patterns.items()}
    text = "\n".join(logs)
    boots = len(pats["banner"].findall(text))
    versions = [int(m.group(1)) for m in pats["app"].finditer(text)]
    last = logs[-1] if logs else ""
    return {
        "boots": boots,
        "halt": bool(pats["halt"].search(text)),
        "no_image": bool(pats["no_image"].search(last)),
        "final": versions[-1] if versions else 0,
        "saw_new": new_major in versions,
        "last_has_app": bool(pats["app"].search(last)),
    }


def allowed_final(scenario, final, old_major, new_major):
    if scenario in ("swap", "revert"):
        return final == old_major
    return final in (old_major, new_major)


def verdict(scenario, cut, facts, settled, ident, old_major, new_major):
    """Outcome name with the meaning of host_sweep.c."""
    if facts["halt"]:
        return "halt"
    if not cut:
        return "no_cut"
    if facts["no_image"] or ident == "none":
        return "no_image"
    if not settled:
        return "no_converge"
    if not allowed_final(scenario, facts["final"], old_major, new_major):
        return "lost_revert"
    return "ok"


# ---- targets ----


class Target:
    """Device access used by the sweep. Implemented for pyocd; tests supply a model."""

    def program(self, lay, old_image, new_image):
        raise NotImplementedError

    def reset_halt(self):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError

    def write_fi(self, words):
        raise NotImplementedError

    def read_fi(self):
        raise NotImplementedError

    def wait_counter(self, k, timeout):
        raise NotImplementedError

    def wait_idle(self, quiet_s, timeout):
        raise NotImplementedError

    def drain(self):
        raise NotImplementedError

    def read_memory(self, addr, n):
        raise NotImplementedError

    def close(self):
        pass


class PyocdTarget(Target):
    """pyocd over an ST-LINK addressed by serial number; one session per process."""

    def __init__(self, probe, target_name, tty, fi_addr, baud=115200):
        if not probe:
            raise SystemExit(
                "--probe SERIAL is required: probes are always addressed by serial number"
            )
        from pyocd.core.helpers import ConnectHelper

        self.session = ConnectHelper.session_with_chosen_probe(
            unique_id=probe,
            target_override=target_name,
            blocking=False,
            options={
                "connect_mode": "attach",
                "frequency": 4000000,
                "resume_on_disconnect": False,
            },
        )
        if self.session is None:
            raise SystemExit("probe %s not found" % probe)
        self.session.open()
        self.target = self.session.target
        self.fi_addr = fi_addr
        self._cleaned = False
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = None
        if tty:
            import serial

            self._port = serial.Serial(tty, baud, timeout=0.1)
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()

    def _read_loop(self):
        while not self._stop.is_set():
            d = self._port.read(256)
            if d:
                with self._lock:
                    self._buf.extend(d)

    def _size(self):
        with self._lock:
            return len(self._buf)

    def _image_extent(self, path):
        """Bytes from the start of the image to its last byte (the trailer of a hex file is not counted)."""
        if path.lower().endswith(".hex"):
            from intelhex import IntelHex

            start, end = IntelHex(path).segments()[0]
            return end - start
        return os.path.getsize(path)

    def program(self, lay, old_image, new_image):
        from pyocd.flash.eraser import FlashEraser
        from pyocd.flash.file_programmer import FileProgrammer

        self.target.halt()
        if not self._cleaned:
            # First call: whole areas; the device may hold anything.
            ranges = [
                "%#x-%#x" % (lay.areas[n]["addr"], lay.areas[n]["addr"] + lay.areas[n]["size"])
                for n in ERASE_AREAS
                if n in lay.areas
            ]
            self._cleaned = True
        else:
            # Later calls: a trial only touches the first sectors of both slots (the swap with offset
            # uses one more in the secondary), their last two sectors (trailers) and the bookkeeping areas.
            n = (
                -(
                    -max(
                        self._image_extent(old_image),
                        self._image_extent(new_image) if new_image else 0,
                    )
                    // lay.erase
                )
                + 3
            )
            ranges = []
            for name in ("primary", "secondary"):
                a = lay.areas[name]
                ranges.append("%#x-%#x" % (a["addr"], a["addr"] + min(n * lay.erase, a["size"])))
                ranges.append(
                    "%#x-%#x" % (a["addr"] + a["size"] - 2 * lay.erase, a["addr"] + a["size"])
                )
            for name in ERASE_AREAS:
                if name not in ("primary", "secondary") and name in lay.areas:
                    ranges.append(
                        "%#x-%#x"
                        % (
                            lay.areas[name]["addr"],
                            lay.areas[name]["addr"] + lay.areas[name]["size"],
                        )
                    )
        FlashEraser(self.session, FlashEraser.Mode.SECTOR).erase(ranges)
        fp = FileProgrammer(self.session, progress=lambda x: None)
        # A .hex image carries its own addresses (sparse, trailer included); a binary is placed at the slot base.
        for path, base in (
            (old_image, lay.areas["primary"]["addr"]),
            (new_image, lay.areas["secondary"]["addr"] + lay.erase),
        ):
            if not path:
                continue  # no update image: the secondary slot stays erased
            if path.lower().endswith(".hex"):
                fp.program(path, file_format="hex")
            else:
                fp.program(path, base_address=base, file_format="bin")
        self.target.reset_and_halt()

    def reset_halt(self):
        self.target.reset_and_halt()

    def run(self):
        self.target.resume()

    def reset(self):
        self._retry(self.target.reset)

    @staticmethod
    def _retry(fn, tries=50):
        """The debug port answers 'DP wait' or times out while the target resets itself; try again."""
        from pyocd.core import exceptions

        for n in range(tries):
            try:
                return fn()
            except (exceptions.TransferError, exceptions.ProbeError):
                if n == tries - 1:
                    raise
                time.sleep(0.02)

    def write_fi(self, words):
        self._retry(lambda: self.target.write_memory_block32(self.fi_addr, list(words)))

    def read_fi(self):
        return self._retry(lambda: list(self.target.read_memory_block32(self.fi_addr, 4)))

    def wait_counter(self, k, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self.read_fi()[2] >= k:
                return True
            time.sleep(0.02)
        return False

    def wait_idle(self, quiet_s, timeout):
        end = time.time() + timeout
        last_size, last_counter, since = self._size(), self.read_fi()[2], time.time()
        while time.time() < end:
            time.sleep(0.1)
            size, counter = self._size(), self.read_fi()[2]
            if size != last_size or counter != last_counter:
                last_size, last_counter, since = size, counter, time.time()
            elif time.time() - since >= quiet_s:
                return True
        return False

    def drain(self):
        with self._lock:
            text = bytes(self._buf).decode("utf-8", "replace")
            self._buf.clear()
        return text

    def read_memory(self, addr, n):
        # Flash reads over the debug port return zeros while the core sleeps in WFI: halt around them.
        self._retry(self.target.halt)
        try:
            return self._retry(lambda: bytes(self.target.read_memory_block8(addr, n)))
        finally:
            self._retry(self.target.resume)

    def close(self):
        self._stop.set()
        if self._reader:
            self._reader.join()
        self.session.close()


# ---- sweep ----


def ops_in_boot(counter_before, counter_after):
    """Operations of one boot; the counter restarts from 0 if the port clears the word."""
    return counter_after - counter_before if counter_after >= counter_before else counter_after


def identify(tgt, lay, args, images):
    """'old', 'new' or 'none' for the primary slot bytes, or None if not verified."""
    if not args.verify_flash:
        return None
    base = lay.areas["primary"]["addr"]
    for name in ("old", "new"):
        if name in images and tgt.read_memory(base, len(images[name])) == images[name]:
            return name
    return "none"


def prepare(tgt, lay, args):
    tgt.program(lay, args.old_image, args.new_image)
    if args.scenario == "revert":
        # Run the swap boot uncut: the new image is then running unconfirmed.
        tgt.write_fi(fi_word(0))
        tgt.run()
        tgt.wait_idle(args.quiet_s, args.timeout)
        tgt.drain()
        tgt.reset_halt()


def first_run(tgt, lay, args):
    """Boot once with tracing on and return the operations of that boot."""
    prepare(tgt, lay, args)
    tgt.write_fi(fi_word(0, trace=True))
    tgt.run()
    tgt.wait_idle(args.quiet_s, args.timeout)
    return fi_log.parse_trace(tgt.drain())


def run_trial(tgt, lay, args, k, mode, delay, images, patterns):
    prepare(tgt, lay, args)
    tgt.write_fi(fi_word(k, mode, delay))
    tgt.run()
    cut = tgt.wait_counter(k, args.timeout)
    counter = tgt.read_fi()[2]
    tgt.write_fi(fi_word(0, counter=counter))
    tgt.wait_idle(args.quiet_s, args.timeout)
    logs = [tgt.drain()]
    counter = tgt.read_fi()[2]
    settled = False
    for _ in range(args.max_boots):
        before = tgt.read_fi()[2]
        tgt.reset()
        tgt.wait_idle(args.quiet_s, args.timeout)
        log = tgt.drain()
        logs.append(log)
        after = tgt.read_fi()[2]
        if ops_in_boot(before, after) == 0 and re.search(patterns["banner"], log):
            settled = True
            break
    facts = analyze(logs, patterns, args.new_major)
    ident = identify(tgt, lay, args, images)
    outcome = verdict(args.scenario, cut, facts, settled, ident, args.old_major, args.new_major)
    return {
        "cut": cut,
        "boots": facts["boots"],
        "outcome": outcome,
        "final": 1
        if facts["final"] == args.old_major
        else 2
        if facts["final"] == args.new_major
        else 0,
        "saw_new": facts["saw_new"],
        "log": "\n".join(logs),
    }


def trial_record(args, ops, k, mode, delay, res):
    return {
        "type": "trial",
        "scenario": args.scenario,
        "k": k,
        "mode": MODE_NAMES[mode],
        "tear": delay,
        "op": {x: ops[k - 1][x] for x in ("kind", "dev", "off", "len")},
        "cut": res["cut"],
        "boots": res["boots"],
        "outcome": res["outcome"],
        "final": res["final"],
        "saw_new": res["saw_new"],
    }


def sweep(tgt, lay, args, ops, labels, images, patterns, log_dir=None):
    trials = select_trials(labels, args.modes, args.delays_us, args.body_stride, args.full)
    records = [{"type": "trace", "scenario": args.scenario, "ops": ops}]
    for n, (k, mode, delay) in enumerate(trials, 1):
        res = run_trial(tgt, lay, args, k, mode, delay, images, patterns)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            with open(
                os.path.join(log_dir, "%s-k%d-m%d-d%d.log" % (args.scenario, k, mode, delay)), "w"
            ) as f:
                f.write(res["log"])
        records.append(trial_record(args, ops, k, mode, delay, res))
        print(
            "trial %d/%d k=%d %s delay=%d: %s"
            % (n, len(trials), k, MODE_NAMES[mode], delay, res["outcome"]),
            file=sys.stderr,
        )
    return records


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layout", required=True, help="mboot_layout.json of the board")
    ap.add_argument("--scenario", choices=("swap", "revert"), default="swap")
    ap.add_argument("--probe", help="probe serial number")
    ap.add_argument("--target", required=True, help="pyocd target name")
    ap.add_argument("--tty", help="serial device of the bootloader log, /dev/serial/by-id/...")
    ap.add_argument("--old-image", help="signed old image padded for the primary slot (confirmed)")
    ap.add_argument(
        "--new-image",
        help="signed new image padded for the secondary slot (test), placed one erase unit up",
    )
    ap.add_argument("--log", help="log of a first run with trace lines (skips the first run)")
    ap.add_argument("--modes", default="0,1,2", help="fault modes: 0 before, 1 after, 2 during")
    ap.add_argument(
        "--delays-us",
        default="10,12,14",
        help="mode 2 delays in microseconds (10 to 14 tear a 16-byte program on the NUCLEO-H563ZI, see hw/tear_probe.py)",
    )
    ap.add_argument("--body-stride", type=int, default=8, help="every Nth body copy")
    ap.add_argument(
        "--full", action="store_true", help="every operation, including every body copy"
    )
    add_fi_arguments(ap)
    ap.add_argument(
        "--quiet-s",
        type=float,
        default=2.0,
        help="seconds without log output or flash operations that end a boot",
    )
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--max-boots", type=int, default=6)
    ap.add_argument(
        "--verify-flash",
        action="store_true",
        help="compare the primary slot with the images after each trial",
    )
    ap.add_argument("--old-major", type=int, default=1)
    ap.add_argument("--new-major", type=int, default=2)
    ap.add_argument(
        "--no-gate-tear", action="store_true", help="report mode 2 without failing on it"
    )
    ap.add_argument("--out", help="write the trace and trial records as JSON lines")
    ap.add_argument("--log-dir", help="keep the serial log of every trial")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the trials and commands without touching a device",
    )
    for name, default in DEFAULT_PATTERNS.items():
        ap.add_argument(
            "--pattern-" + name.replace("_", "-"),
            default=default,
            help="regex for %s lines" % name,
        )
    args = ap.parse_args(argv)
    resolve_fi_address(ap, args)
    args.modes = [int(x) for x in args.modes.split(",") if x]
    args.delays_us = [int(x) for x in args.delays_us.split(",") if x]
    for m in args.modes:
        if m not in MODE_NAMES:
            ap.error("unknown mode %d" % m)
    if args.dry_run and not args.log:
        ap.error("--dry-run needs --log (the first-run trace gives the operations)")
    if not args.dry_run and not (args.old_image and args.new_image):
        ap.error("--old-image and --new-image are required")
    if args.dry_run and not (args.old_image and args.new_image):
        args.old_image = args.old_image or "<old image>"
        args.new_image = args.new_image or "<new image>"
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    lay = layout_mod.Layout.load(args.layout)
    patterns = {k: getattr(args, "pattern_" + k) for k in DEFAULT_PATTERNS}

    if args.log:
        with open(args.log, errors="replace") as f:
            ops = fi_log.parse_trace(f.read())
        labels = phases.label_trace(lay, ops)
    if args.dry_run:
        trials = select_trials(labels, args.modes, args.delays_us, args.body_stride, args.full)
        counts = {}
        for c in labels:
            counts[c] = counts.get(c, 0) + 1
        print(
            "# %d operations in the cuttable boot (scenario %s): %s"
            % (len(ops), args.scenario, ", ".join("%s %d" % kv for kv in sorted(counts.items())))
        )
        print(
            "# %d trials: modes %s, body stride %s"
            % (
                len(trials),
                ",".join(MODE_NAMES[m] for m in args.modes),
                "1" if args.full else args.body_stride,
            )
        )
        for k, mode, delay in trials:
            print(
                "# trial k=%d class=%s mode=%s delay_us=%d"
                % (k, labels[k - 1], MODE_NAMES[mode], delay)
            )
            for c in pyocd_commands(lay, args, k, mode, delay):
                print(c)
        return 0

    tgt = PyocdTarget(args.probe, args.target, args.tty, args.fi_addr)
    try:
        if not args.log:
            ops = first_run(tgt, lay, args)
            labels = phases.label_trace(lay, ops)
        images = {}
        if args.verify_flash:
            for name, path in (("old", args.old_image), ("new", args.new_image)):
                if path.lower().endswith(".hex"):
                    from intelhex import IntelHex

                    ih = IntelHex(path)
                    start, end = ih.segments()[0]
                    data = ih.tobinstr(start=start, end=end - 1)
                else:
                    with open(path, "rb") as f:
                        data = f.read()
                images[name] = data[: host_sweep.parse_image_total(data)]
        records = sweep(tgt, lay, args, ops, labels, images, patterns, args.log_dir)
    finally:
        tgt.close()
    if args.out:
        with open(args.out, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
    gate = [MODE_NAMES[m] for m in args.modes if m != 2 or not args.no_gate_tear]
    rep = report.build(lay, records, gate_modes=gate)
    print(report.render("scenario %s (hardware), layout %s" % (args.scenario, lay.board), rep))
    print("RESULT: %s" % ("PASS" if rep["pass"] else "FAIL"))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
