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

"""Random power-cut test: drops the supply of a board by switching the USB hub port it hangs on.

The board is powered from its ST-LINK USB connector. The hub port is switched with the USB hub
class requests CLEAR_FEATURE/SET_FEATURE(PORT_POWER), so the supply of the ST-LINK and the
target drops and returns. One trial:

  1. power on, wait for the probe (by serial number), program the old image (primary, confirmed)
     and the new image (secondary, test) with the sweep's programmer, halt at reset;
  2. resume, write SYSRESETREQ over SWD (time zero), wait a random delay, switch the port off;
  3. keep the supply off for --off-s, switch it on, wait for the probe and the log port;
  4. let the interrupted boot finish, then reset until a boot performs no swap, capturing the
     log of every boot, and classify with the verdicts of fi_sweep.py (the first boot after the
     power returns is not captured because the virtual com port does not exist yet).

The hub and port are given as USB bus number, hub port path and port number. The tool refuses to
cut unless the ST-LINK with the given serial number sits on exactly that port.

  powercut.py --layout mcuboot_layout.json --elf firmware.elf --probe <ST-LINK serial> \
      --hub-bus 3 --hub-path 1 --hub-port 2 --tty /dev/serial/by-id/... --old-image old.hex \
      --new-image new.hex --scenario swap --trials 200 --window-ms 20:600 --out powercut.jsonl

--elf is the bootloader ELF built with MCUBOOT_TEST_FI=1 (see fi_sweep.py); --fi-addr can be given instead.
"""

import argparse
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "host"))

import fi_sweep  # noqa: E402
import layout as layout_mod  # noqa: E402

PORT_POWER = 8
CLEAR_FEATURE = 1
SET_FEATURE = 3
STLINK_VID = 0x0483
AIRCR = 0xE000ED0C
SYSRESETREQ = 0x05FA0004


