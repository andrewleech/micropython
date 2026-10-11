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

"""Tests of the build system of the STM32 Mboot port.

  python3 tests/mboot/host/test_port_stm32.py

Covers the layout that the application and bootloader of a board agree on, and the board checks
of the bootloader build (the early guards of Makefile.mboot and the #error of mboot_boot.c for a
board file that lacks a pin definition). The arm-none-eabi-gcc checks are skipped without the
toolchain.
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
BOOTLOADER_MAKE = ["make", "-f", "Makefile.mboot"]
BOARD_NAME = "NUCLEO_H563ZI"
BOARD_DIR = os.path.join(STM32, "boards", BOARD_NAME)

HAVE_ARM_GCC = shutil.which("arm-none-eabi-gcc") is not None


def make_variables(cwd, args, names, make=("make",)):
    """Expanded values of the named make variables after the makefiles have been read; nothing
    is built."""
    recipe = "$(foreach v,%s,$(info $(v)=$($(v))))" % " ".join(names)
    proc = subprocess.run(
        list(make)
        + ["--no-print-directory", "--eval=print-vars: ; " + recipe]
        + args
        + ["print-vars"],
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


def copy_board(dest_parent, name=BOARD_NAME):
    dest = os.path.join(dest_parent, name)
    shutil.copytree(BOARD_DIR, dest)
    return dest


class LayoutAgreement(unittest.TestCase):
    """The bootloader and the application of a board are built from the same board and port
    headers, so that the layout id signed into an image is the one the bootloader checks, and a
    board whose layout is invalid builds neither."""

    NAMES = (
        "MBOOT_LAYOUT_ID",
        "MBOOT_PRIMARY_ADDR",
        "MBOOT_APP_LINK_ADDR",
        "MBOOT_IMGTOOL_SIGN_ARGS",
    )

    def layout(self, make, *args):
        with tempfile.TemporaryDirectory() as gen:
            args = ["BOARD=" + BOARD_NAME, "MBOOT_GEN_DIR=" + gen] + list(args)
            return make_variables(STM32, args, self.NAMES, make)

    def test_application_and_bootloader_have_one_layout(self):
        bootloader = self.layout(BOOTLOADER_MAKE)
        application = self.layout(["make"])
        self.assertEqual(sorted(bootloader), sorted(self.NAMES))
        self.assertEqual(application, bootloader)

    def test_invalid_layout_is_refused_before_anything_is_built(self):
        # The size of a slot has to be a multiple of the erase unit: the #error of
        # mboot_layout.h stops the make run of both builds.
        with tempfile.TemporaryDirectory() as tmp:
            board = copy_board(tmp)
            header = os.path.join(board, "mpconfigboard.h")
            with open(header) as f:
                text = f.read()
            with open(header, "w") as f:
                f.write(
                    re.sub(
                        r"^(#define MBOOT_PRIMARY_SIZE\s+).*$", r"\1(0x40001)", text, flags=re.M
                    )
                )
            for make in (BOOTLOADER_MAKE, ["make"]):
                with self.subTest(makefile=" ".join(make)):
                    proc = subprocess.run(
                        make
                        + [
                            "BOARD_DIR=" + board,
                            "BUILD=" + os.path.join(tmp, "build"),
                            "-n",
                        ],
                        cwd=STM32,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        universal_newlines=True,
                    )
                    self.assertNotEqual(proc.returncode, 0)


class BootloaderBoard(unittest.TestCase):
    """The bootloader takes pins and the log UART from the board file and refuses a board it does
    not support instead of silently using the pins of another board."""

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
        "MICROPY_HW_UART_REPL",
        "MICROPY_HW_UART_REPL_BAUD",
    )

    def make(self, board, build, *targets):
        return subprocess.run(
            BOOTLOADER_MAKE + ["BOARD_DIR=" + board, "BUILD=" + build] + list(targets),
            cwd=STM32,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

    def compile_boot_main(self, tmp, edit=None):
        board = copy_board(tmp)
        if edit is not None:
            header = os.path.join(board, "mpconfigboard.h")
            with open(header) as f:
                text = f.read()
            text = edit(text)
            with open(header, "w") as f:
                f.write(text)
        build = os.path.join(tmp, "build")
        return self.make(board, build, os.path.join(build, "mboot_boot.o"))

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_unchanged_board_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.compile_boot_main(tmp)
            self.assertEqual(proc.returncode, 0, proc.stdout)

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_missing_definition_is_refused(self):
        for name in self.REQUIRED:
            with self.subTest(macro=name):
                with tempfile.TemporaryDirectory() as tmp:
                    proc = self.compile_boot_main(
                        tmp, lambda t: re.sub(r"^#define %s\b.*\n" % name, "", t, flags=re.M)
                    )
                self.assertNotEqual(proc.returncode, 0, "built without %s" % name)

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
                BOOTLOADER_MAKE
                + [
                    "BOARD_DIR=" + board,
                    "BUILD=" + os.path.join(tmp, "build"),
                    "PROBE=0000",
                    "-n",
                    "deploy-bootloader",
                ],
                cwd=STM32,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
        self.assertNotEqual(proc.returncode, 0)


class F7Board(unittest.TestCase):
    """PYBD_SF6 (STM32F767): the sectors are not uniform (four of 32 KiB, one of 128 KiB, seven of
    256 KiB), the flash has no ECC and the SPI flash has a constant size for the layout. The
    bootloader and the application agree on the layout, a slot that crosses two sector sizes is
    refused and the bootloader links into its boot area."""

    BOARD = "PYBD_SF6"
    NAMES = LayoutAgreement.NAMES

    def layout(self, make, *args):
        with tempfile.TemporaryDirectory() as gen:
            args = ["BOARD=" + self.BOARD, "MBOOT_GEN_DIR=" + gen] + list(args)
            return make_variables(STM32, args, self.NAMES, make)

    def test_application_and_bootloader_have_one_layout(self):
        self.assertEqual(self.layout(["make"]), self.layout(BOOTLOADER_MAKE))

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
                BOOTLOADER_MAKE
                + ["BOARD_DIR=" + board, "BUILD=" + os.path.join(tmp, "build"), "-n"],
                cwd=STM32,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
        self.assertNotEqual(proc.returncode, 0)

    @unittest.skipUnless(HAVE_ARM_GCC, "arm-none-eabi-gcc not found")
    def test_bootloader_links_into_the_boot_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = os.path.join(tmp, "build")
            proc = subprocess.run(
                BOOTLOADER_MAKE + ["BOARD=" + self.BOARD, "BUILD=" + build, "-j4"],
                cwd=STM32,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            size = os.path.getsize(os.path.join(build, "firmware.bin"))
            with open(os.path.join(build, "mboot_gen", "mboot_layout.ld")) as f:
                boot_size = int(re.search(r"MBOOT_BOOT_SIZE = (0x[0-9a-f]+);", f.read())[1], 16)
        self.assertEqual(size, boot_size)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
