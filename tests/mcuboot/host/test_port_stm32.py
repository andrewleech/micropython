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

"""Tests of the build system and the STM32H5 port files of the MCUboot port.

  python3 tests/mcuboot/host/test_port_stm32.py

Covers the build directory of the MCUBOOT=1 application, the layout that the application and the
bootloader of a board agree on, the bounded wait of the instruction cache invalidation
(icache_test.c against a register model) and the board checks of the bootloader port (the early
guard of the Makefile and the #error of port_core.c for a board file that lacks a pin or UART
definition). The arm-none-eabi-gcc checks are skipped without the toolchain.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STM32 = os.path.join(TOP, "ports", "stm32")
BOOTLOADER = os.path.join(STM32, "mcuboot")
BOARD_NAME = "NUCLEO_H563ZI"
BOARD_DIR = os.path.join(STM32, "boards", BOARD_NAME)

HAVE_ARM_GCC = shutil.which("arm-none-eabi-gcc") is not None


def make_variables(cwd, args, names):
    """Expanded values of the named make variables after the makefiles have been read; nothing
    is built."""
    recipe = "$(foreach v,%s,$(info $(v)=$($(v))))" % " ".join(names)
    proc = subprocess.run(
        ["make", "--no-print-directory", "--eval=print-vars: ; " + recipe] + args + ["print-vars"],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "make %s failed (%d): %s" % (" ".join(args), proc.returncode, proc.stderr)
        )
    found = {}
    for line in proc.stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in names:
            found[name] = value
    return found


def stm32_build_dir(port_dir, args):
    # The generated board files of an MCUBOOT=1 build are written when the Makefile is parsed.
    with tempfile.TemporaryDirectory() as gen:
        return make_variables(port_dir, args + ["MCUBOOT_GEN_DIR=" + gen], ("BUILD",))["BUILD"]


def copy_board(dest_parent, name=BOARD_NAME):
    dest = os.path.join(dest_parent, name)
    shutil.copytree(BOARD_DIR, dest)
    return dest


class AppBuildDirectory(unittest.TestCase):
    """MCUBOOT=1 must not reuse the object directory of the default firmware: make does not
    notice that the flags, the linker script and the load address differ."""

    def build(self, *args):
        return stm32_build_dir(STM32, ["BOARD=" + BOARD_NAME] + list(args))

    def test_default_firmware_keeps_its_directory(self):
        self.assertEqual(self.build(), "build-" + BOARD_NAME)
        self.assertEqual(self.build("MCUBOOT=0"), "build-" + BOARD_NAME)

    def test_mcuboot_application_has_its_own_directory(self):
        self.assertEqual(self.build("MCUBOOT=1"), "build-%s-mcuboot" % BOARD_NAME)

    def test_explicit_build_wins(self):
        self.assertEqual(self.build("MCUBOOT=1", "BUILD=/tmp/x"), "/tmp/x")

    def test_board_variant_is_kept_in_the_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = copy_board(tmp)
            with open(os.path.join(board, "mpconfigvariant_VAR.mk"), "w") as f:
                f.write("# variant without changes\n")
            args = ["BOARD_DIR=" + board, "BOARD_VARIANT=VAR"]
            self.assertEqual(stm32_build_dir(STM32, args), "build-%s-VAR" % BOARD_NAME)
            self.assertEqual(
                stm32_build_dir(STM32, args + ["MCUBOOT=1"]), "build-%s-VAR-mcuboot" % BOARD_NAME
            )


class LayoutAgreement(unittest.TestCase):
    """The bootloader and the application of a board are built from the same board and port
    headers, so that the layout id signed into an image is the one the bootloader checks, and a
    board whose layout is invalid builds neither."""

    NAMES = (
        "MCUBOOT_LAYOUT_ID",
        "MCUBOOT_PRIMARY_ADDR",
        "MCUBOOT_APP_LINK_ADDR",
        "MCUBOOT_IMGTOOL_SIGN_ARGS",
    )

    def layout(self, port_dir, *args):
        with tempfile.TemporaryDirectory() as gen:
            args = ["BOARD=" + BOARD_NAME, "MCUBOOT_GEN_DIR=" + gen] + list(args)
            return make_variables(port_dir, args, self.NAMES)

    def test_application_and_bootloader_have_one_layout(self):
        bootloader = self.layout(BOOTLOADER)
        application = self.layout(STM32, "MCUBOOT=1")
        self.assertEqual(sorted(bootloader), sorted(self.NAMES))
        self.assertEqual(application, bootloader)

    def test_invalid_layout_is_refused_before_anything_is_built(self):
        # The size of a slot has to be a multiple of the erase unit: the #error of
        # mcuboot_layout.h stops the make run of both builds.
        with tempfile.TemporaryDirectory() as tmp:
            board = copy_board(tmp)
            header = os.path.join(board, "mpconfigboard.h")
            with open(header) as f:
                text = f.read()
            with open(header, "w") as f:
                f.write(
                    re.sub(
                        r"^(#define MCUBOOT_PRIMARY_SIZE\s+).*$", r"\1(0x40001)", text, flags=re.M
                    )
                )
            for port_dir, extra in ((BOOTLOADER, []), (STM32, ["MCUBOOT=1"])):
                with self.subTest(port=os.path.relpath(port_dir, TOP)):
                    proc = subprocess.run(
                        ["make", "BOARD_DIR=" + board, "BUILD=" + os.path.join(tmp, "build"), "-n"]
                        + extra,
                        cwd=port_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        universal_newlines=True,
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("multiple of the erase unit", proc.stdout)


class IcacheInvalidate(unittest.TestCase):
    """The wait for the end of an instruction cache invalidation is bounded. A cache that never
    completes it is reported as -ETIMEDOUT; an unbounded wait would hang every flash operation
    and the jump to the application."""

    @unittest.skipUnless(shutil.which("cc"), "cc not found")
    def test_bounded_wait_reports_timeout(self):
        src = os.path.join(HERE, "stm32_port")
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "icache_test")
            subprocess.run(
                [
                    "cc",
                    "-std=gnu99",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + os.path.join(src, "stub"),
                    "-I" + BOOTLOADER,
                    "-I" + os.path.join(TOP, "shared", "mcuboot", "include"),
                    os.path.join(src, "icache_test.c"),
                    "-o",
                    exe,
                ],
                check=True,
            )
            # A hang is a failure, not a stuck test run.
            proc = subprocess.run(
                [exe], stdout=subprocess.PIPE, universal_newlines=True, timeout=20
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("icache_test PASSED", proc.stdout)


class BootloaderBoard(unittest.TestCase):
    """The bootloader takes pins and UART from the board file and refuses a board it does not
    support instead of silently using the pins of another board."""

    # Macros the bootloader reads from mpconfigboard.h and the lines that define them.
    REQUIRED = (
        "MICROPY_HW_LED1",
        "MICROPY_HW_LED2",
        "MICROPY_HW_LED3",
        "MICROPY_HW_LED_ON",
        "MICROPY_HW_LED_OFF",
        "MICROPY_HW_USRSW_PIN",
        "MICROPY_HW_USRSW_PULL",
        "MICROPY_HW_USRSW_PRESSED",
        "MICROPY_HW_UART_REPL_BAUD",
        "MICROPY_HW_UART3_TX",
    )

    def make(self, board, build, *targets):
        return subprocess.run(
            ["make", "BOARD_DIR=" + board, "BUILD=" + build] + list(targets),
            cwd=BOOTLOADER,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

    def compile_port_core(self, tmp, edit=None):
        board = copy_board(tmp)
        if edit is not None:
            header = os.path.join(board, "mpconfigboard.h")
            with open(header) as f:
                text = f.read()
            text = edit(text)
            with open(header, "w") as f:
                f.write(text)
        build = os.path.join(tmp, "build")
        return self.make(board, build, os.path.join(build, "port_core.o"))

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_unchanged_board_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.compile_port_core(tmp)
            self.assertEqual(proc.returncode, 0, proc.stdout)

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_missing_definition_is_named_in_the_error(self):
        for name in self.REQUIRED:
            with self.subTest(macro=name):
                with tempfile.TemporaryDirectory() as tmp:
                    proc = self.compile_port_core(
                        tmp, lambda t: re.sub(r"^#define %s\b.*\n" % name, "", t, flags=re.M)
                    )
                self.assertNotEqual(proc.returncode, 0, "built without %s" % name)
                self.assertRegex(proc.stdout, r"error: #error .*%s" % name)

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_repl_on_another_uart_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.compile_port_core(tmp, lambda t: t.replace("PYB_UART_3", "PYB_UART_1"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout, r"error: #error .*MICROPY_HW_UART_REPL")

    def test_other_mcu_series_is_refused_before_anything_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = copy_board(tmp)
            mk = os.path.join(board, "mpconfigboard.mk")
            with open(mk) as f:
                text = f.read()
            with open(mk, "w") as f:
                f.write(text.replace("MCU_SERIES = h5", "MCU_SERIES = f4"))
            proc = self.make(board, os.path.join(tmp, "build"), "-n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("MCU_SERIES=f4", proc.stdout)

    def test_deploy_target_needs_a_pyocd_target(self):
        # The pyocd target of a board comes from its mpconfigboard.mk; without it the target
        # is refused instead of programming the part of another board.
        with tempfile.TemporaryDirectory() as tmp:
            board = copy_board(tmp)
            mk = os.path.join(board, "mpconfigboard.mk")
            with open(mk) as f:
                text = f.read()
            with open(mk, "w") as f:
                f.write(re.sub(r"^PYOCD_TARGET = .*\n", "", text, flags=re.M))
            proc = subprocess.run(
                [
                    "make",
                    "BOARD_DIR=" + board,
                    "BUILD=" + os.path.join(tmp, "build"),
                    "PROBE=0000",
                    "-n",
                    "deploy-bootloader",
                ],
                cwd=BOOTLOADER,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PYOCD_TARGET", proc.stdout)


class F7Board(unittest.TestCase):
    """PYBD_SF6 (STM32F767): the sectors are not uniform (four of 32 KiB, one of 128 KiB, seven of
    256 KiB), the flash has no ECC and the SPI flash has a constant size for the layout. The
    bootloader and the application agree on the layout, a slot that crosses two sector sizes is
    refused and the bootloader links into its boot area."""

    BOARD = "PYBD_SF6"
    NAMES = LayoutAgreement.NAMES

    def layout(self, port_dir, *args):
        with tempfile.TemporaryDirectory() as gen:
            args = ["BOARD=" + self.BOARD, "MCUBOOT_GEN_DIR=" + gen] + list(args)
            return make_variables(port_dir, args, self.NAMES)

    def test_application_and_bootloader_have_one_layout(self):
        self.assertEqual(self.layout(STM32, "MCUBOOT=1"), self.layout(BOOTLOADER))

    def test_slot_across_sectors_of_different_size_is_refused(self):
        # A slot from the 128 KiB sector into the 256 KiB sectors has no single erase unit.
        with tempfile.TemporaryDirectory() as tmp:
            board = os.path.join(tmp, self.BOARD)
            shutil.copytree(os.path.join(STM32, "boards", self.BOARD), board)
            header = os.path.join(board, "mpconfigboard.h")
            with open(header) as f:
                text = f.read()
            text = text.replace("(0x08040000)", "(0x08020000)")
            with open(header, "w") as f:
                f.write(text)
            proc = subprocess.run(
                ["make", "BOARD_DIR=" + board, "BUILD=" + os.path.join(tmp, "build"), "-n"],
                cwd=BOOTLOADER,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout, r"error: #error .*primary slot has to lie in one run")

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_bootloader_links_into_the_boot_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = os.path.join(tmp, "build")
            proc = subprocess.run(
                ["make", "BOARD=" + self.BOARD, "BUILD=" + build, "-j4"],
                cwd=BOOTLOADER,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            size = os.path.getsize(os.path.join(build, "firmware.bin"))
            with open(os.path.join(build, "mcuboot_gen", "mcuboot_layout.ld")) as f:
                boot_size = int(re.search(r"MCUBOOT_BOOT_SIZE = (0x[0-9a-f]+);", f.read())[1], 16)
        self.assertEqual(size, boot_size)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