class HubPort:
    def __init__(self, bus, path, port, probe_serial):
        import usb.core

        self.usb = usb
        self.port = port
        self.hub = None
        for dev in usb.core.find(find_all=True):
            if (
                dev.bus == bus
                and tuple(dev.port_numbers or ()) == tuple(path)
                and dev.bDeviceClass == 9
            ):
                self.hub = dev
        if self.hub is None:
            raise SystemExit("no hub at bus %d port path %s" % (bus, path))
        self.probe_serial = probe_serial
        self.bus, self.path = bus, tuple(path)
        self.check_probe()

    def find_probe(self):
        for dev in self.usb.core.find(find_all=True, idVendor=STLINK_VID):
            try:
                if dev.serial_number == self.probe_serial:
                    return dev
            except (self.usb.core.USBError, ValueError):
                continue
        return None

    def check_probe(self):
        dev = self.find_probe()
        if dev is None:
            raise SystemExit("probe %s not found" % self.probe_serial)
        if dev.bus != self.bus or tuple(dev.port_numbers or ()) != self.path + (self.port,):
            raise SystemExit(
                "probe %s is on bus %d port path %s, not behind hub %s port %d; refusing to switch power"
                % (self.probe_serial, dev.bus, dev.port_numbers, self.path, self.port)
            )

    def power(self, on):
        self.hub.ctrl_transfer(
            0x23, SET_FEATURE if on else CLEAR_FEATURE, PORT_POWER, self.port, None
        )

    def wait_probe(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self.find_probe() is not None:
                return True
            time.sleep(0.1)
        return False


def open_target(args, tty_wait=15.0):
    end = time.time() + tty_wait
    while not os.path.exists(args.tty) and time.time() < end:
        time.sleep(0.1)
    last = None
    for _ in range(40):
        try:
            return fi_sweep.PyocdTarget(args.probe, args.target, args.tty, args.fi_addr)
        except Exception as exc:  # the probe is still starting
            last = exc
            time.sleep(0.5)
    raise SystemExit("cannot open the target: %r" % (last,))


def close_quiet(tgt):
    try:
        tgt._stop.set()
        if tgt._reader:
            tgt._reader.join(timeout=1)
    except Exception:
        pass
    try:
        tgt.session.close()
    except Exception:
        pass


def boot_settled(log, patterns):
    return (
        bool(re.search(patterns["banner"], log))
        and bool(re.search(patterns["app"], log))
        and not re.search(r"swap_run|boot_copy_image", log)
    )


def one_trial(args, lay, hub, tgt, images, patterns, delay_s):
    """Programs, arms and cuts; returns the target (reopened) and the record."""
    tgt.program(lay, args.old_image, args.new_image if args.scenario != "boot" else None)
    if args.scenario == "revert":
        tgt.write_fi(fi_sweep.fi_word(0))
        tgt.run()
        tgt.wait_idle(args.quiet_s, args.timeout)
        tgt.drain()
        tgt.reset_halt()
    tgt.run()
    t0 = time.perf_counter()
    tgt.target.write32(AIRCR, SYSRESETREQ)
    while time.perf_counter() - t0 < delay_s - 0.002:
        time.sleep(0.0005)
    while time.perf_counter() - t0 < delay_s:
        pass
    hub.power(False)
    t_cut = time.perf_counter() - t0
    close_quiet(tgt)
    time.sleep(args.off_s)
    hub.power(True)
    if not hub.wait_probe(20):
        return None, {"outcome": "probe_lost", "delay_ms": round(t_cut * 1000, 2)}
    tgt = open_target(args)
    time.sleep(args.settle_s)
    tgt.drain()
    logs = []
    settled = False
    for _ in range(args.max_boots):
        tgt.reset()
        tgt.wait_idle(args.quiet_s, args.timeout)
        log = tgt.drain()
        logs.append(log)
        if boot_settled(log, patterns):
            settled = True
            break
    facts = fi_sweep.analyze(logs, patterns, args.new_major)
    ident = fi_sweep.identify(tgt, lay, args, images)
    outcome = fi_sweep.verdict(
        args.scenario, True, facts, settled, ident, args.old_major, args.new_major
    )
    rec = {
        "type": "powercut",
        "scenario": args.scenario,
        "delay_ms": round(t_cut * 1000, 2),
        "outcome": outcome,
        "boots": facts["boots"],
        "final": 1
        if facts["final"] == args.old_major
        else 2
        if facts["final"] == args.new_major
        else 0,
        "saw_new": facts["saw_new"],
        "ident": ident,
        "settled": settled,
        "log": "\n".join(logs),
    }
    return tgt, rec


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layout", required=True)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--target", required=True, help="pyocd target name")
    ap.add_argument("--tty", required=True)
    ap.add_argument("--hub-bus", type=int, required=True)
    ap.add_argument(
        "--hub-path",
        type=lambda s: [int(x) for x in s.split(".") if x],
        required=True,
        help="port path of the hub, e.g. 1 or 1.4",
    )
    ap.add_argument("--hub-port", type=int, required=True)
    ap.add_argument("--old-image", required=True)
    ap.add_argument("--new-image", help="update image (test state); not used by the boot scenario")
    ap.add_argument(
        "--scenario",
        choices=("swap", "revert", "boot"),
        default="swap",
        help="boot: a plain boot of the confirmed old image, no update in the secondary slot",
    )
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument(
        "--window-ms", default="20:600", help="random cut delay range after the reset, ms"
    )
    ap.add_argument("--off-s", type=float, default=1.5, help="supply off time")
    ap.add_argument("--settle-s", type=float, default=3.0)
    ap.add_argument("--quiet-s", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--max-boots", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    fi_sweep.add_fi_arguments(ap)
    ap.add_argument("--old-major", type=int, default=1)
    ap.add_argument("--new-major", type=int, default=2)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log-dir")
    for name, default in fi_sweep.DEFAULT_PATTERNS.items():
        ap.add_argument("--pattern-" + name.replace("_", "-"), default=default)
    args = ap.parse_args()
    fi_sweep.resolve_fi_address(ap, args)
    args.verify_flash = True
    lo, hi = (float(x) / 1000.0 for x in args.window_ms.split(":"))

    lay = layout_mod.Layout.load(args.layout)
    patterns = {k: getattr(args, "pattern_" + k) for k in fi_sweep.DEFAULT_PATTERNS}
    images = {}
    from intelhex import IntelHex
    import sweep as host_sweep

    if args.scenario != "boot" and not args.new_image:
        ap.error("--new-image is required for the swap and revert scenarios")
    for name, path in (("old", args.old_image), ("new", args.new_image)):
        if not path:
            continue
        ih = IntelHex(path)
        start, end = ih.segments()[0]
        data = ih.tobinstr(start=start, end=end - 1)
        images[name] = data[: host_sweep.parse_image_total(data)]

    hub = HubPort(args.hub_bus, args.hub_path, args.hub_port, args.probe)
    rng = random.Random(args.seed)
    tgt = open_target(args)
    counts = {}
    with open(args.out, "a") as out:
        for n in range(args.trials):
            delay = rng.uniform(lo, hi)
            try:
                tgt, rec = one_trial(args, lay, hub, tgt, images, patterns, delay)
            except Exception as exc:
                rec = {"outcome": "error", "error": repr(exc), "delay_ms": round(delay * 1000, 2)}
                close_quiet(tgt) if tgt else None
                hub.power(True)
                hub.wait_probe(20)
                time.sleep(2)
                tgt = open_target(args)
            if args.log_dir and rec.get("log") is not None:
                os.makedirs(args.log_dir, exist_ok=True)
                with open(
                    os.path.join(args.log_dir, "%s-%04d.log" % (args.scenario, n)), "w"
                ) as f:
                    f.write(
                        "delay_ms %s outcome %s\n%s"
                        % (rec["delay_ms"], rec["outcome"], rec["log"])
                    )
            rec.pop("log", None)
            rec["n"] = n
            out.write(json.dumps(rec) + "\n")
            out.flush()
            counts[rec["outcome"]] = counts.get(rec["outcome"], 0) + 1
            print(
                "trial %d/%d delay %.1f ms: %s"
                % (n + 1, args.trials, rec["delay_ms"], rec["outcome"]),
                file=sys.stderr,
                flush=True,
            )
            if (
                rec["outcome"] in ("probe_lost", "error")
                and counts.get("probe_lost", 0) + counts.get("error", 0) > 5
            ):
                print("too many infrastructure failures, stopping", file=sys.stderr)
                break
    close_quiet(tgt)
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
