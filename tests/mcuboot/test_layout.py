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

# Tests of shared/mcuboot/include/mcuboot_layout.h and tools/mcuboot_gen.py: the layout derived
# from a board, the configurations the header refuses, and the generator output.
#
# Run with:  python3 -m pytest tests/mcuboot/test_layout.py

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / "tools"))

import mcuboot_gen as gen  # noqa: E402

CC = shutil.which("gcc") or shutil.which("cc")
STM32_BOARD = TOP / "ports" / "stm32" / "boards" / "NUCLEO_H563ZI"
STM32_DEV = TOP / "ports" / "stm32" / "mcuboot"
CMSIS_HOST = TOP / "tests" / "mcuboot" / "host" / "cmsis_host"
KEY = TOP / "tests" / "mcuboot" / "keys" / "test-ecdsa-p256.pem"


def cmsis_flags(series="H5xx", device="STM32H573xx"):
    """Options that let a host compiler read the CMSIS device header that mcuboot_dev.h of the
    stm32 port includes (the stub in cmsis_host stands in for the Arm intrinsics header)."""
    return [
        "-D%s" % device,
        "-isystem%s" % (TOP / "lib" / "stm32lib" / "CMSIS" / ("STM32" + series) / "Include"),
        "-isystem%s" % (TOP / "lib" / "CMSIS_6" / "CMSIS" / "Core" / "Include"),
        "-idirafter%s" % CMSIS_HOST,
    ]


STM32_CPP = cmsis_flags()


def stm32_dev_header():
    """Text of mcuboot_dev.h of the stm32 port for a copy in another directory."""
    return (
        (STM32_DEV / "mcuboot_dev.h")
        .read_text()
        .replace('#include "../flash.h"', '#include "%s"' % (TOP / "ports" / "stm32" / "flash.h"))
    )


# A board for the STM32H5 device description: bank 1 holds the bootloader, the primary slot,
# the areas behind it and the filesystem, bank 2 the secondary slot.
BASE = {
    "MCUBOOT_PRIMARY_SIZE": "(640 * 1024)",
    "MCUBOOT_SECONDARY_ADDR": "0x08100000",
    "MCUBOOT_ROLLBACK_COUNTER": "1",
}

# A SPI flash behind the internal flash, as the MBOOT_SPIFLASH_* macros of a board describe it.
SPI = {
    "MBOOT_SPIFLASH_ADDR": "0x90000000",
    "MBOOT_SPIFLASH_BYTE_SIZE": "(16 * 1024 * 1024)",
    "MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE": "2",
}

# The SPI flash of the STM32F769DISC as its board file describes it for mboot: 512 pages of
# 128 KiB, each of 32 erase blocks of 4 KiB.
F769DISC = TOP / "ports" / "stm32" / "boards" / "STM32F769DISC" / "mpconfigboard.h"


def mboot_spiflash(path):
    """The MBOOT_SPIFLASH_* macros a board file defines."""
    found = re.findall(
        r"^#define\s+(MBOOT_SPIFLASH_(?:ADDR|BYTE_SIZE|ERASE_BLOCKS_PER_PAGE))\s+(.*?)\s*(?://.*)?$",
        path.read_text(),
        re.M,
    )
    return dict(found)


def preprocess(defs, dev_dir=None, extra=(), cpp=STM32_CPP):
    """Run the preprocessor over mcuboot_layout.h with a board made of defs. Returns the
    completed process."""
    with tempfile.TemporaryDirectory() as tmp:
        board = Path(tmp) / "board"
        board.mkdir()
        (board / "mpconfigboard.h").write_text(
            "".join("#define %s %s\n" % kv for kv in defs.items())
        )
        cmd = [
            CC,
            "-E",
            "-P",
            "-x",
            "c",
            "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
            "-I%s" % board,
            "-I%s" % (dev_dir or STM32_DEV),
            *cpp,
            *extra,
            "-",
        ]
        return subprocess.run(
            cmd, input='#include "mcuboot_layout.h"\n', capture_output=True, text=True
        )


def board(**overrides):
    defs = dict(BASE)
    defs.update(overrides)
    return {k: v for k, v in defs.items() if v is not None}


def layout(defs):
    """The layout the generator reads from a board."""
    cmd = [
        CC,
        "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
        "-I%s" % STM32_DEV,
        *STM32_CPP,
    ]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mpconfigboard.h").write_text(
            "".join("#define %s %s\n" % kv for kv in defs.items())
        )
        values = gen.probe(cmd + ["-I%s" % tmp])
    return values, gen.build(values, "test")


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestRealBoard(unittest.TestCase):
    """The NUCLEO-H563ZI board file."""

    def cmd(self):
        return [
            CC,
            "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
            "-I%s" % STM32_BOARD,
            "-I%s" % STM32_DEV,
            *STM32_CPP,
        ]

    def test_layout(self):
        lay = gen.build(gen.probe(self.cmd()), "NUCLEO_H563ZI")
        a = lay["areas"]
        self.assertEqual((a["boot"]["addr"], a["boot"]["size"]), (0x08000000, 0x10000))
        self.assertEqual((a["primary"]["addr"], a["primary"]["size"]), (0x08010000, 0xA0000))
        self.assertEqual((a["secondary"]["addr"], a["secondary"]["size"]), (0x08100000, 0xA2000))
        self.assertEqual((a["seccnt"]["addr"], a["seccnt"]["size"]), (0x080B0000, 0x4000))
        self.assertEqual((a["shadow"]["addr"], a["shadow"]["size"]), (0x080B4000, 0x4000))
        self.assertEqual((a["fs"]["addr"], a["fs"]["size"]), (0x080B8000, 0x48000))
        self.assertEqual(lay["max_img_sectors"], 81)
        self.assertEqual(lay["layout_id"], "f78a8771")
        self.assertEqual({a["erase"] for a in lay["areas"].values()}, {0x2000})
        self.assertEqual(lay["trailer_size"], 2688)
        self.assertEqual(lay["max_image_size"], 0x9E000)
        self.assertEqual(lay["app_start"], 0x08010400)
        self.assertEqual(lay["dfu"]["update_addr"], 0x08102000)
        self.assertEqual(lay["imgtool"]["align"], 16)
        self.assertEqual(lay["imgtool"]["max_align"], 16)
        self.assertEqual(lay["fsload"], ["fat"])

    def test_internal_flash_comes_from_the_device_headers(self):
        # Device 0 is read from the CMSIS header and ports/stm32/flash.h of the STM32H5.
        dev = gen.build(gen.probe(self.cmd()), "NUCLEO_H563ZI")["devices"][0]
        self.assertEqual(
            (dev["base"], dev["size"], dev["runs"], dev["write"]),
            (0x08000000, 0x200000, [{"off": 0, "size": 0x200000, "erase": 0x2000}], 16),
        )
        self.assertEqual((dev["erased_val"], dev["mapped"], dev["ecc"]), (0xFF, 1, 1))

    def test_layout_id_matches_c(self):
        # The identity of the layout is computed by the preprocessor for the C code and read
        # back by the generator; both have to give the same word.
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "id.c"
            src.write_text(
                '#include <stdio.h>\n#include <stdint.h>\n#include "mcuboot_layout.h"\n'
                'int main(void) { printf("%08x\\n", (unsigned)MCUBOOT_LAYOUT_ID); return 0; }\n'
            )
            exe = Path(tmp) / "id"
            subprocess.run(self.cmd() + [str(src), "-o", str(exe)], check=True)
            c_id = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        lay = gen.build(gen.probe(self.cmd()), "NUCLEO_H563ZI")
        self.assertEqual(lay["layout_id"], c_id.strip())

    def test_layout_id_follows_layout(self):
        a = layout(board())[1]["layout_id"]
        self.assertEqual(a, layout(board())[1]["layout_id"])
        self.assertNotEqual(a, layout(board(MCUBOOT_PRIMARY_SIZE="(632 * 1024)"))[1]["layout_id"])
        self.assertNotEqual(a, layout(board(MCUBOOT_ROLLBACK_COUNTER="0"))[1]["layout_id"])


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestDerivation(unittest.TestCase):
    def test_version_rollback_has_no_counter_area(self):
        _, lay = layout(board(MCUBOOT_ROLLBACK_COUNTER="0"))
        self.assertNotIn("seccnt", lay["areas"])
        self.assertEqual(lay["areas"]["shadow"]["addr"], 0x080B0000)
        self.assertEqual(lay["areas"]["fs"]["addr"], 0x080B4000)

    def test_filesystem_override(self):
        _, lay = layout(board(MCUBOOT_FS_ADDR="0x080C0000", MCUBOOT_FS_SIZE="0x40000"))
        self.assertEqual(
            (lay["areas"]["fs"]["addr"], lay["areas"]["fs"]["size"]), (0x080C0000, 0x40000)
        )

    def test_primary_address_override(self):
        _, lay = layout(board(MCUBOOT_PRIMARY_ADDR="0x08020000", MCUBOOT_PRIMARY_SIZE="0x80000"))
        self.assertEqual(lay["areas"]["primary"]["addr"], 0x08020000)
        self.assertEqual(lay["areas"]["seccnt"]["addr"], 0x080A0000)

    def test_dfu_ids(self):
        _, lay = layout(board())
        self.assertEqual((lay["dfu"]["vid"], lay["dfu"]["pid"]), (0xF055, 0xDFA5))
        _, lay = layout(board(MCUBOOT_DFU_PID="0xDFA7"))
        self.assertEqual(lay["dfu"]["pid"], 0xDFA7)

    def test_fsload_types(self):
        self.assertEqual(layout(board())[1]["fsload"], [])
        self.assertEqual(layout(board(MCUBOOT_FSLOAD_LFS2="1"))[1]["fsload"], ["lfs2"])
        both = layout(board(MCUBOOT_FSLOAD_FAT="1", MCUBOOT_FSLOAD_LFS2="1"))[1]["fsload"]
        self.assertEqual(both, ["fat", "lfs2"])

    def test_spi_secondary(self):
        # The secondary slot in a SPI flash: the shadow area covers the primary slot only.
        _, lay = layout(board(MCUBOOT_SECONDARY_ADDR="0x90000000", **SPI))
        self.assertEqual(lay["areas"]["secondary"]["dev"], 1)
        self.assertEqual(lay["areas"]["secondary"]["off"], 0)
        self.assertEqual(lay["areas"]["shadow"]["size"], 0x2000)
        self.assertEqual(lay["areas"]["fs"]["addr"] + lay["areas"]["fs"]["size"], 0x08200000)
        self.assertEqual(len(lay["devices"]), 2)

    def test_filesystem_on_the_f769disc_spi_flash(self):
        # The SPI flash of a board with an mboot configuration holds the filesystem of the
        # application: only the bootloader reads it.
        spi = mboot_spiflash(F769DISC)
        self.assertEqual(len(spi), 3)
        _, lay = layout(
            board(
                MCUBOOT_FS_ADDR=spi["MBOOT_SPIFLASH_ADDR"],
                MCUBOOT_FS_SIZE=spi["MBOOT_SPIFLASH_BYTE_SIZE"],
                **spi,
            )
        )
        fs = lay["areas"]["fs"]
        self.assertEqual((fs["dev"], fs["off"], fs["size"]), (1, 0, 64 * 1024 * 1024))
        self.assertEqual(
            lay["devices"][1]["runs"], [{"off": 0, "size": 64 * 1024 * 1024, "erase": 128 * 1024}]
        )
        self.assertEqual(lay["areas"]["secondary"]["dev"], 0)

    def test_signing_arguments(self):
        _, lay = layout(board(MCUBOOT_SECURITY_COUNTER="3"))
        args = lay["imgtool"]["sign_args"]
        for pair in (
            ("--slot-size", "0xA0000"),
            ("--max-sectors", "81"),
            ("--security-counter", "3"),
        ):
            self.assertEqual(args[args.index(pair[0]) + 1], pair[1])
        self.assertEqual(lay["imgtool"]["initial_args"][-2:], ["--pad", "--confirm"])


