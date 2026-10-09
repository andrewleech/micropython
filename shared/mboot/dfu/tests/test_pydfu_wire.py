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

"""Wire-protocol tests for tools/pydfu.py.

Checks the ctrl_transfer arguments pydfu sends for:
  - mass_erase() and page_erase(addr) in default mode (pure DFU 1.1, vendor erase)
  - mass_erase() and page_erase(addr) in --dfuse mode
  - exit_dfu(), which checks the manifest result and resets the bus (pure DFU 1.1)

usb.core and usb.util are mocked in sys.modules before pydfu is imported, so
pyusb is not needed and no USB calls are made.
"""

import os
import struct
import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Mock pyusb. pydfu.py uses:
#   usb.core.find(...)
#   usb.util.get_string(dev, index)
#   usb.util.claim_interface(dev, intf)
#   usb.util.dispose_resources(dev)
#   inspect.getfullargspec(usb.util.get_string)  - at import time
# ---------------------------------------------------------------------------

_usb_mock = types.ModuleType("usb")
_usb_core = types.ModuleType("usb.core")
_usb_util = types.ModuleType("usb.util")
_usb_mock.core = _usb_core
_usb_mock.util = _usb_util
_usb_core.find = lambda *a, **kw: []
_usb_core.USBError = type("USBError", (Exception,), {})
_usb_util.get_string = lambda dev, index: ""
_usb_util.claim_interface = lambda dev, intf: None
_usb_util.dispose_resources = lambda dev: None
sys.modules.setdefault("usb", _usb_mock)
sys.modules.setdefault("usb.core", _usb_core)
sys.modules.setdefault("usb.util", _usb_util)

# pydfu is in tools/, four levels up from this file.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import pydfu  # noqa: E402  (must come after sys.path and usb mock)

# ---------------------------------------------------------------------------
# DFU constants (the same values as in pydfu.py).
# ---------------------------------------------------------------------------

_DFU_DNLOAD = 1
_DFU_GETSTATUS = 3
_DFU_STATE_DFU_DOWNLOAD_BUSY = 0x04
_DFU_STATE_DFU_DOWNLOAD_IDLE = 0x05
_DFU_INTERFACE = 0

# Vendor erase opcode (pure DFU 1.1, shared/tinyusb/mboot).
_MBOOT_VREQ_ERASE = 0x80

# DfuSe command bytes, sent in the DNLOAD payload.
_DFUSE_CMD_ERASE = 0x41
_DFUSE_CMD_SET_ADDRESS = 0x21

# Region for the page_erase tests.
_TEST_BASE = 0x60000000
_TEST_PAGE_SIZE = 4096


# ---------------------------------------------------------------------------
# Recording stub for ctrl_transfer.
# ---------------------------------------------------------------------------


class _RecordingDev:
    """USB device stub that records ctrl_transfer calls.

    In DfuSe mode pydfu calls check_status() after each DNLOAD, which sends
    two GETSTATUS requests and expects:
      1st GETSTATUS -> state == DOWNLOAD_BUSY
      2nd GETSTATUS -> state == DOWNLOAD_IDLE
    The stub counts GETSTATUS calls to return the right state for each.
    Every other request returns [].
    """

    def __init__(self):
        self.calls = []
        self._getstatus_count = 0

    def ctrl_transfer(self, bmRequestType, bRequest, wValue, wIndex, data, timeout):
        self.calls.append((bmRequestType, bRequest, wValue, wIndex, data))
        if bRequest == _DFU_GETSTATUS:
            # Odd GETSTATUS -> DOWNLOAD_BUSY, even -> DOWNLOAD_IDLE.
            self._getstatus_count += 1
            if self._getstatus_count % 2 == 1:
                state = _DFU_STATE_DFU_DOWNLOAD_BUSY
            else:
                state = _DFU_STATE_DFU_DOWNLOAD_IDLE
            return [0, 0, 0, 0, state, 0]
        return []


def _install_dev(dev):
    """Set pydfu's module-level __dev."""
    pydfu.__dict__["__dev"] = dev


def _set_dfuse(flag):
    """Set pydfu's module-level __dfuse."""
    pydfu.__dict__["__dfuse"] = flag


