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

"""Unit tests of the Python side of the power-cut tooling.

  python3 tests/mboot/host/test_tools.py

Covers phase labelling and trailer geometry against the trailer offsets of the STM32H563 flash
map, the audit-log and DFU result decoders, the fi_sweep.py trace parser, trial selection and the arm / disarm /
settle protocol against a model target. The dry run of fi_sweep.py reads the layout that
tools/mboot_gen.py writes for the NUCLEO-H563ZI (the Makefile of this directory runs it) and
the trace of a 3 sector swap on that board. Regenerate fixtures/swap_3s_h5.fi.log when the flash
map of the board changes:
  make -C tests/mboot/host test SWEEP_ARGS="--scenarios swap --sectors 3 --modes before \
      --write-fi-log fixtures/swap_3s_h5.fi.log"
The C harness itself is exercised by `make test`.
"""

import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tools"))

import fi_log  # noqa: E402
import fi_sweep  # noqa: E402
import layout as layout_mod  # noqa: E402
import mboot_dfu_result  # noqa: E402
import mboot_log  # noqa: E402
import phases  # noqa: E402
import report  # noqa: E402


def h5_layout():
    """STM32H5 flash map: 8 KiB erase units, 16 byte write unit, ECC. 16 sector primary (0x20000
    at 0x10000), 17 sector secondary (0x22000 at 0x100000), 128 sectors max."""
    dev = {
        "id": 0,
        "name": "internal",
        "base": 0x08000000,
        "size": 0x200000,
        "runs": [{"off": 0, "size": 0x200000, "erase": 8192}],
        "write": 16,
        "erased_val": 255,
        "ecc": True,
    }
    return layout_mod.Layout(
        {
            "devices": [dev],
            "areas": {
                "primary": {
                    "id": 1,
                    "dev": 0,
                    "off": 0x10000,
                    "size": 0x20000,
                    "addr": 0x08010000,
                    "erase": 8192,
                },
                "secondary": {
                    "id": 2,
                    "dev": 0,
                    "off": 0x100000,
                    "size": 0x22000,
                    "addr": 0x08100000,
                    "erase": 8192,
                },
            },
            "max_align": 16,
            "max_img_sectors": 128,
            "swap_mode": "offset",
        }
    )