HOST_BOARDS = TOP / "tests" / "mcuboot" / "host" / "boards"
SEL = {
    "single": "(MCUBOOT_POLICY_SEL_SINGLE)",
    "overwrite": "(MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL)",
    "move": "(MCUBOOT_SWAP_MODE_SEL_MOVE)",
    "scratch": "(MCUBOOT_SWAP_MODE_SEL_SCRATCH)",
    "offset": "(MCUBOOT_SWAP_MODE_SEL_OFFSET)",
}


def host_layout(name):
    """The layout of a board of tests/mcuboot/host/boards (a flash without ECC)."""
    cmd = [
        CC,
        "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
        "-I%s" % (HOST_BOARDS / name),
        *STM32_CPP,
    ]
    return gen.probe(cmd), gen.build(gen.probe(cmd), name)


def bootutil_trailer(defs, dev_dir=None, board_dir=None):
    """(boot_trailer_sz() of bootutil_area.c, the swap info field to the end of the primary slot,
    MCUBOOT_TRAILER_SIZE) of a configuration. Compiles the bootutil source that sizes the trailer."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if board_dir is None:
            (tmp / "mpconfigboard.h").write_text(
                "".join("#define %s %s\n" % kv for kv in defs.items())
            )
            board_dir = tmp
        (tmp / "t.c").write_text(
            """