def _seed_mem_layout():
    """Give pydfu's __mem_layout a single region of 4 KB pages at _TEST_BASE."""
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
    """mass_erase() in default mode (pure DFU 1.1)."""

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
        """A length of 0xFFFFFFFF means mass erase."""
        pydfu.mass_erase()
        _, length = struct.unpack("<II", self.dev.calls[0][4])
        self.assertEqual(length, 0xFFFFFFFF)


class TestPageEraseDefault(unittest.TestCase):
    """page_erase(addr) in default mode (pure DFU 1.1)."""

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
    """mass_erase() in --dfuse mode.

    Sends bmRequestType=0x21, bRequest=DFU_DNLOAD(1), wValue=0, wIndex=0, with
    a payload that starts with 0x41 (_DFUSE_CMD_ERASE).
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
        """The first payload byte is 0x41, the erase command."""
        pydfu.mass_erase()
        dnload_calls = [c for c in self.dev.calls if c[1] == _DFU_DNLOAD]
        payload = dnload_calls[0][4]
        # pydfu passes the string "\x41" (Python 2 compatibility), so convert to int.
        first_byte = payload[0] if isinstance(payload[0], int) else ord(payload[0])
        self.assertEqual(first_byte, _DFUSE_CMD_ERASE)


class TestPageEraseDfuSe(unittest.TestCase):
    """page_erase(addr) in --dfuse mode.

    Sends bmRequestType=0x21, bRequest=DFU_DNLOAD(1), with a 5-byte payload:
    struct.pack('<BI', 0x41, addr).
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


class _ScriptedDev:
    """Device stub that answers GETSTATUS from a list of (status, state) and counts reset() calls."""

    def __init__(self, answers):
        self.calls = []
        self.answers = list(answers)
        self.reset_calls = 0

    def ctrl_transfer(self, bmRequestType, bRequest, wValue, wIndex, data, timeout):
        self.calls.append((bmRequestType, bRequest, wValue, wIndex, data))
        if bRequest == _DFU_GETSTATUS:
            status, state = self.answers.pop(0)
            return [status, 0, 0, 0, state, 0]
        return []

    def reset(self):
        self.reset_calls += 1


_DFU_STATE_MANIFEST = 7
_DFU_STATE_MANIFEST_WAIT_RESET = 8
_DFU_STATE_ERROR = 10


class TestExitDfu(unittest.TestCase):
    """exit_dfu() in default mode (pure DFU 1.1): manifest, result check, bus reset."""

    def setUp(self):
        _set_dfuse(False)

    def test_accepted_image_resets_the_bus(self):
        dev = _ScriptedDev([(0, _DFU_STATE_MANIFEST), (0, _DFU_STATE_MANIFEST_WAIT_RESET)])
        _install_dev(dev)
        pydfu.exit_dfu()
        # A zero-length DNLOAD of block 0, two status requests, then the reset.
        self.assertEqual(dev.calls[0][1], _DFU_DNLOAD)
        self.assertEqual(dev.calls[0][2], 0)
        self.assertFalse(dev.calls[0][4])
        self.assertEqual([c[1] for c in dev.calls[1:]], [_DFU_GETSTATUS, _DFU_GETSTATUS])
        self.assertEqual(dev.reset_calls, 1)

    def test_rejected_image_is_reported_and_not_reset(self):
        dev = _ScriptedDev([(0, _DFU_STATE_MANIFEST), (0x02, _DFU_STATE_ERROR)])
        _install_dev(dev)
        with self.assertRaises(SystemExit) as cm:
            pydfu.exit_dfu()
        self.assertIn("0x02", str(cm.exception))
        self.assertEqual(dev.reset_calls, 0)

    def test_dfuse_device_is_not_reset_by_the_host(self):
        # A DfuSe device resets itself after the first status request.
        _set_dfuse(True)
        dev = _ScriptedDev([(0, _DFU_STATE_MANIFEST)])
        _install_dev(dev)
        pydfu.exit_dfu()
        self.assertEqual(len(dev.calls), 2)
        self.assertEqual(dev.reset_calls, 0)


if __name__ == "__main__":
    unittest.main()