class PhaseTests(unittest.TestCase):
    def test_trailer_geometry_matches_hardware_offsets(self):
        # Offsets counted back from the end of the 0x20000 slot: magic at end-16, swap_info at
        # end-64, swap_size at end-96, status table at end-4192.
        g = h5_layout().area_geometry(0x20000)
        self.assertEqual(g["magic"], 0x1FFF0)
        self.assertEqual(g["image_ok"], 0x1FFE0)
        self.assertEqual(g["copy_done"], 0x1FFD0)
        self.assertEqual(g["swap_info"], 0x1FFC0)
        self.assertEqual(g["unprot_tlv"], 0x1FFB0)
        self.assertEqual(g["swap_size"], 0x1FFA0)
        self.assertEqual(g["status_start"], 0x1EFA0)
        self.assertEqual(g["trailer_sector"], 0x1E000)

    def test_h5_trace_labels(self):
        lay = h5_layout()
        base = 0x10000
        sec = 0x100000
        cases = [
            ({"kind": "erase", "dev": 0, "off": base + 0x1E000, "len": 0x2000}, "P_TRAILER_ERASE"),
            ({"kind": "write", "dev": 0, "off": base + 0x1FFC0, "len": 16}, "P_SWAPINFO"),
            ({"kind": "write", "dev": 0, "off": base + 0x1FFA0, "len": 16}, "P_SWAPSIZE"),
            ({"kind": "write", "dev": 0, "off": base + 0x1FFB0, "len": 16}, "P_UNPROT_TLV"),
            ({"kind": "write", "dev": 0, "off": base + 0x1FFF0, "len": 16}, "P_MAGIC"),
            ({"kind": "write", "dev": 0, "off": base + 0x1FFD0, "len": 16}, "P_COPYDONE"),
            ({"kind": "write", "dev": 0, "off": base + 0x1FFE0, "len": 16}, "P_IMAGEOK"),
            ({"kind": "write", "dev": 0, "off": base + 0x1EFA0, "len": 16}, "P_STATUS"),
            ({"kind": "write", "dev": 0, "off": base + 0x1EFF0, "len": 16}, "P_STATUS"),
            ({"kind": "erase", "dev": 0, "off": sec + 0x2000, "len": 0x2000}, "P_ERASE_SEC"),
            ({"kind": "erase", "dev": 0, "off": base, "len": 0x2000}, "P_ERASE_PRI"),
            ({"kind": "write", "dev": 0, "off": base + 0x400, "len": 1024}, "P_WRITE_PRI"),
            ({"kind": "write", "dev": 0, "off": sec + 0x4000, "len": 1024}, "P_WRITE_SEC"),
            ({"kind": "erase", "dev": 0, "off": sec + 0x20000, "len": 0x2000}, "P_TRAILER_ERASE"),
            ({"kind": "write", "dev": 0, "off": 0x5000, "len": 16}, "P_OTHER"),
        ]
        for op, want in cases:
            self.assertEqual(phases.classify(lay, op), want, op)

    def test_unclassified_operation_fails_the_report(self):
        lay = h5_layout()
        ops = [{"seq": 1, "kind": "write", "dev": 0, "off": 0x5000, "len": 16}]
        trials = [
            {
                "type": "trial",
                "k": 1,
                "mode": "before",
                "tear": 0,
                "outcome": "ok",
                "boots": 1,
                "final": 1,
                "op": ops[0],
            }
        ]
        rep = report.build(lay, [{"type": "trace", "ops": ops}] + trials, ["before"])
        self.assertFalse(rep["pass"])

    def test_class_without_cuts_fails_the_report(self):
        lay = h5_layout()
        ops = [
            {"seq": 1, "kind": "erase", "dev": 0, "off": 0x10000 + 0x1E000, "len": 0x2000},
            {"seq": 2, "kind": "write", "dev": 0, "off": 0x10000 + 0x1FFF0, "len": 16},
        ]
        trials = [
            {
                "type": "trial",
                "k": 1,
                "mode": "before",
                "tear": 0,
                "outcome": "ok",
                "boots": 1,
                "final": 1,
                "op": ops[0],
            }
        ]
        rep = report.build(lay, [{"type": "trace", "ops": ops}] + trials, ["before"])
        self.assertFalse(rep["pass"])
        self.assertTrue(any("no cuts" in c["verdict"] for c in rep["classes"]))


def make_rec(seq, rtype, result=0, source=0, flags=3, ver=(2, 0, 0, 0), detail=0, hash_prefix=0):
    body = struct.pack(
        "<IIBBBBBBHIII",
        mboot_log.REC_MAGIC,
        seq,
        rtype,
        result,
        source,
        flags,
        ver[0],
        ver[1],
        ver[2],
        ver[3],
        detail,
        hash_prefix,
    )
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def spi_layout(dev1_erase=8192):
    """The h5 flash map with the secondary slot in a SPI NOR flash (device 1, not memory mapped)."""
    data = {
        "devices": [
            {
                "id": 0,
                "base": 0x08000000,
                "size": 0x200000,
                "runs": [{"off": 0, "size": 0x200000, "erase": 8192}],
                "write": 16,
                "mapped": 1,
                "ecc": 1,
            },
            {
                "id": 1,
                "base": 0x90000000,
                "size": 0x1000000,
                "runs": [{"off": 0, "size": 0x1000000, "erase": dev1_erase}],
                "write": 16,
                "mapped": 0,
                "ecc": 0,
            },
        ],
        "areas": {
            "primary": {"id": 1, "dev": 0, "off": 0x10000, "size": 0x20000, "erase": 8192},
            "secondary": {"id": 2, "dev": 1, "off": 0, "size": 0x22000, "erase": dev1_erase},
        },
        "max_align": 16,
        "max_img_sectors": 128,
        "swap_mode": "offset",
    }
    return layout_mod.Layout(data)


