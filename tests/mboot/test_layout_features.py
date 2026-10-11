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

# Tests of the optional features of shared/mboot/include/mboot_layout.h: the filesystem
# readers (raw window, gzip), the mbedtls crypto backend, the fault injection hardening profiles,
# the security counter kept by the port and the automatic confirmation. What the header derives
# from the board inputs, the combinations it refuses and what tools/mboot_gen.py exports for
# the Makefiles.
#
# Run with:  python3 -m pytest tests/mboot/test_layout_features.py

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_layout import CC, TOP, STM32_CPP, STM32_DEV, board, layout, preprocess  # noqa: E402


def macros(defs, header="mboot_layout.h"):
    """The macros that the header leaves defined for a board made of defs."""
    with tempfile.TemporaryDirectory() as tmp:
        b = Path(tmp) / "board"
        b.mkdir()
        (b / "mpconfigboard.h").write_text("".join("#define %s %s\n" % kv for kv in defs.items()))
        cmd = [
            CC,
            "-E",
            "-dM",
            "-x",
            "c",
            "-I%s" % (TOP / "shared" / "mboot" / "include"),
            "-I%s" % b,
            "-I%s" % STM32_DEV,
            *STM32_CPP,
            "-",
        ]
        r = subprocess.run(cmd, input='#include "%s"\n' % header, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return dict(re.findall(r"^#define (\w+) ?(.*)$", r.stdout, re.M))


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestFsloadReaders(unittest.TestCase):
    def test_raw_alone_enables_fsload(self):
        m = macros(board(MBOOT_FSLOAD_RAW="1"))
        self.assertEqual(m["MBOOT_FSLOAD_ENABLE"], "1")
        self.assertIn("MBOOT_FSLOAD_RAW", m)
        self.assertNotIn("MBOOT_FSLOAD_FAT", m)

    def test_none_leaves_fsload_off(self):
        m = macros(board())
        self.assertEqual(m["MBOOT_FSLOAD_ENABLE"], "0")
        for name in ("RAW", "FAT", "LFS2", "GZIP"):
            self.assertNotIn("MBOOT_FSLOAD_" + name, m)

    def test_zero_is_not_defined(self):
        m = macros(board(MBOOT_FSLOAD_FAT="1", MBOOT_FSLOAD_RAW="0", MBOOT_FSLOAD_GZIP="0"))
        self.assertNotIn("MBOOT_FSLOAD_RAW", m)
        self.assertNotIn("MBOOT_FSLOAD_GZIP", m)

    def test_gzip_over_any_reader(self):
        for reader in ("FAT", "LFS2", "RAW"):
            m = macros(board(MBOOT_FSLOAD_GZIP="1", **{"MBOOT_FSLOAD_" + reader: "1"}))
            self.assertIn("MBOOT_FSLOAD_GZIP", m)

    def test_gzip_needs_a_reader(self):
        r = preprocess(board(MBOOT_FSLOAD_GZIP="1"))
        self.assertNotEqual(r.returncode, 0)

    def test_generator_exports_the_readers(self):
        _, lay = layout(
            board(
                MBOOT_FSLOAD_FAT="1",
                MBOOT_FSLOAD_LFS2="1",
                MBOOT_FSLOAD_RAW="1",
                MBOOT_FSLOAD_GZIP="1",
            )
        )
        self.assertEqual(sorted(lay["fsload"]), ["fat", "gzip", "lfs2", "raw"])
        _, lay = layout(board(MBOOT_FSLOAD_RAW="1"))
        self.assertEqual(lay["fsload"], ["raw"])


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestCrypto(unittest.TestCase):
    def test_default_is_tinycrypt(self):
        m = macros(board())
        self.assertIn("MCUBOOT_USE_TINYCRYPT", m)
        self.assertNotIn("MCUBOOT_USE_MBED_TLS", m)

    def test_mbedtls(self):
        m = macros(board(MBOOT_CRYPTO="MBOOT_CRYPTO_SEL_MBEDTLS"))
        self.assertIn("MCUBOOT_USE_MBED_TLS", m)
        self.assertNotIn("MCUBOOT_USE_TINYCRYPT", m)
        self.assertIn("MCUBOOT_SIGN_EC256", m)

    def test_unknown_backend(self):
        r = preprocess(board(MBOOT_CRYPTO="7"))
        self.assertNotEqual(r.returncode, 0)

    def test_generator_exports_the_backend(self):
        self.assertEqual(layout(board())[1]["crypto"], "tinycrypt")
        _, lay = layout(board(MBOOT_CRYPTO="MBOOT_CRYPTO_SEL_MBEDTLS"))
        self.assertEqual(lay["crypto"], "mbedtls")


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestFih(unittest.TestCase):
    PROFILES = {0: "OFF", 1: "LOW", 2: "MEDIUM", 3: "HIGH"}

    def test_profiles(self):
        for level, name in self.PROFILES.items():
            m = macros(board(MBOOT_FIH_LEVEL=str(level)))
            for other in self.PROFILES.values():
                self.assertEqual(
                    "MCUBOOT_FIH_PROFILE_" + other in m, other == name, (level, other)
                )

    def test_default_is_off(self):
        self.assertIn("MCUBOOT_FIH_PROFILE_OFF", macros(board()))

    def test_unknown_level(self):
        r = preprocess(board(MBOOT_FIH_LEVEL="4"))
        self.assertNotEqual(r.returncode, 0)

    def test_generator_exports_the_profile(self):
        for level, name in self.PROFILES.items():
            self.assertEqual(layout(board(MBOOT_FIH_LEVEL=str(level)))[1]["fih"], name.lower())


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestPortCounter(unittest.TestCase):
    def test_flash_counter_is_the_default(self):
        m = macros(board())
        self.assertIn("MCUBOOT_HW_ROLLBACK_PROT", m)
        self.assertIn("MBOOT_SECCNT_FLASH", m)
        self.assertNotIn("MBOOT_SECCNT_PORT", m)
        self.assertIn("MBOOT_SECCNT_ADDR", m)

    def test_port_counter(self):
        m = macros(board(MBOOT_SECCNT_PORT="1"))
        self.assertIn("MCUBOOT_HW_ROLLBACK_PROT", m)
        self.assertIn("MBOOT_SECCNT_PORT", m)
        self.assertNotIn("MBOOT_SECCNT_FLASH", m)
        self.assertNotIn("MBOOT_SECCNT_ADDR", m)

    def test_port_counter_has_no_flash_area(self):
        _, lay = layout(board(MBOOT_SECCNT_PORT="1"))
        self.assertNotIn("seccnt", lay["areas"])
        self.assertEqual(lay["rollback"], "counter-port")
        self.assertEqual(layout(board())[1]["rollback"], "counter-flash")
        self.assertEqual(layout(board(MBOOT_ROLLBACK_COUNTER="0"))[1]["rollback"], "version")

    def test_port_counter_is_limited_and_lockable(self):
        # bootutil asks the port whether the counter can still be raised and locks it after the
        # update.
        m = macros(board(MBOOT_SECCNT_PORT="1"), "mcuboot_config/mcuboot_config.h")
        self.assertIn("MCUBOOT_HW_ROLLBACK_PROT_COUNTER_LIMITED", m)
        self.assertIn("MCUBOOT_HW_ROLLBACK_PROT_LOCK", m)
        m = macros(board(), "mcuboot_config/mcuboot_config.h")
        self.assertNotIn("MCUBOOT_HW_ROLLBACK_PROT_COUNTER_LIMITED", m)
        self.assertNotIn("MCUBOOT_HW_ROLLBACK_PROT_LOCK", m)

    def test_port_counter_needs_a_counter(self):
        r = preprocess(board(MBOOT_ROLLBACK_COUNTER="0", MBOOT_SECCNT_PORT="1"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MBOOT_SECCNT_PORT needs MBOOT_ROLLBACK_COUNTER 1", r.stderr)

    def test_zero_is_the_flash_counter(self):
        self.assertIn("MBOOT_SECCNT_FLASH", macros(board(MBOOT_SECCNT_PORT="0")))


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestConfirm(unittest.TestCase):
    def test_manual_is_the_default(self):
        self.assertNotIn("MBOOT_CONFIRM_AUTO", macros(board()))
        self.assertNotIn("MBOOT_CONFIRM_AUTO", macros(board(MBOOT_CONFIRM_AUTO="0")))

    def test_auto(self):
        self.assertIn("MBOOT_CONFIRM_AUTO", macros(board(MBOOT_CONFIRM_AUTO="1")))

    def test_generator_exports_the_mode(self):
        self.assertEqual(layout(board())[1]["confirm_mode"], "manual")
        self.assertEqual(layout(board(MBOOT_CONFIRM_AUTO="1"))[1]["confirm_mode"], "auto")


if __name__ == "__main__":
    unittest.main()
