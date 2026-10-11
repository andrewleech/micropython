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

"""DFU write and erase confinement test for a board in DFU mode: DNLOAD and erase requests must only reach the secondary data range.

The bootloader must already be in a DFU session (machine.bootloader() in the application).
The tool talks to it with tests/mboot/dfu_raw.py and, between the requests,
hashes every flash area over SWD (the core is halted for the read, about 5 s for 2 MiB):

  dfu_confine.py --layout mboot_layout.json --probe STLINK_SERIAL --target <pyocd target> --stage-only
  dfu_confine.py --layout mboot_layout.json --probe STLINK_SERIAL --target <pyocd target> \
      [--usb-serial UID] [--out result.json]

--stage-only (run before the bootloader is put into DFU) programs marker data into the sectors an
erase must not reach but which are otherwise blank: the spare sector in front of the secondary
data range, the first sector after the secondary slot and the last sector of the device. Without
markers, an erase of a blank sector cannot be seen.

The areas are the ones of the layout JSON (boot, primary, log, seccnt, shadow, fs, the spare sector,
the data range and the trailer of the secondary slot) plus the unassigned rest of the device after
the secondary slot. The data range ends where the secondary trailer begins, so blocks and erases
that address the trailer are refused like any other address outside the region. Each attempt has
an expected outcome:

  refused   the DFU status is an error (errADDRESS, errWRITE, errERASE, errTARGET) or the request
            stalled, and NO flash area changed. A request that fails the range check must have no
            side effect, not even the session begin erase of the spare sector and trailer, so the
            refused attempts run before the first accepted one and the spare sector marker is
            still there
  accepted  the request succeeded; only the data range, the spare sector, the secondary trailer
            (erased by the session begin, never written by the host) and its shadow words may change
  partial   a range erase that starts inside the region and runs past its end: it fails, and only
            the sectors of the data range, the spare sector and the shadow words may have changed

Any change to an area outside the secondary slot (boot, primary, fs, seccnt, the unassigned tail)
is a failure in every case, as is a change to anything in the secondary slot other than the
allowed areas above. The shadow area is hashed too; it only changes when the secondary trailer
sectors were erased by a session begin. The update audit log may change only by appending a record (the
session begin record).
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import dfu_raw  # noqa: E402

# A session begin (the first write or erase of a session on the secondary alt) erases the spare
# sector and the trailer sectors (their shadow words) and appends one record to the update audit log.
ALLOWED_IN_REGION = ("sec_spare", "sec_data", "sec_trailer", "shadow", "log")


def log_append_only(old, new):
    """True if new differs from old only by programming erased (0xFF) bytes, as when a log record is appended."""
    return all(o == 0xFF or o == n for o, n in zip(old, new))


def areas_from_layout(path):
    doc = json.load(open(path))
    dev = doc["devices"][0]
    base = dev["base"]
    a = doc["areas"]
    erase = a["secondary"]["erase"]
    dfu_regions = doc["dfu"]["regions"]
    sec = a["secondary"]
    # The counter and the shadow area exist only on a board that has them.
    out = [
        (name, a[name]["addr"], a[name]["size"])
        for name in ("boot", "primary", "log", "seccnt", "shadow", "fs")
        if name in a
    ]
    out.append(("sec_spare", sec["addr"], erase))
    # The DFU data region ends where the trailer of the secondary slot begins; the trailer is
    # outside the region and only the session begin erases it.
    data = dfu_regions[0]
    out.append(("sec_data", data["addr"], data["size"]))
    end = sec["addr"] + sec["size"]
    out.append(("sec_trailer", data["addr"] + data["size"], end - (data["addr"] + data["size"])))
    out.append(("tail", end, base + dev["size"] - end))
    return doc, out, dfu_regions


class Swd:
    def __init__(self, probe, target):
        from pyocd.core.helpers import ConnectHelper

        self.sess = ConnectHelper.session_with_chosen_probe(
            unique_id=probe,
            target_override=target,
            options={"connect_mode": "attach", "frequency": 8000000},
        )
        self.sess.open()
        self.t = self.sess.target

    def close(self):
        self.sess.close()

    def snap(self, areas):
        """({area: hash}, {area: bytes of the update audit log}) with the core halted for the reads."""
        self.t.halt()
        try:
            out = {}
            raw = {}
            for name, addr, size in areas:
                words = self.t.read_memory_block32(addr, size // 4)
                data = struct.pack("<%dI" % len(words), *words)
                out[name] = hashlib.sha256(data).hexdigest()[:16]
                if name == "log":
                    raw[name] = data
        finally:
            self.t.resume()
        return out, raw

    def read(self, addr, n):
        self.t.halt()
        try:
            words = self.t.read_memory_block32(addr, n // 4)
        finally:
            self.t.resume()
        return struct.pack("<%dI" % len(words), *words)


def stage_markers(args, doc, regions):
    """Program marker data into blank sectors outside the secondary data range (device not in DFU)."""
    import tempfile

    from pyocd.core.helpers import ConnectHelper
    from pyocd.flash.file_programmer import FileProgrammer

    erase = doc["areas"]["secondary"]["erase"]
    base = doc["devices"][0]["base"]
    size = doc["devices"][0]["size"]
    sec = doc["areas"]["secondary"]
    targets = [
        regions[0]["addr"] - erase,
        sec["addr"] + sec["size"],
        base + size - erase,
    ]
    sess = ConnectHelper.session_with_chosen_probe(
        unique_id=args.probe, target_override=args.target, options={"connect_mode": "attach"}
    )
    sess.open()
    try:
        for addr in targets:
            data = bytes(
                (0xA5 ^ (i * 7) ^ (addr >> 13)) & 0xFE for i in range(erase)
            )  # never 0xFF
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                f.write(data)
                path = f.name
            FileProgrammer(sess, chip_erase="sector").program(
                path, base_address=addr, file_format="bin"
            )
            os.unlink(path)
            print("marker at 0x%08x (%d bytes)" % (addr, erase))
    finally:
        sess.close()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layout", required=True)
    ap.add_argument("--probe", required=True, help="ST-LINK serial (always given explicitly)")
    ap.add_argument("--target", required=True, help="pyocd target name")
    ap.add_argument("--usb-serial", default=None, help="iSerial of the DFU device")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--only", default=None, help="comma separated attempt name prefixes (dnload, erase)"
    )
    ap.add_argument(
        "--stage-only", action="store_true", help="program the marker sectors and exit"
    )
    args = ap.parse_args()

    doc, areas, regions = areas_from_layout(args.layout)
    if args.stage_only:
        stage_markers(args, doc, regions)
        return 0
    sec_addr = regions[0]["addr"]
    sec_size = regions[0]["size"]
    xfer = 2048
    nblk = sec_size // xfer
    log_addr = doc["areas"]["log"]["addr"]
    base = doc["devices"][0]["base"]
    a = doc["areas"]
    erase = a["secondary"]["erase"]
    sec_end = sec_addr + sec_size  # end of the data region = start of the secondary trailer
    slot_end = a["secondary"]["addr"] + a["secondary"]["size"]
    per_sector = erase // xfer

    swd = Swd(args.probe, args.target)
    dfu = dfu_raw.DfuRaw(serial=args.usb_serial, wait_s=10)
    results = []
    failures = []

    def sector_is_blank(addr):
        return all(b == 0xFF for b in swd.read(addr, erase))

    def attempt(name, expect, fn, alt=0, pre=None, check=None, idle=True):
        """pre() runs before the first snapshot (staging through DFU); check() returns a list of failures.

        idle=False skips the DFU_ABORT that returns the device to dfuIDLE. A vendor erase does not need it,
        and the abort would end the session, so the erase would start a new one and erase the trailer sectors.
        """
        if args.only and not any(name.startswith(p) for p in args.only.split(",")):
            return
        if pre:
            pre()
        before, before_raw = swd.snap(areas)
        if idle:
            try:
                dfu.to_idle()
            except Exception as e:
                print("  (to_idle: %s)" % e)
        if dfu.alt != alt:
            dfu.set_alt(alt)
        try:
            outcome = fn()
        except Exception as e:
            outcome = "exception %s" % e
        try:
            st, _, state, _ = dfu.get_status()
            tail = "%s/%s" % (dfu_raw.status_name(st), dfu_raw.state_name(state))
        except Exception as e:
            tail = "status? %s" % e
        after, after_raw = swd.snap(areas)
        changed = sorted(k for k in before if before[k] != after[k])
        log_ok = "log" not in changed or log_append_only(before_raw["log"], after_raw["log"])
        refused = (
            outcome.startswith("stall")
            or outcome.startswith("err")
            or outcome.startswith("exception")
        )
        ok = True
        why = []
        outside = [c for c in changed if c not in ALLOWED_IN_REGION]
        if outside:
            ok = False
            why.append("changed outside the secondary range: %s" % outside)
        if not log_ok:
            ok = False
            why.append("update audit log changed other than by appending")
        if expect == "refused":
            if not refused:
                ok = False
                why.append("request was not refused")
            if changed:
                ok = False
                why.append("refused request changed %s" % changed)
        elif expect == "accepted":
            if refused:
                ok = False
                why.append("request was refused")
        elif expect == "partial":
            if not refused:
                ok = False
                why.append("request did not fail")
        if check:
            extra = check()
            if extra:
                ok = False
                why.extend(extra)
        print(
            "%-44s %-8s %-18s %-22s changed=%s %s"
            % (name, expect, outcome, tail, changed, "PASS" if ok else "FAIL " + "; ".join(why))
        )
        sys.stdout.flush()
        results.append(
            {
                "name": name,
                "expect": expect,
                "outcome": outcome,
                "dfu": tail,
                "changed": changed,
                "pass": ok,
                "why": why,
            }
        )
        if not ok:
            failures.append(name)

    def dn(block, n=xfer, fill=0x5A):
        def f():
            st, state = dfu.dnload(block, bytes([fill]) * n)
            if st is None:
                return "stall"
            return "ok" if st == 0 else "err %s" % dfu_raw.status_name(st)

        return f

    def er(addr, length, alt=0):
        def f():
            r = dfu.erase(addr, length, alt)
            return "ok" if r == "ok" else "stall"

        return f

    def dirty_sectors(idx_list):
        """Write non-blank data into whole sectors (by index in the data range) through DFU."""

        def f():
            dfu.to_idle()
            dfu.abort()  # new session: the touched-sector bitmap is cleared, so each sector is erased before it is written
            if dfu.alt != 0:
                dfu.set_alt(0)
            for idx in idx_list:
                for b in range(per_sector):
                    st, state = dfu.dnload(idx * per_sector + b, bytes([0x5A]) * xfer)
                    if st != 0:
                        raise RuntimeError("staging write failed %s" % dfu_raw.status_name(st))

        return f

    def secondary_sector(idx):
        return sec_addr + idx * erase

    nsect = sec_size // erase
    marker_spare = sec_addr - erase
    marker_tail0 = slot_end
    marker_tail1 = base + doc["devices"][0]["size"] - erase

    def markers_present(spare=True):
        marks = ((marker_spare,) if spare else ()) + (marker_tail0, marker_tail1)
        bad = [hex(m) for m in marks if sector_is_blank(m)]
        return ["marker sector blank (not staged?): %s" % bad] if bad else []

    # ---- refused requests first: no side effect, so no session begin yet and the spare marker stays ----
    attempt(
        "dnload alt0 block %d (first block of the secondary trailer)" % nblk,
        "refused",
        dn(nblk),
        check=markers_present,
    )
    attempt("dnload alt0 block %d" % (nblk + 1), "refused", dn(nblk + 1))
    attempt(
        "dnload alt0 block %d (last block of the secondary trailer)" % (nblk + per_sector - 1),
        "refused",
        dn(nblk + per_sector - 1),
    )
    attempt(
        "dnload alt0 block %d (first past the slot)" % (nblk + per_sector),
        "refused",
        dn(nblk + per_sector),
    )
    attempt("dnload alt0 block 65535", "refused", dn(65535))
    attempt("dnload alt0 block 0x8000", "refused", dn(0x8000))

    def blk(addr):
        return (addr - log_addr) // xfer

    other = [name for name in ("seccnt", "shadow") if name in a]
    attempt("dnload alt1 block 0 (log)", "refused", dn(0), alt=1)
    for name in other:
        attempt(
            "dnload alt1 block %d (%s)" % (blk(a[name]["addr"]), name),
            "refused",
            dn(blk(a[name]["addr"])),
            alt=1,
        )
    attempt(
        "dnload alt1 block %d (fs)" % blk(a["fs"]["addr"]),
        "refused",
        dn(blk(a["fs"]["addr"])),
        alt=1,
    )
    attempt(
        "dnload alt1 block %d (fs end-1)" % (blk(a["fs"]["addr"] + a["fs"]["size"]) - 1),
        "refused",
        dn(blk(a["fs"]["addr"] + a["fs"]["size"]) - 1),
        alt=1,
    )
    attempt("dnload alt1 block 65535", "refused", dn(65535), alt=1)
    attempt("erase alt1 mass erase", "refused", er(0, 0xFFFFFFFF, 1), alt=1)
    attempt("erase alt1 range erase log", "refused", er(log_addr, erase, 1), alt=1)
    for name in other:
        attempt(
            "erase alt1 range erase %s" % name, "refused", er(a[name]["addr"], erase, 1), alt=1
        )
    attempt("erase alt1 range erase fs", "refused", er(a["fs"]["addr"], erase, 1), alt=1)
    targets = [
        ("boot", a["boot"]["addr"]),
        ("boot last sector", a["boot"]["addr"] + a["boot"]["size"] - erase),
        ("primary", a["primary"]["addr"]),
        ("primary last sector", a["primary"]["addr"] + a["primary"]["size"] - erase),
        ("log", a["log"]["addr"]),
    ]
    targets += [(name, a[name]["addr"]) for name in other]
    targets += [
        ("fs", a["fs"]["addr"]),
        ("fs last sector", a["fs"]["addr"] + a["fs"]["size"] - erase),
        ("secondary spare (marker)", marker_spare),
        ("first sector after secondary (marker)", marker_tail0),
        ("device last sector (marker)", marker_tail1),
    ]
    for label, addr in targets:
        attempt("erase alt0 range erase %s" % label, "refused", er(addr, erase))
    attempt("erase alt0 range erase secondary trailer", "refused", er(sec_end, erase))
    if slot_end - erase != sec_end:
        attempt(
            "erase alt0 range erase last sector of the secondary trailer",
            "refused",
            er(slot_end - erase, erase),
        )
    attempt("erase alt0 start before region, runs in", "refused", er(marker_spare, 2 * erase))
    attempt(
        "erase alt0 boot..primary span", "refused", er(a["primary"]["addr"] - erase, 2 * erase)
    )
    attempt(
        "erase alt0 covers primary (len 0x%X)" % a["primary"]["size"],
        "refused",
        er(a["primary"]["addr"], a["primary"]["size"]),
    )
    attempt("erase alt0 wrap addr+len", "refused", er(0xFFFFE000, 0x4000))
    attempt("erase alt0 addr 0xFFFFFFFF len 1", "refused", er(0xFFFFFFFF, 1))
    attempt("erase alt0 addr 0 len 0xFFFFFFFE", "refused", er(0, 0xFFFFFFFE))
    attempt(
        "dnload alt0 markers still intact", "refused", dn(nblk + per_sector), check=markers_present
    )

    # ---- accepted writes (non-blank data written inside the range, sector 0 and last sector) ----
    attempt(
        "dnload alt0 block 0 (0x5A)",
        "accepted",
        dn(0, xfer, 0x5A),
        check=lambda: [] if not sector_is_blank(sec_addr) else ["sector 0 still blank"],
    )
    attempt(
        "dnload alt0 last block %d (0x5A)" % (nblk - 1),
        "accepted",
        dn(nblk - 1, xfer, 0x5A),
        check=lambda: (
            [] if not sector_is_blank(secondary_sector(nsect - 1)) else ["last sector still blank"]
        ),
    )
    attempt(
        "dnload alt0 last block, 16 bytes short",
        "accepted",
        dn(nblk - 1, xfer - 16, 0x5A),
        pre=lambda: (dfu.to_idle(), dfu.abort()),
    )

    # ---- erase inside the range ----
    def blank_expect(blank, dirty):
        def f():
            out = []
            for idx in blank:
                if not sector_is_blank(secondary_sector(idx)):
                    out.append("sector %d not blank" % idx)
            for idx in dirty:
                if sector_is_blank(secondary_sector(idx)):
                    out.append("sector %d erased but should be untouched" % idx)
            out.extend(markers_present(spare=False))  # the session begin erases the spare sector
            return out

        return f

    mid = nsect // 2
    attempt(
        "erase alt0 first sector",
        "accepted",
        er(sec_addr, erase),
        idle=False,
        pre=dirty_sectors([0, 1, 2, mid, nsect - 1]),
        check=blank_expect([0], [1, 2, mid, nsect - 1]),
    )
    attempt(
        "erase alt0 unaligned address inside (2 sectors)",
        "accepted",
        er(secondary_sector(1) + 0x100, erase),
        idle=False,
        check=blank_expect([1, 2], [mid, nsect - 1]),
    )
    attempt(
        "erase alt0 start inside, past end",
        "partial",
        er(sec_end - erase, 3 * erase),
        idle=False,
        check=blank_expect([1, 2, nsect - 1], [mid]),
    )
    attempt(
        "erase alt0 mass erase",
        "accepted",
        er(0, 0xFFFFFFFF),
        idle=False,
        pre=dirty_sectors([0, 1, mid, nsect - 1]),
        check=blank_expect(range(nsect), []),
    )

    swd.close()
    dfu.close()
    n_fail = len(failures)
    print(
        "\nRESULT: %s (%d attempts, %d failed)"
        % ("PASS" if n_fail == 0 else "FAIL", len(results), n_fail)
    )
    if failures:
        print("failed: " + "; ".join(failures))
    if args.out:
        json.dump(results, open(args.out, "w"), indent=1)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