class SpiSlotTests(unittest.TestCase):
    def test_unmapped_device_is_a_nor_chip_for_host_sweep(self):
        args = spi_layout().host_args()
        devs = [a for i, a in enumerate(args) if args[i - 1] == "--dev"]
        self.assertEqual([d.split(":")[-1] for d in devs], ["0", "1"])

    def test_secondary_on_the_chip_is_labelled_like_one_on_device_0(self):
        lay = spi_layout()
        # The 8 KiB trailer unit of the secondary slot is two erase blocks of the chip.
        for off in (0x20000, 0x21000):
            self.assertEqual(
                phases.classify(lay, {"kind": "erase", "dev": 1, "off": off, "len": 0x1000}),
                "P_TRAILER_ERASE",
            )
        g = lay.area_geometry(0x22000)
        self.assertEqual(
            phases.classify(lay, {"kind": "write", "dev": 1, "off": g["magic"], "len": 16}),
            "P_MAGIC",
        )
        self.assertEqual(
            phases.classify(lay, {"kind": "write", "dev": 1, "off": g["copy_done"], "len": 16}),
            "P_COPYDONE",
        )

    def test_slot_devices_need_the_same_erase_unit(self):
        with self.assertRaises(ValueError):
            spi_layout(dev1_erase=131072)


class LogDecodeTests(unittest.TestCase):
    def test_order_across_units_and_wrap(self):
        unit = 256
        a = make_rec(0xFFFFFFFF, 6) + make_rec(0, 8)  # wraps: 0xFFFFFFFF is older than 0
        a += b"\xff" * (unit - len(a))
        b = make_rec(1, 13) + b"\xff" * (unit - 32)
        recs, consumed, free, notes = mboot_log.decode_dump(b + a, unit)
        self.assertEqual([r["seq"] for r in recs], [0xFFFFFFFF, 0, 1])
        self.assertEqual(
            [r["type_name"] for r in recs], ["SWAP_DONE", "REVERTED", "APP_CONFIRMED"]
        )
        self.assertEqual(recs[2]["unit"], 0)
        self.assertEqual(consumed, [])
        self.assertEqual(free, 2 * (unit // 32) - 3)
        self.assertEqual(notes, [])

    def test_torn_record_is_consumed_not_valid(self):
        good = make_rec(7, 4)
        torn = bytearray(make_rec(8, 5))
        torn[9] ^= 0x10
        data = good + bytes(torn) + b"\xff" * 32
        recs, consumed, free, notes = mboot_log.decode_dump(data)
        self.assertEqual([r["seq"] for r in recs], [7])
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]["offset"], 32)
        self.assertEqual(free, 1)

    def test_partially_programmed_magic_is_consumed(self):
        data = b"\x4d\x42\xff\xff" + b"\xff" * 28
        recs, consumed, free, _ = mboot_log.decode_dump(data)
        self.assertEqual((len(recs), len(consumed), free), (0, 1, 0))

    def test_gap_is_reported(self):
        recs, _, _, notes = mboot_log.decode_dump(make_rec(3, 1) + make_rec(9, 1))
        self.assertEqual(len(recs), 2)
        self.assertEqual(len(notes), 1)

    def test_dfu_result(self):
        r = mboot_log.decode_result(struct.pack("<IHBBII", 5, 4, 1, 3, 0xAB, 0))
        self.assertEqual(
            (r["seq"], r["code_name"], r["source_name"], r["phase"], r["detail"]),
            (5, "ERR_SIG", "dfu", 3, 0xAB),
        )


class DfuResultDecodeTests(unittest.TestCase):
    def test_decodes_result(self):
        r = mboot_dfu_result.decode_result(struct.pack("<IHBBII", 5, 4, 1, 3, 0xAB, 0))
        self.assertEqual(
            (r["seq"], r["code_name"], r["source_name"], r["phase"], r["detail"]),
            (5, "ERR_SIG", "dfu", 3, 0xAB),
        )

    def test_rejects_wrong_length(self):
        with self.assertRaisesRegex(ValueError, "16 bytes"):
            mboot_dfu_result.decode_result(b"\0" * 15)


class TraceTests(unittest.TestCase):
    def test_roundtrip_and_prefix(self):
        ops = [
            {"seq": 1, "kind": "erase", "dev": 0, "off": 0x1E000, "len": 0x2000},
            {"seq": 2, "kind": "write", "dev": 0, "off": 0x1FFC0, "len": 0x10},
        ]
        self.assertEqual(fi_log.parse_trace(fi_log.trace_to_log(ops)), ops)

    def test_only_first_boot(self):
        text = "MPY MCUboot v\nFI 1 e 0 0x0 0x2000\nFI 2 w 0 0x10 0x10\nMPY MCUboot v\nFI 3 w 0 0x20 0x10\n"
        self.assertEqual(len(fi_log.parse_trace(text)), 2)

    def test_numbering_gap_is_an_error(self):
        with self.assertRaises(ValueError):
            fi_log.parse_trace("FI 1 e 0 0x0 0x2000\nFI 3 w 0 0x10 0x10\n")
        with self.assertRaises(ValueError):
            fi_log.parse_trace("no trace here\n")


