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

"""Wire-protocol contract tests for tools/pydfu.py.

Verifies that pydfu issues the correct ctrl_transfer arguments for:
  - mass_erase() in default (pure DFU 1.1 / vendor-erase) mode
  - page_erase(addr) in default mode
  - mass_erase() and page_erase(addr) in --dfuse (legacy DfuSe) mode

pyusb is not required: usb.core and usb.util are mocked via sys.modules
before pydfu is imported so that no USB calls are made.
"""

import os
import struct
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Mock pyusb so pydfu.py imports cleanly without the real library installed.
# pydfu.py uses:
#   usb.core.find(...)
#   usb.util.get_string(dev, index)
#   usb.util.claim_interface(dev, intf)
#   usb.util.dispose_resources(dev)
#   inspect.getfullargspec(usb.util.get_string)  — at import time
# ---------------------------------------------------------------------------

_usb_mock = types.ModuleType("usb")
_usb_core = types.ModuleType("usb.core")
_usb_util = types.ModuleType("usb.util")
_usb_mock.core = _usb_core
_usb_mock.util = _usb_util
_usb_core.find = lambda *a, **kw: []
_usb_util.get_string = lambda dev, index: ""
_usb_util.claim_interface = lambda dev, intf: None
_usb_util.dispose_resources = lambda dev: None
sys.modules.setdefault("usb", _usb_mock)
sys.modules.setdefault("usb.core", _usb_core)
sys.modules.setdefault("usb.util", _usb_util)

# Add the MicroPython tools/ directory to sys.path so pydfu is importable.
# This file lives in shared/tinyusb/mboot/tests/; tools/ is four levels up.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import pydfu  # noqa: E402  (must come after sys.path and usb mock)

# ---------------------------------------------------------------------------
# DFU constants (mirrored from pydfu.py for assertion clarity).
# ---------------------------------------------------------------------------

_DFU_DNLOAD = 1
_DFU_GETSTATUS = 3
_DFU_STATE_DFU_DOWNLOAD_BUSY = 0x04
_DFU_STATE_DFU_DOWNLOAD_IDLE = 0x05
_DFU_INTERFACE = 0

# Vendor-erase opcode (pure DFU 1.1 / shared/tinyusb/mboot).
_MBOOT_VREQ_ERASE = 0x80

# DfuSe command bytes (embedded in the DNLOAD payload, not in bRequest).
_DFUSE_CMD_ERASE = 0x41
_DFUSE_CMD_SET_ADDRESS = 0x21

# Region used by page_erase tests.
_TEST_BASE = 0x60000000
_TEST_PAGE_SIZE = 4096


# ---------------------------------------------------------------------------
# Helper — recording stub for ctrl_transfer.
# ---------------------------------------------------------------------------


class _RecordingDev:
    """Minimal USB device stub that records ctrl_transfer calls.

    In DfuSe mode pydfu calls check_status() after each DNLOAD, which
    issues two consecutive GETSTATUS ctrl_transfer calls expecting:
      1st GETSTATUS -> state == DOWNLOAD_BUSY
      2nd GETSTATUS -> state == DOWNLOAD_IDLE
    The stub tracks GETSTATUS call count independently to return the
    correct synthetic state for each.  All other requests return [].
    """

    def __init__(self):
        self.calls = []
        self._getstatus_count = 0

    def ctrl_transfer(self, bmRequestType, bRequest, wValue, wIndex, data, timeout):
        self.calls.append((bmRequestType, bRequest, wValue, wIndex, data))
        if bRequest == _DFU_GETSTATUS:
            # DfuSe erase/set-address each call check_status twice:
            #   odd-numbered GETSTATUS -> DOWNLOAD_BUSY
            #   even-numbered GETSTATUS -> DOWNLOAD_IDLE
            self._getstatus_count += 1
            if self._getstatus_count % 2 == 1:
                state = _DFU_STATE_DFU_DOWNLOAD_BUSY
            else:
                state = _DFU_STATE_DFU_DOWNLOAD_IDLE
            return [0, 0, 0, 0, state, 0]
        return []


def _install_dev(dev):
    """Write *dev* into pydfu's module-level __dev global."""
    pydfu.__dict__["__dev"] = dev


def _set_dfuse(flag):
    """Set pydfu's module-level __dfuse flag."""
    pydfu.__dict__["__dfuse"] = flag