#include <stdio.h>
#include <stdint.h>
#include "mcuboot_layout.h"
#include "bootutil/bootutil_public.h"
#include "bootutil_priv.h"
#include "bootutil_area.h"
#include "bootutil_misc.h"
#include "flash_map_backend/flash_map_backend.h"
uint32_t flash_area_align(const struct flash_area *fa) { (void)fa; return 0; }
int flash_area_get_sector(const struct flash_area *fa, off_t off, struct flash_sector *fs) { (void)fa; (void)off; (void)fs; return -1; }
int flash_area_erase(const struct flash_area *fa, uint32_t off, uint32_t len) { (void)fa; (void)off; (void)len; return -1; }
void mcuboot_port_wdt_feed(void) {}
const union boot_img_magic_t boot_img_magic = {{0}};
int main(void) {
    struct flash_area fa = {.fa_size = MCUBOOT_PRIMARY_SIZE};
    printf("%u %u %u\\n", (unsigned)boot_trailer_sz(MCUBOOT_MAX_WRITE_UNIT),
        (unsigned)(MCUBOOT_PRIMARY_SIZE - (boot_copy_done_off(&fa) - BOOT_MAX_ALIGN)), (unsigned)MCUBOOT_TRAILER_SIZE);
    return 0;
}
"""
        )
        lib = TOP / "lib" / "mcuboot" / "boot" / "bootutil"
        exe = tmp / "t"
        subprocess.run(
            [
                CC,
                "-w",
                "-o",
                str(exe),
                str(tmp / "t.c"),
                str(lib / "src" / "bootutil_area.c"),
                "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                "-I%s" % board_dir,
                "-I%s" % (dev_dir or STM32_DEV),
                "-I%s" % (lib / "include"),
                "-I%s" % (lib / "src"),
                "-include",
                str(
                    TOP / "shared" / "mcuboot" / "include" / "mcuboot_config" / "mcuboot_config.h"
                ),
                *STM32_CPP,
            ],
            check=True,
        )
        out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
    return tuple(int(x) for x in out.split())


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestPolicies(unittest.TestCase):
    """Derived geometry of the slot policies and swap modes."""

    def test_default_is_swap_using_offset(self):
        values, lay = layout(board())
        self.assertEqual((lay["policy"], lay["swap_mode"]), ("swap", "offset"))
        self.assertEqual(values["MCUBOOT_BOOTSTRAP"], 1)
        self.assertNotIn("--overwrite-only", lay["imgtool"]["sign_args"])
        self.assertEqual(
            (lay["rollback"], lay["crypto"], lay["fih"]), ("counter-flash", "tinycrypt", "off")
        )
        self.assertTrue(lay["validate_primary"])
        self.assertFalse(lay["hash_direct"])

    def test_h563_layout_id_is_independent_of_the_choices(self):
        # Spelling out the default policy, swap mode, hash and validate settings gives the same
        # layout id as leaving them out.
        base = layout(board())[1]["layout_id"]
        named = layout(
            board(
                MCUBOOT_POLICY=SEL["single"].replace("SINGLE", "SWAP"),
                MCUBOOT_SWAP_MODE=SEL["offset"],
                MCUBOOT_HASH_DIRECT="0",
                MCUBOOT_VALIDATE_PRIMARY="1",
            )
        )[1]["layout_id"]
        self.assertEqual(base, named)

    def test_layout_ids_differ_by_policy_and_mode(self):
        ids = {
            layout(board(**kv))[1]["layout_id"]
            for kv in (
                {},
                {"MCUBOOT_SWAP_MODE": SEL["move"]},
                {"MCUBOOT_POLICY": SEL["overwrite"]},
                {"MCUBOOT_POLICY": SEL["single"], "MCUBOOT_SECONDARY_ADDR": None},
            )
        }
        self.assertEqual(len(ids), 4)

    def test_swap_using_move(self):
        values, lay = layout(board(MCUBOOT_SWAP_MODE=SEL["move"]))
        a = lay["areas"]
        erase = 0x2000
        self.assertEqual((lay["policy"], lay["swap_mode"]), ("swap", "move"))
        # The secondary slot is one erase unit smaller than the primary slot, which keeps a unit free
        # for the move; neither slot has the spare unit of swap using offset.
        self.assertEqual(a["secondary"]["size"], a["primary"]["size"] - erase)
        self.assertNotIn("MCUBOOT_BOOTSTRAP", values)
        self.assertEqual(lay["dfu"]["update_addr"], a["secondary"]["addr"])
        # Three status states per sector, no field for the size of the TLVs; the image leaves out
        # the trailer sectors and the free unit.
        trailer_sectors = -(-lay["trailer_size"] // erase)
        self.assertEqual(
            lay["max_image_size"], a["primary"]["size"] - (trailer_sectors + 1) * erase
        )
        self.assertEqual(lay["dfu"]["regions"][0]["size"], lay["max_image_size"])
        self.assertEqual(lay["max_img_sectors"], a["primary"]["size"] // erase)

    def test_swap_using_scratch_has_a_scratch_area(self):
        values, lay = host_layout("scratch")
        a = lay["areas"]
        self.assertEqual(lay["swap_mode"], "scratch")
        self.assertEqual(a["secondary"]["size"], a["primary"]["size"])
        self.assertEqual((a["scratch"]["id"], a["scratch"]["size"]), (3, 0x2000))
        self.assertEqual(a["scratch"]["addr"], a["primary"]["addr"] + a["primary"]["size"])
        self.assertEqual(a["fs"]["addr"], a["scratch"]["addr"] + a["scratch"]["size"])
        self.assertNotIn("MCUBOOT_BOOTSTRAP", values)

    def test_scratch_placement_is_a_board_input(self):
        # A board-placed scratch area does not move the default filesystem address.
        with tempfile.TemporaryDirectory() as tmp:
            src = HOST_BOARDS / "scratch"
            for f in ("mcuboot_dev.h", "mpconfigboard.h"):
                shutil.copy(src / f, tmp)
            with open(Path(tmp) / "mpconfigboard.h", "a") as f:
                f.write(
                    "#define MCUBOOT_SCRATCH_ADDR (0x900E0000)\n#define MCUBOOT_SCRATCH_SIZE (0x4000)\n"
                )
            cmd = [CC, "-I%s" % (TOP / "shared" / "mcuboot" / "include"), "-I%s" % tmp]
            lay = gen.build(gen.probe(cmd), "t")
        self.assertEqual(
            (lay["areas"]["scratch"]["addr"], lay["areas"]["scratch"]["size"]),
            (0x900E0000, 0x4000),
        )
        self.assertEqual(
            lay["areas"]["fs"]["addr"],
            lay["areas"]["primary"]["addr"] + lay["areas"]["primary"]["size"],
        )

    def test_overwrite_external(self):
        values, lay = layout(board(MCUBOOT_POLICY=SEL["overwrite"]))
        a = lay["areas"]
        self.assertEqual(lay["policy"], "overwrite-external")
        self.assertIsNone(lay["swap_mode"])
        self.assertEqual(a["secondary"]["size"], a["primary"]["size"])
        self.assertIn("--overwrite-only", lay["imgtool"]["sign_args"])
        self.assertEqual(values["MCUBOOT_OVERWRITE_ONLY"], 1)
        self.assertNotIn("MCUBOOT_SWAP_USING_OFFSET", values)
        # No status table: the flag words and the magic (3 * 16 + 16), the image up to them.
        self.assertEqual(lay["trailer_size"], 64)
        self.assertEqual(lay["max_image_size"], a["primary"]["size"] - 64)
        self.assertEqual(lay["dfu"]["update_addr"], a["secondary"]["addr"])

    def test_overwrite_external_secondary_may_be_external(self):
        _, lay = layout(
            board(MCUBOOT_POLICY=SEL["overwrite"], MCUBOOT_SECONDARY_ADDR="0x90000000", **SPI)
        )
        self.assertEqual(lay["areas"]["secondary"]["dev"], 1)
        self.assertEqual(lay["areas"]["secondary"]["size"], lay["areas"]["primary"]["size"])

    def test_single(self):
        values, lay = layout(board(MCUBOOT_POLICY=SEL["single"], MCUBOOT_SECONDARY_ADDR=None))
        a = lay["areas"]
        self.assertEqual(lay["policy"], "single")
        self.assertNotIn("secondary", a)
        self.assertNotIn("intent", a)
        self.assertEqual(values["MCUBOOT_SINGLE_APPLICATION_SLOT"], 1)
        # Flag words and magic (4 * 16 + 16); the update is written to the primary slot, which the
        # DFU front end names "Application".
        self.assertEqual(lay["trailer_size"], 80)
        self.assertEqual(lay["max_image_size"], a["primary"]["size"] - 80)
        self.assertEqual(lay["dfu"]["update_addr"], a["primary"]["addr"])
        # The slot holds no swap state: the DFU region is all of it, the image up to the reservation.
        self.assertEqual(lay["dfu"]["regions"][0]["size"], a["primary"]["size"])
        self.assertEqual(lay["app_len"], a["primary"]["size"] - 80 - 0x400 - 0x400)
        self.assertEqual(lay["dfu"]["regions"][0]["name"], "Application")
        # The filesystem runs to the end of the flash.
        self.assertEqual(a["fs"]["addr"] + a["fs"]["size"], 0x08200000)
        # The shadow area covers the one slot only.
        self.assertEqual(a["shadow"]["size"], 0x2000)

    def test_single_with_fsload_has_an_intent_area(self):
        _, lay = layout(
            board(
                MCUBOOT_POLICY=SEL["single"], MCUBOOT_SECONDARY_ADDR=None, MCUBOOT_FSLOAD_FAT="1"
            )
        )
        a = lay["areas"]
        self.assertEqual((a["intent"]["id"], a["intent"]["size"]), (7, 0x2000))
        self.assertEqual(a["intent"]["addr"], a["shadow"]["addr"] + a["shadow"]["size"])
        self.assertEqual(a["fs"]["addr"], a["intent"]["addr"] + a["intent"]["size"])

    def test_intent_placement_is_a_board_input(self):
        _, lay = layout(
            board(
                MCUBOOT_POLICY=SEL["single"],
                MCUBOOT_SECONDARY_ADDR=None,
                MCUBOOT_FSLOAD_FAT="1",
                MCUBOOT_INTENT_ADDR="0x081FE000",
                MCUBOOT_FS_ADDR="0x080C0000",
                MCUBOOT_FS_SIZE="0x100000",
            )
        )
        self.assertEqual(lay["areas"]["intent"]["addr"], 0x081FE000)

    def test_single_on_a_flash_without_ecc_has_no_shadow_area(self):
        values, lay = host_layout("single")
        self.assertNotIn("shadow", lay["areas"])
        self.assertEqual(lay["areas"]["seccnt"]["size"], 0x2000)

    def test_trailer_sizes_agree_with_bootutil(self):
        # MCUBOOT_TRAILER_SIZE against the trailer that bootutil_area.c sizes, for every policy and
        # swap mode, on the 16 byte write unit of the STM32H5 and on the 8 byte unit of a plain flash.
        h5 = {
            "offset": {},
            "move": {"MCUBOOT_SWAP_MODE": SEL["move"]},
            "overwrite": {"MCUBOOT_POLICY": SEL["overwrite"]},
            "single": {"MCUBOOT_POLICY": SEL["single"], "MCUBOOT_SECONDARY_ADDR": None},
        }
        for name, overrides in h5.items():
            with self.subTest(board="h5", mode=name):
                status_and_info, swap_info_end, ours = bootutil_trailer(board(**overrides))
                self.assertEqual(ours, swap_info_end if name == "overwrite" else status_and_info)
        for name in ("plain", "move", "scratch", "overwrite", "single"):
            with self.subTest(board=name):
                status_and_info, swap_info_end, ours = bootutil_trailer(
                    None, dev_dir=HOST_BOARDS / name, board_dir=HOST_BOARDS / name
                )
                self.assertEqual(ours, swap_info_end if name == "overwrite" else status_and_info)

    def test_hash_direct_with_a_mapped_device(self):
        values, lay = layout(board(MCUBOOT_SWAP_MODE=SEL["move"], MCUBOOT_HASH_DIRECT="1"))
        self.assertTrue(lay["hash_direct"])
        self.assertEqual(values["MCUBOOT_HASH_STORAGE_DIRECTLY"], 1)

    def test_validate_primary_choice_is_in_the_layout(self):
        values, lay = layout(board(MCUBOOT_VALIDATE_PRIMARY="0"))
        self.assertFalse(lay["validate_primary"])
        self.assertNotIn("MCUBOOT_VALIDATE_PRIMARY_SLOT", values)

    def test_make_variables_select_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, want in (
                ("move", ("swap", "move", "counter-flash")),
                ("single", ("single", "", "counter-flash")),
                ("overwrite", ("overwrite-external", "", "counter-flash")),
            ):
                cmd = [
                    CC,
                    "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                    "-I%s" % (HOST_BOARDS / name),
                ]
                lay = gen.build(gen.probe(cmd), name)
                mk = gen.render_mk(lay)
                self.assertIn("MCUBOOT_POLICY := %s\n" % want[0], mk)
                self.assertIn(
                    ("MCUBOOT_SWAP_MODE := %s\n" % want[1])
                    .replace(" \n", "\n")
                    .replace(" := \n", " :=\n"),
                    mk,
                )
                self.assertIn("MCUBOOT_ROLLBACK := %s\n" % want[2], mk)

    def test_linker_symbols_of_the_areas(self):
        lay = host_layout("single")[1]
        ld = gen.render_ld(lay)
        self.assertIn("MCUBOOT_INTENT_START = 0x", ld)
        self.assertNotIn("MCUBOOT_SECONDARY_START", ld)
        ld = gen.render_ld(host_layout("scratch")[1])
        self.assertIn("MCUBOOT_SCRATCH_START = 0x90050000;", ld)
        self.assertIn("MCUBOOT_SCRATCH_SIZE = 0x2000;", ld)


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestRefused(unittest.TestCase):
    """Configurations that the header turns into a preprocessor error."""

    def refused(self, message, **overrides):
        r = preprocess(board(**overrides))
        self.assertNotEqual(r.returncode, 0, "the configuration was accepted")
        self.assertIn(message, r.stderr)

    def accepted(self, **overrides):
        r = preprocess(board(**overrides))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_accepts_base(self):
        self.accepted()

    def test_missing_inputs(self):
        self.refused("MCUBOOT_PRIMARY_SIZE and MCUBOOT_SECONDARY_ADDR", MCUBOOT_PRIMARY_SIZE=None)
        self.refused(
            "MCUBOOT_PRIMARY_SIZE and MCUBOOT_SECONDARY_ADDR", MCUBOOT_SECONDARY_ADDR=None
        )
        self.refused("MCUBOOT_ROLLBACK_COUNTER", MCUBOOT_ROLLBACK_COUNTER=None)

    def test_unaligned(self):
        self.refused("multiple of the erase unit", MCUBOOT_SECONDARY_ADDR="0x08100010")
        self.refused("multiple of the erase unit", MCUBOOT_PRIMARY_SIZE="(640 * 1024 + 16)")
        self.refused("multiple of the erase unit", MCUBOOT_PRIMARY_ADDR="0x08010010")

    def test_overlaps(self):
        self.refused(
            "secondary slot overlaps the shadow area",
            MCUBOOT_SECONDARY_ADDR="0x080B4000",
            MCUBOOT_FS_ADDR="0x08160000",
            MCUBOOT_FS_SIZE="0x48000",
        )
        self.refused(
            "bootloader region overlaps the primary slot", MCUBOOT_PRIMARY_ADDR="0x08008000"
        )
        self.refused(
            "filesystem overlaps", MCUBOOT_FS_ADDR="0x080B0000", MCUBOOT_FS_SIZE="0x40000"
        )

    def test_outside_device(self):
        self.refused("does not fit in device 0", MCUBOOT_SECONDARY_ADDR="0x08180000")
        self.refused(
            "primary slot does not fit in device 0", MCUBOOT_PRIMARY_SIZE="(2 * 1024 * 1024)"
        )

    def test_filesystem_needs_size(self):
        self.refused("MCUBOOT_FS_ADDR needs MCUBOOT_FS_SIZE", MCUBOOT_FS_ADDR="0x080C0000")

    def test_header(self):
        self.refused("multiples of the vector table alignment", MCUBOOT_HEADER_SIZE="0x600")
        self.refused("multiples of the vector table alignment", MCUBOOT_HEADER_SIZE="0x10")
        self.accepted(MCUBOOT_HEADER_SIZE="0x800")

    def test_ecc_needs_dfu(self):
        self.refused("needs the DFU front end", MCUBOOT_DFU="0")

    def test_choices(self):
        self.refused("MCUBOOT_FIH_LEVEL", MCUBOOT_FIH_LEVEL="4")
        self.refused("MCUBOOT_LOG_LEVEL", MCUBOOT_LOG_LEVEL="5")
        for level in "0123":
            self.accepted(MCUBOOT_FIH_LEVEL=level)

    def test_spi_secondary(self):
        self.accepted(MCUBOOT_SECONDARY_ADDR="0x90000000", **SPI)

    def test_f769disc_spi_flash_cannot_hold_the_secondary_slot(self):
        # Its erase unit of 128 KiB is not the 8 KiB of the internal flash.
        spi = mboot_spiflash(F769DISC)
        self.refused(
            "erase unit of the secondary slot device",
            MCUBOOT_SECONDARY_ADDR=spi["MBOOT_SPIFLASH_ADDR"],
            **spi,
        )

    def test_spi_flash_detected_at_run_time_is_refused(self):
        # The SPI flash of a PYBD board is sized from a chip table at run time, so its size is not
        # a constant the layout can use.
        self.refused(
            "detected at run time",
            MCUBOOT_SECONDARY_ADDR="0x90000000",
            MBOOT_SPIFLASH_LAYOUT_DYNAMIC_MAX_LEN="(20)",
            **SPI,
        )

    def test_spi_erase_unit_has_to_match(self):
        self.refused(
            "erase unit of the secondary slot device",
            MCUBOOT_SECONDARY_ADDR="0x90000000",
            **dict(SPI, MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE="4"),
        )

    def test_spi_secondary_has_to_fit(self):
        self.refused(
            "does not fit in device 1",
            MCUBOOT_SECONDARY_ADDR="0x90F80000",
            **dict(SPI, MBOOT_SPIFLASH_BYTE_SIZE="(16 * 1024 * 1024)"),
        )

    def test_spi_device_without_ecc_and_unmapped(self):
        # A SPI flash is described by the port with ECC 0 and mapped 0: a port that says
        # otherwise is refused.
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp) / "mcuboot_dev.h"
            dev.write_text(
                stm32_dev_header().replace(
                    "#define MCUBOOT_DEV1_ECC 0", "#define MCUBOOT_DEV1_ECC 1"
                )
            )
            r = preprocess(board(MCUBOOT_SECONDARY_ADDR="0x90000000", **SPI), dev_dir=tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("device 1 has no ECC", r.stderr)

    def test_filesystem_has_to_fit_its_device(self):
        self.refused(
            "filesystem does not fit in device 0",
            MCUBOOT_FS_ADDR="0x081C0000",
            MCUBOOT_FS_SIZE="0x80000",
        )
        self.refused(
            "filesystem does not fit in device 1",
            MCUBOOT_SECONDARY_ADDR="0x90000000",
            MCUBOOT_FS_ADDR="0x90F00000",
            MCUBOOT_FS_SIZE="0x200000",
            **SPI,
        )

    def test_filesystem_cannot_overlap_the_secondary_slot(self):
        self.refused(
            "filesystem overlaps the secondary slot",
            MCUBOOT_FS_ADDR="0x08100000",
            MCUBOOT_FS_SIZE="0x10000",
        )
        self.refused(
            "filesystem overlaps the secondary slot",
            MCUBOOT_SECONDARY_ADDR="0x90000000",
            MCUBOOT_FS_ADDR="0x90010000",
            MCUBOOT_FS_SIZE="0x10000",
            **SPI,
        )
        self.accepted(
            MCUBOOT_SECONDARY_ADDR="0x90000000",
            MCUBOOT_FS_ADDR="0x90100000",
            MCUBOOT_FS_SIZE="0x10000",
            **SPI,
        )

    def dev_header(self, old, new):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp) / "mcuboot_dev.h"
            text = stm32_dev_header()
            self.assertIn(old, text)
            dev.write_text(text.replace(old, new))
            return preprocess(board(MCUBOOT_SECONDARY_ADDR="0x90000000", **SPI), dev_dir=tmp)

    def test_spi_secondary_write_unit_has_to_equal_the_trailer_alignment(self):
        r = self.dev_header(
            "#define MCUBOOT_DEV1_WRITE MCUBOOT_DEV0_WRITE", "#define MCUBOOT_DEV1_WRITE 8u"
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("write unit of a secondary slot on device 1", r.stderr)

    def test_spi_erase_unit_is_a_multiple_of_the_erase_block(self):
        r = self.dev_header(
            "#define MCUBOOT_DEV1_ERASE (MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE * 4096u)",
            "#define MCUBOOT_DEV1_ERASE 0x2800u",
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("4 KiB erase block", r.stderr)

    # ---- slot policies and swap modes ----

    def refused_with(self, message, extra=(), dev_dir=None, **overrides):
        r = preprocess(board(**overrides), extra=extra, dev_dir=dev_dir)
        self.assertNotEqual(r.returncode, 0, "the configuration was accepted")
        self.assertIn(message, r.stderr)

    def single(self, **overrides):
        return dict(MCUBOOT_POLICY=SEL["single"], MCUBOOT_SECONDARY_ADDR=None, **overrides)

    def test_unknown_policy_and_mode(self):
        self.refused("MCUBOOT_POLICY is MCUBOOT_POLICY_SEL_SWAP", MCUBOOT_POLICY="7")
        self.refused("MCUBOOT_SWAP_MODE is MCUBOOT_SWAP_MODE_SEL_OFFSET", MCUBOOT_SWAP_MODE="7")
        self.refused("MCUBOOT_CRYPTO is MCUBOOT_CRYPTO_SEL_TINYCRYPT", MCUBOOT_CRYPTO="7")

    def test_swap_mode_needs_the_swap_policy(self):
        self.refused(
            "MCUBOOT_SWAP_MODE only applies to",
            MCUBOOT_POLICY=SEL["overwrite"],
            MCUBOOT_SWAP_MODE=SEL["move"],
        )

    def test_swap_modes_accepted(self):
        for mode in ("offset", "move"):
            self.accepted(MCUBOOT_SWAP_MODE=SEL[mode])
        self.accepted(MCUBOOT_POLICY=SEL["overwrite"])
        self.accepted(**self.single())

    def test_secondary_slot_follows_the_mode(self):
        # The secondary slot size follows the primary slot size and the mode. A secondary slot
        # that starts inside the areas behind the primary slot, or runs past the device, is refused.
        self.refused(
            "secondary slot overlaps the shadow area",
            MCUBOOT_SWAP_MODE=SEL["move"],
            MCUBOOT_SECONDARY_ADDR="0x080B4000",
            MCUBOOT_FS_ADDR="0x08160000",
            MCUBOOT_FS_SIZE="0x48000",
        )
        self.refused(
            "does not fit in device 0",
            MCUBOOT_POLICY=SEL["overwrite"],
            MCUBOOT_SECONDARY_ADDR="0x08170000",
        )
        self.accepted(MCUBOOT_POLICY=SEL["overwrite"], MCUBOOT_SECONDARY_ADDR="0x08160000")

    def test_move_needs_two_erase_units(self):
        self.refused(
            "two erase units", MCUBOOT_SWAP_MODE=SEL["move"], MCUBOOT_PRIMARY_SIZE="0x2000"
        )

    def test_single_has_one_slot(self):
        self.refused(
            "MCUBOOT_SECONDARY_ADDR must not be defined",
            MCUBOOT_POLICY=SEL["single"],
        )

    def test_single_needs_a_counter(self):
        # A version comparison against the primary slot cannot run after the only slot has been
        # overwritten.
        self.refused_with(
            "needs MCUBOOT_ROLLBACK_COUNTER 1", **self.single(MCUBOOT_ROLLBACK_COUNTER="0")
        )
        self.accepted(**self.single(MCUBOOT_ROLLBACK_COUNTER="1"))

    def test_version_check_only_without_a_counter(self):
        self.refused("MCUBOOT_VERSION_CHECK only applies", MCUBOOT_VERSION_CHECK="0")
        values, _ = layout(board(MCUBOOT_ROLLBACK_COUNTER="0", MCUBOOT_VERSION_CHECK="0"))
        self.assertNotIn("MCUBOOT_DOWNGRADE_PREVENTION", values)
        values, _ = layout(board(MCUBOOT_ROLLBACK_COUNTER="0"))
        self.assertEqual(values["MCUBOOT_DOWNGRADE_PREVENTION"], 1)

    def test_single_filesystem_may_use_the_rest_of_the_flash(self):
        self.accepted(**self.single(MCUBOOT_FS_ADDR="0x080C0000", MCUBOOT_FS_SIZE="0x100000"))
        self.refused(
            "filesystem does not fit in device 0",
            **self.single(MCUBOOT_FS_ADDR="0x08100000", MCUBOOT_FS_SIZE="0x200000"),
        )

    def test_intent_area(self):
        fs = dict(MCUBOOT_FSLOAD_FAT="1")
        self.refused_with(
            "MCUBOOT_INTENT_ADDR only applies",
            MCUBOOT_INTENT_ADDR="0x080C0000",
            **self.single(),
        )
        self.refused(
            "MCUBOOT_INTENT_ADDR only applies",
            MCUBOOT_INTENT_ADDR="0x080C0000",
            MCUBOOT_FSLOAD_FAT="1",
        )
        self.refused_with(
            "primary slot overlaps the intent area",
            **self.single(
                MCUBOOT_INTENT_ADDR="0x08010000",
                MCUBOOT_FS_ADDR="0x080C0000",
                MCUBOOT_FS_SIZE="0x40000",
                **fs,
            ),
        )
        self.refused_with(
            "intent area has to lie in one run of erase units and start at a multiple",
            **self.single(
                MCUBOOT_INTENT_ADDR="0x081FE010",
                MCUBOOT_FS_ADDR="0x080C0000",
                MCUBOOT_FS_SIZE="0x40000",
                **fs,
            ),
        )
        self.refused_with(
            "intent area overlaps",
            **self.single(
                MCUBOOT_INTENT_ADDR="0x080C0000",
                MCUBOOT_FS_ADDR="0x080C0000",
                MCUBOOT_FS_SIZE="0x40000",
                **fs,
            ),
        )

    def test_scratch_area(self):
        # Swap using scratch needs a flash without ECC: the swap status in the scratch area is not
        # covered by the shadow words.
        self.refused(
            "swap using scratch is not supported on a device with ECC",
            MCUBOOT_SWAP_MODE=SEL["scratch"],
        )
        self.refused(
            "MCUBOOT_SCRATCH_ADDR and MCUBOOT_SCRATCH_SIZE only apply",
            MCUBOOT_SCRATCH_SIZE="0x4000",
        )

    def scratch_board(self, defs):
        """Text of the preprocessor result for the host scratch board with more definitions."""
        with tempfile.TemporaryDirectory() as tmp:
            for f in ("mcuboot_dev.h", "mpconfigboard.h"):
                shutil.copy(HOST_BOARDS / "scratch" / f, tmp)
            with open(Path(tmp) / "mpconfigboard.h", "a") as f:
                f.write("".join("#define %s %s\n" % kv for kv in defs.items()))
            return subprocess.run(
                [
                    CC,
                    "-E",
                    "-P",
                    "-x",
                    "c",
                    "-",
                    "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                    "-I%s" % tmp,
                ],
                input='#include "mcuboot_layout.h"\n',
                capture_output=True,
                text=True,
            )

    def test_scratch_area_rules(self):
        self.assertEqual(self.scratch_board({}).returncode, 0)
        r = self.scratch_board(
            {"MCUBOOT_SCRATCH_SIZE": "0x1000", "MCUBOOT_SCRATCH_ADDR": "0x900E0000"}
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        for defs, message in (
            ({"MCUBOOT_SCRATCH_SIZE": "0x800"}, "at least one erase unit"),
            ({"MCUBOOT_SCRATCH_SIZE": "0x1800"}, "multiple of the erase unit"),
            ({"MCUBOOT_SCRATCH_ADDR": "0x900E0800"}, "multiple of the erase unit"),
            (
                {"MCUBOOT_SCRATCH_ADDR": "0x90020000", "MCUBOOT_SCRATCH_SIZE": "0x2000"},
                "primary slot overlaps the scratch area",
            ),
            (
                {"MCUBOOT_SCRATCH_ADDR": "0x90000000", "MCUBOOT_SCRATCH_SIZE": "0x2000"},
                "bootloader region overlaps the scratch area",
            ),
            (
                {"MCUBOOT_SCRATCH_ADDR": "0x90090000", "MCUBOOT_SCRATCH_SIZE": "0x2000"},
                "secondary slot overlaps the scratch area",
            ),
            (
                {"MCUBOOT_SCRATCH_ADDR": "0x900FF000", "MCUBOOT_SCRATCH_SIZE": "0x2000"},
                "does not fit in device 0",
            ),
        ):
            with self.subTest(defs=defs):
                r = self.scratch_board(defs)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)

    def test_hash_direct(self):
        self.refused_with("not supported with swap using offset", **dict(MCUBOOT_HASH_DIRECT="1"))
        self.refused_with(
            "cannot hash the fsload stream area",
            **dict(MCUBOOT_SWAP_MODE=SEL["move"], MCUBOOT_HASH_DIRECT="1", MCUBOOT_FSLOAD_FAT="1"),
        )
        self.accepted(MCUBOOT_SWAP_MODE=SEL["move"], MCUBOOT_HASH_DIRECT="1")
        self.accepted(MCUBOOT_POLICY=SEL["overwrite"], MCUBOOT_HASH_DIRECT="1")

    def test_hash_direct_needs_mapped_devices(self):
        # The SPI flash is not memory mapped: a secondary slot there cannot be hashed in place.
        self.refused(
            "memory mapped slot devices",
            MCUBOOT_POLICY=SEL["overwrite"],
            MCUBOOT_SECONDARY_ADDR="0x90000000",
            MCUBOOT_HASH_DIRECT="1",
            **SPI,
        )

    def test_validate_primary_zero(self):
        # Without it the bootloader starts whatever the primary slot holds. Refused for the single
        # slot policy and in a production build, a warning otherwise.
        self.refused_with(
            "VALIDATE_PRIMARY 0 is not allowed with MCUBOOT_POLICY_SEL_SINGLE",
            **self.single(MCUBOOT_VALIDATE_PRIMARY="0"),
        )
        self.refused_with(
            "refused in a production build",
            extra=["-DMCUBOOT_PRODUCTION=1"],
            MCUBOOT_VALIDATE_PRIMARY="0",
        )
        r = preprocess(board(MCUBOOT_VALIDATE_PRIMARY="0"), extra=["-DMCUBOOT_PRODUCTION=0"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("starts without a signature check", r.stderr)
        r = preprocess(board(), extra=["-DMCUBOOT_PRODUCTION=1"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("signature check", r.stderr)

    def test_primary_cannot_be_on_device_1(self):
        self.refused(
            "primary slot has to be in device 0",
            MCUBOOT_PRIMARY_ADDR="0x90000000",
            MCUBOOT_SECONDARY_ADDR="0x90100000",
            **SPI,
        )


PYBD_SF6 = TOP / "ports" / "stm32" / "boards" / "PYBD_SF6"


def board_layout(board_dir, dev_dir=None, extra=(), cpp=()):
    """(values, layout) of a board directory that holds mpconfigboard.h and, unless dev_dir is
    given, mcuboot_dev.h."""
    cmd = [
        CC,
        "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
        "-I%s" % board_dir,
        "-I%s" % (dev_dir or board_dir),
        *extra,
        *cpp,
    ]
    values = gen.probe(cmd)
    return values, gen.build(values, Path(board_dir).name)


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestRuns(unittest.TestCase):
    """Devices of runs of erase units, as the flash of the STM32F7 has them: every area lies in one
    run and has the erase unit of that run."""

    # The shipped PYBD_SF6 configuration: single slot policy, bootloader in the first two 32 KiB
    # sectors, the next two unused, the intent area in the 128 KiB sector, the primary slot in five
    # 256 KiB sectors, the counter in the last two, the filesystem on the SPI flash.
    F7 = {
        "MCUBOOT_POLICY": "(MCUBOOT_POLICY_SEL_SINGLE)",
        "MCUBOOT_PRIMARY_ADDR": "(0x08040000)",
        "MCUBOOT_PRIMARY_SIZE": "(5 * 0x40000)",
        "MCUBOOT_INTENT_ADDR": "(0x08020000)",
        "MCUBOOT_SECCNT_ADDR": "(0x08180000)",
        "MCUBOOT_ROLLBACK_COUNTER": "(1)",
        "MCUBOOT_FSLOAD_FAT": "(1)",
        "MCUBOOT_FS_ADDR": "(0x80000000)",
        "MCUBOOT_FS_SIZE": "(2 * 1024 * 1024)",
    }
    DEV_F7 = (HOST_BOARDS / "f7" / "mcuboot_dev.h").read_text()

    def f7_board(self, defs=None, dev=()):
        """Preprocess the layout header over the F7 model with changed board defines (None removes
        one) and textual changes of the device description. Returns the process."""
        d = dict(self.F7)
        d.update(defs or {})
        d = {k: v for k, v in d.items() if v is not None}
        text = self.DEV_F7
        for old, new in dev:
            self.assertIn(old, text)
            text = text.replace(old, new)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mcuboot_dev.h").write_text(text)
            return preprocess(d, dev_dir=tmp, extra=["-Wall", "-Wextra", "-Werror"], cpp=())

    def refused(self, message, defs=None, dev=()):
        r = self.f7_board(defs, dev)
        self.assertNotEqual(r.returncode, 0, "the configuration was accepted")
        self.assertIn(message, r.stderr)

    def test_shipped_configuration_is_accepted_without_warnings(self):
        # The SPI flash base 0x80000000 is above the range of a signed 32 bit value; the lookups
        # are clean with -Wall -Wextra -Werror.
        r = self.f7_board()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_areas_take_the_unit_of_their_run(self):
        _, lay = board_layout(HOST_BOARDS / "f7")
        a = lay["areas"]
        units = {name: (area["addr"], area["size"], area["erase"]) for name, area in a.items()}
        self.assertEqual(
            units,
            {
                "boot": (0x08000000, 0x10000, 0x8000),
                "intent": (0x08020000, 0x20000, 0x20000),
                "primary": (0x08040000, 0x140000, 0x40000),
                "seccnt": (0x08180000, 0x80000, 0x40000),
                "fs": (0x80000000, 0x200000, 0x1000),
            },
        )
        self.assertEqual(
            lay["devices"][0]["runs"],
            [
                {"off": 0, "size": 0x20000, "erase": 0x8000},
                {"off": 0x20000, "size": 0x20000, "erase": 0x20000},
                {"off": 0x40000, "size": 0x1C0000, "erase": 0x40000},
            ],
        )
        self.assertEqual(
            lay["devices"][1]["runs"], [{"off": 0, "size": 0x200000, "erase": 0x1000}]
        )
        self.assertEqual(
            sum(r["size"] for r in lay["devices"][0]["runs"]), lay["devices"][0]["size"]
        )

    def test_single_slot_holds_the_whole_slot_but_the_reservation(self):
        # One slot of five 256 KiB sectors: the image runs up to the 48 byte trailer reservation,
        # the DFU region and the application link region are the slot.
        _, lay = board_layout(HOST_BOARDS / "f7")
        self.assertEqual(lay["trailer_size"], 48)
        self.assertEqual(lay["max_image_size"], 5 * 0x40000 - 48)
        self.assertEqual(lay["dfu"]["regions"][0]["size"], 5 * 0x40000)
        self.assertEqual(lay["dfu"]["write_align"], 4)
        self.assertEqual(lay["app_len"], 5 * 0x40000 - 48 - 0x400 - 0x400)
        self.assertEqual((lay["policy"], lay["rollback"]), ("single", "counter-flash"))

    def test_the_real_board_is_the_host_model(self):
        # PYBD_SF6 with the device description of the stm32 port gives the layout of the host test
        # board f7 (same areas, same units, same layout id).
        cpp = cmsis_flags("F7xx", "STM32F767xx") + ["-I%s" % (TOP / "ports" / "stm32")]
        _, real = board_layout(PYBD_SF6, dev_dir=STM32_DEV, cpp=cpp)
        _, model = board_layout(HOST_BOARDS / "f7")
        self.assertEqual(real["areas"], model["areas"])
        self.assertEqual(real["layout_id"], model["layout_id"])
        self.assertEqual(real["devices"][0]["runs"], model["devices"][0]["runs"])
        self.assertEqual(real["devices"][0]["write"], 4)
        self.assertEqual(real["devices"][0]["ecc"], 0)

    def test_offset_and_scratch_models(self):
        _, off = board_layout(HOST_BOARDS / "f7_offset")
        a = off["areas"]
        self.assertEqual(
            (a["primary"]["size"], a["secondary"]["size"]), (3 * 0x40000, 4 * 0x40000)
        )
        self.assertEqual(a["secondary"]["addr"] + a["secondary"]["size"], 0x08200000)
        self.assertEqual({a[n]["erase"] for n in ("primary", "secondary")}, {0x40000})
        self.assertEqual(off["max_image_size"], 2 * 0x40000)
        self.assertEqual(off["min_image_size"], 0x40001)
        self.assertEqual(off["dfu"]["update_addr"], a["secondary"]["addr"] + 0x40000)
        _, scr = board_layout(HOST_BOARDS / "f7_scratch")
        a = scr["areas"]
        self.assertEqual((a["scratch"]["addr"], a["scratch"]["size"]), (0x081C0000, 0x40000))
        self.assertEqual(scr["max_image_size"], 2 * 0x40000)
        self.assertEqual(scr["min_image_size"], 0)

    def test_min_image_size_only_for_swap_using_offset(self):
        self.assertEqual(layout(board())[1]["min_image_size"], 0x2001)
        self.assertEqual(layout(board(MCUBOOT_SWAP_MODE=SEL["move"]))[1]["min_image_size"], 0)
        self.assertEqual(layout(board(MCUBOOT_POLICY=SEL["overwrite"]))[1]["min_image_size"], 0)

    # ---- the description of a device ----

    def test_erase_and_runs_exclude_each_other(self):
        self.refused(
            "gives the erase units of device 0 twice",
            dev=[
                (
                    "#define MCUBOOT_DEV0_WRITE",
                    "#define MCUBOOT_DEV0_ERASE 0x8000u\n#define MCUBOOT_DEV0_WRITE",
                )
            ],
        )

    def test_erase_or_runs_is_needed(self):
        self.refused(
            "does not give the erase units of device 0",
            dev=[("#define MCUBOOT_DEV0_RUNS 3\n", "")],
        )

    def test_number_of_runs(self):
        self.refused(
            "one to four runs",
            dev=[("#define MCUBOOT_DEV0_RUNS 3", "#define MCUBOOT_DEV0_RUNS 5")],
        )
        self.refused(
            "size and the erase unit of every run of device 0",
            dev=[("#define MCUBOOT_DEV0_RUNS 3", "#define MCUBOOT_DEV0_RUNS 4")],
        )

    def test_runs_add_up_to_the_device(self):
        self.refused(
            "do not add up to its size",
            dev=[("#define MCUBOOT_DEV0_SIZE 0x200000u", "#define MCUBOOT_DEV0_SIZE 0x300000u")],
        )

    def test_runs_are_whole_power_of_two_units_starting_on_a_unit(self):
        message = "every run of device 0 has to hold whole erase units"
        self.refused(
            message,
            dev=[
                (
                    "#define MCUBOOT_DEV0_RUN2_ERASE 0x40000u",
                    "#define MCUBOOT_DEV0_RUN2_ERASE 0x30000u",
                )
            ],
        )
        # A run of 128 KiB units behind three 32 KiB sectors starts inside a unit.
        self.refused(
            message,
            dev=[
                (
                    "#define MCUBOOT_DEV0_RUN0_SIZE (4 * 0x8000u)",
                    "#define MCUBOOT_DEV0_RUN0_SIZE (3 * 0x8000u)",
                ),
                (
                    "#define MCUBOOT_DEV0_RUN2_SIZE (7 * 0x40000u)",
                    "#define MCUBOOT_DEV0_RUN2_SIZE (7 * 0x40000u + 0x8000u)",
                ),
            ],
        )
        self.refused(
            message,
            dev=[
                ("#define MCUBOOT_DEV0_RUN0_ERASE 0x8000u", "#define MCUBOOT_DEV0_RUN0_ERASE 2u")
            ],
        )

    def test_uniform_device_is_one_run(self):
        _, lay = layout(board())
        self.assertEqual(
            lay["devices"][0]["runs"], [{"off": 0, "size": 0x200000, "erase": 0x2000}]
        )

    # ---- every area in one run, on a unit ----

    def test_area_has_to_lie_in_one_run(self):
        # From the 128 KiB sector into the 256 KiB sectors.
        self.refused(
            "primary slot has to lie in one run",
            {
                "MCUBOOT_PRIMARY_ADDR": "(0x08020000)",
                "MCUBOOT_PRIMARY_SIZE": "(0x140000)",
                "MCUBOOT_INTENT_ADDR": "(0x08160000)",
            },
        )
        self.refused(
            "bootloader region has to lie in one run",
            dev=[("#define MCUBOOT_BOOT_SIZE 0x10000u", "#define MCUBOOT_BOOT_SIZE 0x30000u")],
        )

    def test_area_starts_on_a_unit_of_its_run(self):
        # 0x08050000 is 0x10000 into the 256 KiB sectors.
        self.refused(
            "primary slot has to lie in one run of erase units, start at and be a multiple of the erase unit",
            {"MCUBOOT_PRIMARY_ADDR": "(0x08050000)", "MCUBOOT_PRIMARY_SIZE": "(0x100000)"},
        )

    def test_area_size_is_a_multiple_of_the_unit_of_its_run(self):
        self.refused(
            "primary slot has to lie in one run",
            {"MCUBOOT_PRIMARY_SIZE": "(5 * 0x40000 + 0x8000)"},
        )
        self.refused(
            "bootloader region has to lie in one run",
            dev=[("#define MCUBOOT_BOOT_SIZE 0x10000u", "#define MCUBOOT_BOOT_SIZE 0xC000u")],
        )

    def test_the_default_filesystem_cannot_span_runs(self):
        # Without MCUBOOT_FS_ADDR the filesystem fills the space between the auxiliaries and the
        # secondary slot, so this layout is refused when that space crosses an erase-run boundary.
        self.refused(
            "filesystem has to lie in one run",
            {
                "MCUBOOT_POLICY": None,
                "MCUBOOT_INTENT_ADDR": None,
                "MCUBOOT_FSLOAD_FAT": None,
                "MCUBOOT_SECCNT_ADDR": None,
                "MCUBOOT_ROLLBACK_COUNTER": "(0)",
                "MCUBOOT_PRIMARY_SIZE": "(0x80000)",
                "MCUBOOT_SECONDARY_ADDR": "(0x08140000)",
                "MCUBOOT_FS_ADDR": None,
                "MCUBOOT_FS_SIZE": None,
            },
            self.four_runs(0x08100000, run3_erase=0x40000),
        )

    def test_the_filesystem_has_room(self):
        # The single policy puts the default filesystem behind the counter, which ends the flash.
        self.refused(
            "the filesystem has no room",
            {"MCUBOOT_FS_ADDR": None, "MCUBOOT_FS_SIZE": None},
        )

    # ---- slots, scratch and shadow share a unit ----

    # A device of four runs: four 32 KiB sectors, one of 128 KiB, then 256 KiB sectors up to
    # 0x08140000 or 0x08180000, then 128 KiB sectors.
    def four_runs(self, big_end, run3_erase=0x20000):
        return [
            (
                "#define MCUBOOT_DEV0_RUN2_SIZE (7 * 0x40000u)",
                "#define MCUBOOT_DEV0_RUN2_SIZE %#xu" % (big_end - 0x08040000),
            ),
            ("#define MCUBOOT_DEV0_RUNS 3", "#define MCUBOOT_DEV0_RUNS 4"),
            (
                "#define MCUBOOT_DEV0_WRITE",
                "#define MCUBOOT_DEV0_RUN3_SIZE %#xu\n#define MCUBOOT_DEV0_RUN3_ERASE %#xu\n#define MCUBOOT_DEV0_WRITE"
                % (0x08200000 - big_end, run3_erase),
            ),
        ]

    def swap_defs(self, **kv):
        d = {
            "MCUBOOT_POLICY": None,
            "MCUBOOT_INTENT_ADDR": None,
            "MCUBOOT_FSLOAD_FAT": None,
            "MCUBOOT_SECCNT_ADDR": None,
            "MCUBOOT_ROLLBACK_COUNTER": "(0)",
            "MCUBOOT_PRIMARY_ADDR": "(0x08040000)",
            "MCUBOOT_PRIMARY_SIZE": "(0x80000)",
        }
        d.update(kv)
        return d

    def test_slots_have_one_erase_unit(self):
        # The primary slot is in the 256 KiB sectors, the secondary slot in the 128 KiB sectors.
        defs = self.swap_defs(MCUBOOT_SECONDARY_ADDR="(0x080C0000)")
        self.refused(
            "erase unit of the secondary slot device differs from the one of the primary slot",
            defs,
            dev=self.four_runs(0x080C0000),
        )
        # Both in the 256 KiB sectors.
        r = self.f7_board(defs)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_scratch_has_the_erase_unit_of_the_slots(self):
        # Slots of two 256 KiB sectors and a scratch area in the 128 KiB sectors.
        defs = self.swap_defs(
            MCUBOOT_SECONDARY_ADDR="(0x080C0000)",
            MCUBOOT_SWAP_MODE="(MCUBOOT_SWAP_MODE_SEL_SCRATCH)",
            MCUBOOT_SCRATCH_ADDR="(0x08180000)",
            MCUBOOT_SCRATCH_SIZE="(0x20000)",
        )
        dev = self.four_runs(0x08180000)
        self.refused("scratch area needs the erase unit of the slots", defs, dev=dev)
        defs["MCUBOOT_SCRATCH_ADDR"] = "(0x08140000)"
        defs["MCUBOOT_SCRATCH_SIZE"] = "(0x40000)"
        r = self.f7_board(defs, dev)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_shadow_has_the_erase_unit_of_the_slots(self):
        # A device with ECC and 16 byte words: the shadow area is one sector for every sector of the
        # trailers of both slots, so it takes the unit of the slots.
        dev = [
            ("#define MCUBOOT_DEV0_WRITE 4u", "#define MCUBOOT_DEV0_WRITE 16u"),
            ("#define MCUBOOT_DEV0_ECC 0", "#define MCUBOOT_DEV0_ECC 1"),
            ("#define MCUBOOT_DEV1_WRITE MCUBOOT_DEV0_WRITE", "#define MCUBOOT_DEV1_WRITE 16u"),
        ]
        defs = self.swap_defs(
            MCUBOOT_POLICY="(MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL)",
            MCUBOOT_SECONDARY_ADDR="(0x080C0000)",
            MCUBOOT_SHADOW_ADDR="(0x08010000)",
        )
        self.refused("shadow area needs the erase unit of the slots", defs, dev=dev)
        defs["MCUBOOT_SHADOW_ADDR"] = "(0x08140000)"
        r = self.f7_board(defs, dev)
        self.assertEqual(r.returncode, 0, r.stderr)

    # ---- explicit areas ----

    def test_no_two_areas_overlap(self):
        for defs, message in (
            ({"MCUBOOT_SECCNT_ADDR": "(0x08100000)"}, "primary slot overlaps the counter area"),
            ({"MCUBOOT_INTENT_ADDR": "(0x08180000)"}, "counter area overlaps the intent area"),
            (
                {"MCUBOOT_FS_ADDR": "(0x08180000)", "MCUBOOT_FS_SIZE": "(0x40000)"},
                "filesystem overlaps the counter area",
            ),
        ):
            with self.subTest(message=message):
                self.refused(message, defs)

    def test_counter_uses_two_units_of_its_run(self):
        _, lay = board_layout(HOST_BOARDS / "f7")
        self.assertEqual(lay["areas"]["seccnt"]["size"], 2 * 0x40000)

    def test_explicit_counter_and_shadow_placement(self):
        _, lay = layout(board(MCUBOOT_SECCNT_ADDR="0x080C0000"))
        a = lay["areas"]
        self.assertEqual((a["seccnt"]["addr"], a["shadow"]["addr"]), (0x080C0000, 0x080C4000))
        _, lay = layout(board(MCUBOOT_SECCNT_ADDR="0x080E0000", MCUBOOT_SHADOW_ADDR="0x080F0000"))
        a = lay["areas"]
        self.assertEqual((a["seccnt"]["addr"], a["shadow"]["addr"]), (0x080E0000, 0x080F0000))

    def test_explicit_counter_and_shadow_need_their_feature(self):
        r = preprocess(board(MCUBOOT_SECCNT_ADDR="0x080E0000", MCUBOOT_ROLLBACK_COUNTER="0"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MCUBOOT_SECCNT_ADDR needs MCUBOOT_ROLLBACK_COUNTER 1", r.stderr)
        r = preprocess(board(MCUBOOT_SECCNT_ADDR="0x080E0000", MCUBOOT_SECCNT_PORT="1"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MCUBOOT_SECCNT_ADDR needs", r.stderr)
        # A flash without ECC has no shadow area.
        with tempfile.TemporaryDirectory() as tmp:
            for f in ("mcuboot_dev.h", "mpconfigboard.h"):
                shutil.copy(HOST_BOARDS / "plain" / f, tmp)
            with open(Path(tmp) / "mpconfigboard.h", "a") as f:
                f.write("#define MCUBOOT_SHADOW_ADDR 0x900E0000\n")
            r = subprocess.run(
                [
                    CC,
                    "-E",
                    "-P",
                    "-x",
                    "c",
                    "-",
                    "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                    "-I%s" % tmp,
                ],
                input='#include "mcuboot_layout.h"\n',
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MCUBOOT_SHADOW_ADDR only applies to a device 0 with ECC", r.stderr)

    def test_layout_id_follows_explicit_areas_but_not_the_defaults(self):
        base = layout(board())[1]["layout_id"]
        # The counter immediately after the primary slot is where it is without the input.
        self.assertEqual(base, layout(board(MCUBOOT_SECCNT_ADDR="0x080B0000"))[1]["layout_id"])
        self.assertEqual(base, layout(board(MCUBOOT_SHADOW_ADDR="0x080B4000"))[1]["layout_id"])
        moved = layout(board(MCUBOOT_SECCNT_ADDR="0x080E0000"))[1]["layout_id"]
        self.assertNotEqual(base, moved)
        self.assertNotEqual(base, layout(board(MCUBOOT_SHADOW_ADDR="0x080E0000"))[1]["layout_id"])
        self.assertNotEqual(moved, layout(board(MCUBOOT_SECCNT_ADDR="0x080E4000"))[1]["layout_id"])

    def test_layout_id_matches_c_for_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "id.c"
            src.write_text(
                '#include <stdio.h>\n#include <stdint.h>\n#include "mcuboot_layout.h"\n'
                'int main(void) { printf("%08x\\n", (unsigned)MCUBOOT_LAYOUT_ID); return 0; }\n'
            )
            exe = Path(tmp) / "id"
            subprocess.run(
                [
                    CC,
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                    "-I%s" % (HOST_BOARDS / "f7"),
                    str(src),
                    "-o",
                    str(exe),
                ],
                check=True,
            )
            c_id = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(board_layout(HOST_BOARDS / "f7")[1]["layout_id"], c_id.strip())

    def test_unit_lookups_in_c(self):
        # The macros that stand for the run table agree with it at run time for every area.
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "u.c"
            src.write_text(
                '#include <stdio.h>\n#include <stdint.h>\n#include "mcuboot_layout.h"\n'
                "int main(void) {\n"
                '  printf("%u %u %u %u\\n", (unsigned)MCUBOOT_BOOT_UNIT, (unsigned)MCUBOOT_INTENT_UNIT,\n'
                "    (unsigned)MCUBOOT_SLOT_UNIT, (unsigned)MCUBOOT_SECCNT_UNIT);\n"
                "  return 0; }\n"
            )
            exe = Path(tmp) / "u"
            subprocess.run(
                [
                    CC,
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                    "-I%s" % (HOST_BOARDS / "f7"),
                    str(src),
                    "-o",
                    str(exe),
                ],
                check=True,
            )
            out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.split(), ["32768", "131072", "262144", "262144"])


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestSignMinimumSize(unittest.TestCase):
    """tools/mcuboot_sign.py does not make an image that swap using offset cannot swap safely: at
    most one erase unit of the slot."""

    def sign(self, tmp, board, payload_len, command="sign"):
        layout_json = Path(tmp) / "layout"
        r = subprocess.run(
            [
                sys.executable,
                str(TOP / "tools" / "mcuboot_gen.py"),
                "--out",
                str(layout_json),
                "--board",
                board,
                "--key",
                str(KEY),
                "--",
                CC,
                "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                "-I%s" % (HOST_BOARDS / board),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = Path(tmp) / "raw.bin"
        raw.write_bytes(bytes(i & 0xFF for i in range(payload_len)))
        out = Path(tmp) / "signed.bin"
        return subprocess.run(
            [
                sys.executable,
                str(TOP / "tools" / "mcuboot_sign.py"),
                command,
                "--layout",
                str(layout_json / "mcuboot_layout.json"),
                "-k",
                str(KEY),
                str(raw),
                str(out),
            ],
            capture_output=True,
            text=True,
        ), out

    def test_one_unit_image_is_refused_with_swap_using_offset(self):
        # plain: swap using offset, 4 KiB sectors; header 0x400 and the TLVs add about 0x200.
        for command in ("sign", "initial"):
            with tempfile.TemporaryDirectory() as tmp:
                r, out = self.sign(tmp, "plain", 2048, command)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("at most one erase unit", r.stderr)
                self.assertFalse(out.exists())

    def test_larger_image_is_made(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, out = self.sign(tmp, "plain", 8000)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.exists())

    def test_other_modes_take_a_small_image(self):
        for board in ("move", "scratch", "overwrite", "single"):
            with self.subTest(board=board), tempfile.TemporaryDirectory() as tmp:
                r, out = self.sign(tmp, board, 2048)
                self.assertEqual(r.returncode, 0, r.stderr)


class TestEvaluate(unittest.TestCase):
    def test_expressions(self):
        self.assertEqual(gen.evaluate("(0x08000000u + 0x10000u)"), 0x08010000)
        self.assertEqual(gen.evaluate("(((640) * 1024) / 8192u)"), 80)
        expect = ((0x811C9DC5 ^ 7) * 16777619) & 0xFFFFFFFF
        self.assertEqual(gen.evaluate("(0xFFFFFFFFu & ((0x811C9DC5u ^ 7u) * 16777619u))"), expect)
        self.assertEqual(gen.evaluate("~0u & 0xFFu"), 0xFF)
        # The conditional operator and comparisons, as the lookups of the erase unit by address use them.
        self.assertEqual(
            gen.evaluate("((0x08040000) >= (0x80000000) ? (4096u) : (0x40000UL))"), 0x40000
        )
        self.assertEqual(gen.evaluate("(5 < 3 ? 1 : 7 > 6 ? 2 : 3)"), 2)
        self.assertEqual(gen.evaluate("(1 != 1 || 2 == 2) && !0"), 1)
        self.assertEqual(gen.evaluate("-7 / 2 + -7 % 4"), -6)

    def test_refuses_code(self):
        with self.assertRaises(ValueError):
            gen.evaluate("__import__('os')")
        for bad in ("1 +", "(1", "1 2", "1 ? 2"):
            with self.assertRaises(ValueError):
                gen.evaluate(bad)


@unittest.skipIf(CC is None, "needs a host C compiler")
class TestGenerator(unittest.TestCase):
    def run_gen(self, out, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(TOP / "tools" / "mcuboot_gen.py"),
                "--out",
                str(out),
                "--board",
                "NUCLEO_H563ZI",
                *extra,
                "--",
                CC,
                "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
                "-I%s" % STM32_BOARD,
                "-I%s" % STM32_DEV,
                *STM32_CPP,
            ],
            capture_output=True,
            text=True,
        )

    def test_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_gen(tmp, "--key", str(KEY))
            self.assertEqual(r.returncode, 0, r.stderr)
            ld = (Path(tmp) / "mcuboot_layout.ld").read_text()
            self.assertIn("MCUBOOT_PRIMARY_START = 0x08010000;", ld)
            self.assertIn("MCUBOOT_FS_START = 0x080B8000;", ld)
            mk = (Path(tmp) / "mcuboot_layout.mk").read_text()
            self.assertIn("MCUBOOT_APP_LINK_ADDR := 0x08010400", mk)
            self.assertIn("MCUBOOT_DFU_WRITE_ALIGN := 16", mk)
            doc = json.loads((Path(tmp) / "mcuboot_layout.json").read_text())
            self.assertEqual(doc["keys"]["files"], [str(KEY)])
            self.assertEqual(len(doc["keys"]["pub_sha256"][0]), 64)
            keys_c = (Path(tmp) / "mcuboot_keys_gen.c").read_text()
            self.assertIn("bootutil_key_cnt = 1", keys_c)

    def test_preprocessor_failure(self):
        r = subprocess.run(
            [
                sys.executable,
                str(TOP / "tools" / "mcuboot_gen.py"),
                "--out",
                "/nonexistent-dir-for-test",
                "--",
                CC,
                "-I%s" % (TOP / "shared" / "mcuboot" / "include"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stderr, re.compile("mpconfigboard.h|preprocessor"))


if __name__ == "__main__":
    unittest.main()