class SelectionTests(unittest.TestCase):
    def labels(self):
        return (
            ["P_TRAILER_ERASE", "P_SWAPINFO"] + ["P_WRITE_PRI"] * 20 + ["P_STATUS", "P_COPYDONE"]
        )

    def test_body_copies_are_thinned_others_kept(self):
        trials = fi_sweep.select_trials(self.labels(), [1], [0], body_stride=8)
        ks = [k for k, _, _ in trials]
        body = [3 + i for i in range(20)]
        self.assertEqual([k for k in ks if k in body], [3, 11, 19, 22])
        for k in (1, 2, 23, 24):
            self.assertIn(k, ks)

    def test_full_keeps_every_operation(self):
        trials = fi_sweep.select_trials(self.labels(), [0, 1], [0], full=True)
        self.assertEqual(len(trials), 2 * 24)

    def test_during_mode_repeats_per_delay(self):
        trials = fi_sweep.select_trials(self.labels(), [2], [20, 40, 60], full=True)
        self.assertEqual(len(trials), 3 * 24)
        self.assertEqual({d for _, _, d in trials}, {20, 40, 60})

    def test_fi_word_layout(self):
        self.assertEqual(
            fi_sweep.fi_word(7, 2, 40, trace=True, counter=3),
            [0x4A4E4946, 7, 3, 2 | 0x80 | (40 << 8)],
        )


class DryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(build.cleanup)
        out = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-s",
                "-C",
                HERE,
                "layout",
                "BOARD=h5",
                "BUILD=" + build.name,
            ],
            check=True,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        cls.layout_json = out.stdout.strip()

    def run_dry(self, *extra):
        import contextlib
        import io

        buf = io.StringIO()
        argv = [
            "--layout",
            self.layout_json,
            "--log",
            os.path.join(HERE, "fixtures", "swap_3s_h5.fi.log"),
            "--dry-run",
            "--fi-addr",
            "0x2009dff0",
            "--probe",
            "TESTPROBE0001",
            "--target",
            "testtarget",
        ] + list(extra)
        with contextlib.redirect_stdout(buf):
            self.assertEqual(fi_sweep.main(argv), 0)
        return buf.getvalue().splitlines()

    def test_sample_log_builds_the_command_list(self):
        lines = self.run_dry("--modes", "0,1,2")
        self.assertIn("67 operations", lines[0])
        self.assertIn("P_WRITE_PRI 24", lines[0])
        self.assertIn("130 trials", lines[1])
        trials = [i for i, l in enumerate(lines) if l.startswith("# trial")]
        self.assertEqual(len(trials), 130)
        first = lines[trials[0] : trials[1]]
        self.assertTrue(first[0].startswith("# trial k=1 class=P_TRAILER_ERASE mode=before"))
        # The probe is always addressed by serial number, the arm command carries the FI word.
        self.assertTrue(all("-u TESTPROBE0001" in c for c in first if c.startswith("pyocd")))
        self.assertTrue(
            any(
                "write32 0x2009dff0 0x4a4e4946 0x00000001 0x00000000 0x00000000" in c
                for c in first
            )
        )

    def test_erase_ranges_leave_out_the_bootloader_and_the_filesystem(self):
        lay = layout_mod.Layout.load(self.layout_json)
        want = sorted(
            "0x%x-0x%x" % (a["addr"], a["addr"] + a["size"])
            for name, a in lay.areas.items()
            if name not in ("boot", "fs")
        )
        erases = [l for l in self.run_dry("--modes", "0") if l.startswith("pyocd erase")]
        self.assertTrue(erases)
        for line in erases:
            parts = line.split()
            got = sorted(parts[i + 1] for i, p in enumerate(parts) if p == "-s")
            self.assertEqual(got, want)