def _seed_mem_layout():
    """Seed pydfu's __mem_layout with a single 4 KB-page region at _TEST_BASE."""
    pydfu.__dict__["__mem_layout"] = [
        {
            "addr": _TEST_BASE,
            "last_addr": _TEST_BASE + _TEST_PAGE_SIZE * 16 - 1,
            "size": _TEST_PAGE_SIZE * 16,
            "num_pages": 16,
            "page_size": _TEST_PAGE_SIZE,
        }
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMassEraseDefault(unittest.TestCase):
    """mass_erase() in default (pure DFU 1.1) mode."""

    def setUp(self):
        _set_dfuse(False)
        self.dev = _RecordingDev()
        _install_dev(self.dev)

    def test_single_ctrl_transfer(self):
        pydfu.mass_erase()
        self.assertEqual(len(self.dev.calls), 1)

    def test_bmRequestType(self):
        pydfu.mass_erase()
        self.assertEqual(self.dev.calls[0][0], 0x41)

    def test_bRequest(self):
        pydfu.mass_erase()
        self.assertEqual(self.dev.calls[0][1], _MBOOT_VREQ_ERASE)

    def test_wValue_zero(self):
        pydfu.mass_erase()
        self.assertEqual(self.dev.calls[0][2], 0)

    def test_wIndex_dfu_interface(self):
        pydfu.mass_erase()
        self.assertEqual(self.dev.calls[0][3], _DFU_INTERFACE)

    def test_payload_8_bytes(self):
        pydfu.mass_erase()
        data = self.dev.calls[0][4]
        self.assertEqual(len(data), 8)

    def test_payload_addr_zero(self):
        pydfu.mass_erase()
        addr, _ = struct.unpack("<II", self.dev.calls[0][4])
        self.assertEqual(addr, 0)

    def test_payload_length_sentinel(self):
        """Length field must be 0xFFFFFFFF to signal mass-erase."""
        pydfu.mass_erase()
        _, length = struct.unpack("<II", self.dev.calls[0][4])
        self.assertEqual(length, 0xFFFFFFFF)


class TestPageEraseDefault(unittest.TestCase):
    """page_erase(addr) in default (pure DFU 1.1) mode."""

    def setUp(self):
        _set_dfuse(False)
        self.dev = _RecordingDev()
        _install_dev(self.dev)
        _seed_mem_layout()

    def test_single_ctrl_transfer(self):
        pydfu.page_erase(_TEST_BASE)
        self.assertEqual(len(self.dev.calls), 1)

    def test_bmRequestType(self):
        pydfu.page_erase(_TEST_BASE)
        self.assertEqual(self.dev.calls[0][0], 0x41)

    def test_bRequest(self):
        pydfu.page_erase(_TEST_BASE)
        self.assertEqual(self.dev.calls[0][1], _MBOOT_VREQ_ERASE)

    def test_payload_addr_le(self):
        pydfu.page_erase(_TEST_BASE)
        addr, _ = struct.unpack("<II", self.dev.calls[0][4])
        self.assertEqual(addr, _TEST_BASE)

    def test_payload_page_size_le(self):
        pydfu.page_erase(_TEST_BASE)
        _, ps = struct.unpack("<II", self.dev.calls[0][4])
        self.assertEqual(ps, _TEST_PAGE_SIZE)


class TestMassEraseDfuSe(unittest.TestCase):
    """mass_erase() in legacy --dfuse (DfuSe) mode.

    The DfuSe path must use bmRequestType=0x21, bRequest=DFU_DNLOAD(1),
    wValue=0, wIndex=0, with a one-byte payload starting with 0x41
    (_DFUSE_CMD_ERASE).
    """

    def setUp(self):
        _set_dfuse(True)
        self.dev = _RecordingDev()
        _install_dev(self.dev)

    def tearDown(self):
        _set_dfuse(False)

    def test_bmRequestType(self):
        pydfu.mass_erase()
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        self.assertGreater(len(dnload_calls), 0)
        self.assertEqual(dnload_calls[0][0], 0x21)

    def test_bRequest_dnload(self):
        pydfu.mass_erase()
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        self.assertEqual(dnload_calls[0][1], _DFU_DNLOAD)

    def test_payload_first_byte_erase_cmd(self):
        """First byte of DfuSe DNLOAD payload must be 0x41 (erase command)."""
        pydfu.mass_erase()
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        payload = dnload_calls[0][4]
        # pydfu passes a string "\x41" (Python 2 compat); normalise to int.
        first_byte = payload[0] if isinstance(payload[0], int) else ord(payload[0])
        self.assertEqual(first_byte, _DFUSE_CMD_ERASE)


class TestPageEraseDfuSe(unittest.TestCase):
    """page_erase(addr) in legacy --dfuse (DfuSe) mode.

    The DfuSe path must use bmRequestType=0x21, bRequest=DFU_DNLOAD(1),
    with a 5-byte payload: struct.pack('<BI', 0x41, addr).
    """

    def setUp(self):
        _set_dfuse(True)
        self.dev = _RecordingDev()
        _install_dev(self.dev)

    def tearDown(self):
        _set_dfuse(False)

    def test_bmRequestType(self):
        pydfu.page_erase(_TEST_BASE)
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        self.assertGreater(len(dnload_calls), 0)
        self.assertEqual(dnload_calls[0][0], 0x21)

    def test_payload_starts_with_erase_cmd(self):
        pydfu.page_erase(_TEST_BASE)
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        payload = bytes(dnload_calls[0][4])
        self.assertEqual(payload[0], _DFUSE_CMD_ERASE)

    def test_payload_encodes_address(self):
        pydfu.page_erase(_TEST_BASE)
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        payload = bytes(dnload_calls[0][4])
        cmd, addr = struct.unpack("<BI", payload)
        self.assertEqual(addr, _TEST_BASE)


if __name__ == "__main__":
    unittest.main()
