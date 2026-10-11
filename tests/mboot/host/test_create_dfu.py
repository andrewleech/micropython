#!/usr/bin/env python3
"""
Tests of tools/create_dfu.py and the DFU files tools/mboot_sign.py writes with it.

The DFU CRC and layout are checked against values computed independently of create_dfu.py, so a
mistake in both the writer and the reader cannot pass.
"""

import os
import struct
import sys
import tempfile
import unittest
import zlib
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tools"))

import create_dfu  # noqa: E402
import mboot_sign  # noqa: E402


class SuffixTests(unittest.TestCase):
    def test_plain_file_is_payload_plus_suffix_with_uninverted_crc(self):
        data = create_dfu.DfuFile.plain(b"firmware", 0x1234, 0x5678, 0x0102).to_bytes()
        body, suffix = data[:-16], data[-16:]
        self.assertEqual(body, b"firmware")
        self.assertEqual(
            suffix[:12], struct.pack("<4H3sB", 0x0102, 0x5678, 0x1234, 0x0100, b"UFD", 16)
        )
        self.assertEqual(struct.unpack("<I", suffix[12:])[0], ~zlib.crc32(data[:-4]) & 0xFFFFFFFF)

    def test_corrupt_file_is_rejected(self):
        data = bytearray(create_dfu.DfuFile.plain(b"firmware").to_bytes())
        data[0] ^= 1
        with self.assertRaisesRegex(create_dfu.DfuError, "CRC"):
            create_dfu.DfuFile.from_bytes(bytes(data))

    def test_missing_suffix_is_rejected(self):
        with self.assertRaisesRegex(create_dfu.DfuError, "suffix"):
            create_dfu.DfuFile.from_bytes(b"x" * 64)


class DfuSeTests(unittest.TestCase):
    def test_layout_of_headers(self):
        dfu = create_dfu.DfuFile.dfuse(
            [create_dfu.Target([create_dfu.Element(0x08000000, b"abcd")])]
        )
        body = dfu.to_bytes()[:-16]
        target_len = 274 + 8 + 4
        self.assertEqual(body[:11], struct.pack("<5sBIB", b"DfuSe", 1, 11 + target_len, 1))
        self.assertEqual(body[11:17], b"Target")
        self.assertEqual(struct.unpack_from("<II", body, 11 + 266), (8 + 4, 1))
        self.assertEqual(body[11 + 274 :], struct.pack("<II", 0x08000000, 4) + b"abcd")

    def test_roundtrip_targets_names_and_ids(self):
        targets = [
            create_dfu.Target([create_dfu.Element(0x08000000, b"a" * 10)], alt=0, name="Internal"),
            create_dfu.Target(
                [create_dfu.Element(0x90000000, b"b"), create_dfu.Element(0x90001000, b"cc")],
                alt=1,
            ),
        ]
        out = create_dfu.DfuFile.from_bytes(
            create_dfu.DfuFile.dfuse(targets, 0xF055, 0xDFA5, 0x0100).to_bytes()
        )
        self.assertEqual(out.targets, targets)
        self.assertEqual((out.vid, out.pid, out.device), (0xF055, 0xDFA5, 0x0100))
        self.assertEqual([e.addr for e in out.elements], [0x08000000, 0x90000000, 0x90001000])

    def test_align_pads_each_element_with_erased_bytes(self):
        dfu = create_dfu.DfuFile.dfuse(
            [create_dfu.Target([create_dfu.Element(0, b"abc"), create_dfu.Element(16, b"x" * 8)])]
        )
        out = create_dfu.DfuFile.from_bytes(dfu.to_bytes(align=8))
        self.assertEqual([e.data for e in out.elements], [b"abc" + b"\xff" * 5, b"x" * 8])

    def test_inconsistent_sizes_are_rejected(self):
        dfu = create_dfu.DfuFile.dfuse([create_dfu.Target([create_dfu.Element(0, b"abcd")])])
        body = bytearray(dfu.to_bytes()[:-16])
        struct.pack_into("<I", body, 11 + 274 + 4, 5)  # element size beyond its target
        data = bytes(body) + dfu.to_bytes()[-16:-4]
        data += struct.pack("<I", ~zlib.crc32(data) & 0xFFFFFFFF)
        with self.assertRaisesRegex(create_dfu.DfuError, "beyond"):
            create_dfu.DfuFile.from_bytes(data)


class CommandLineTests(unittest.TestCase):
    def test_create_info_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = (os.path.join(tmp, n) for n in ("a.bin", "b.bin", "out.dfu"))
            for path, data in ((a, b"A" * 20), (b, b"B" * 5)):
                with open(path, "wb") as f:
                    f.write(data)
            rc = create_dfu.main(
                ["create", "-o", out, "--usb", "0483:df11", "--align", "8", f"0x08000000:{a}",
                 f"0x08010000:{b}"]
            )  # fmt: skip
            self.assertEqual(rc, 0)
            dfu = create_dfu.DfuFile.read(out)
            self.assertEqual((dfu.vid, dfu.pid), (0x0483, 0xDF11))
            self.assertEqual(
                [(e.addr, len(e.data)) for e in dfu.elements], [(0x08000000, 24), (0x08010000, 8)]
            )
            self.assertEqual(create_dfu.main(["extract", out, "-d", os.path.join(tmp, "x")]), 0)
            with open(os.path.join(tmp, "x", "out.dfu.alt0.0x08010000.bin"), "rb") as f:
                self.assertEqual(f.read(), b"B" * 5 + b"\xff" * 3)

    def test_missing_input_is_an_error_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                create_dfu.main(["create", "-o", os.path.join(tmp, "o"), "0:" + tmp + "/none"]), 1
            )


class SignWriterTests(unittest.TestCase):
    def test_signed_image_goes_to_the_dfu_address_with_the_layout_ids(self):
        layout = SimpleNamespace(dfu_addr=0x08102000, dfu_vid=0xF055, dfu_pid=0xDFA5)
        with tempfile.TemporaryDirectory() as tmp:
            image, out = os.path.join(tmp, "app.signed.bin"), os.path.join(tmp, "app.dfu")
            with open(image, "wb") as f:
                f.write(b"signed image" * 3)
            mboot_sign.write_dfu(layout, image, out)
            dfu = create_dfu.DfuFile.read(out)
        self.assertEqual((dfu.vid, dfu.pid), (0xF055, 0xDFA5))
        self.assertEqual(dfu.elements, [create_dfu.Element(0x08102000, b"signed image" * 3)])

    def test_layout_without_dfu_is_refused(self):
        layout = SimpleNamespace(dfu_addr=None, dfu_vid=None, dfu_pid=None)
        with self.assertRaises(mboot_sign.SignError):
            mboot_sign.write_dfu(layout, "in", "out")


if __name__ == "__main__":
    unittest.main()