class ModelTarget(fi_sweep.Target):
    """Boot model: pending swap, new image unconfirmed, reverted old image. A cut leaves the swap half done."""

    SWAP_OPS = 10
    REVERT_OPS = 6

    def __init__(self, halt_at=None):
        self.halt_at = halt_at
        self.words = [0, 0, 0, 0]
        self.state = "pending"
        self.log = ""
        self.programs = 0

    def program(self, lay, old_image, new_image):
        self.state = "pending"
        self.programs += 1

    def reset_halt(self):
        pass

    def write_fi(self, words):
        self.words = list(words)

    def read_fi(self):
        return list(self.words)

    def _boot(self, cut_at):
        self.log += "MPY MCUboot v1\n"
        if self.state == "halted":
            self.log += "ASSERT swap_offset.c:402\n"
            return False
        if self.state == "pending":
            if cut_at and cut_at <= self.SWAP_OPS:
                self.words[2] += cut_at
                self.state = "partial_halt" if cut_at == self.halt_at else "partial"
                return True
            self.words[2] += self.SWAP_OPS
            self.state = "new"
            self.log += "[APP] v2.0.0\n"
        elif self.state == "partial":
            self.words[2] += self.SWAP_OPS // 2
            self.state = "new"
            self.log += "[APP] v2.0.0\n"
        elif self.state == "partial_halt":
            self.state = "halted"
            self.log += "ASSERT swap_offset.c:402\n"
        elif self.state == "new":
            self.words[2] += self.REVERT_OPS
            self.state = "old"
            self.log += "[APP] v1.0.0\n"
        else:
            self.log += "[APP] v1.0.0\n"
        return False

    def run(self):
        target = self.words[1]
        cut = self._boot(target if target else 0)
        if cut:
            # the device resets itself and boots again
            self._boot(0)

    def reset(self):
        self._boot(0)

    def wait_counter(self, k, timeout):
        return self.words[2] >= k

    def wait_idle(self, quiet_s, timeout):
        return True

    def drain(self):
        text, self.log = self.log, ""
        return text

    def read_memory(self, addr, n):
        return b""


class Args:
    scenario = "swap"
    quiet_s = 0.0
    timeout = 1.0
    max_boots = 6
    old_major = 1
    new_major = 2
    verify_flash = False
    old_image = "v1.bin"
    new_image = "v2.bin"


class ProtocolTests(unittest.TestCase):
    def run_one(self, tgt, k):
        return fi_sweep.run_trial(tgt, h5_layout(), Args, k, 0, 0, {}, fi_sweep.DEFAULT_PATTERNS)

    def test_cut_resumes_and_reverts(self):
        tgt = ModelTarget()
        res = self.run_one(tgt, 4)
        self.assertTrue(res["cut"])
        self.assertEqual(res["outcome"], "ok")
        self.assertEqual((res["final"], res["saw_new"]), (1, True))
        self.assertEqual(tgt.programs, 1)

    def test_assert_in_log_is_a_halt(self):
        res = self.run_one(ModelTarget(halt_at=3), 3)
        self.assertEqual(res["outcome"], "halt")

    def test_target_beyond_the_trace_is_no_cut(self):
        res = self.run_one(ModelTarget(), 99)
        self.assertFalse(res["cut"])
        self.assertEqual(res["outcome"], "no_cut")

    def test_new_image_that_stays_is_a_lost_revert(self):
        class Stuck(ModelTarget):
            def _boot(self, cut_at):
                if self.state == "new":
                    self.log += "MPY MCUboot v1\n[APP] v2.0.0\n"
                    return False
                return super()._boot(cut_at)

        res = self.run_one(Stuck(), 2)
        self.assertEqual(res["outcome"], "lost_revert")


class DfuResultDecodeTests(unittest.TestCase):
    def test_decodes_result(self):
        r = mboot_dfu_result.decode_result(struct.pack("<IHBBII", 5, 4, 1, 3, 0xAB, 0))
        self.assertEqual(
            (r["seq"], r["code_name"], r["source_name"], r["phase"], r["detail"]),
            (5, "ERR_SIG", "dfu", 3, 0xAB),
        )

    def test_rejects_wrong_length(self):
        with self.assertRaisesRegex(ValueError, "16 bytes"):
            mboot_dfu_result.decode_result(b"\0" * 15)


if __name__ == "__main__":
    unittest.main()
