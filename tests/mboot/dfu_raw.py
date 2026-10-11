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

"""Raw DFU 1.1 client for the MCUboot bootloader (pyusb).

Sends arbitrary DFU_DNLOAD blocks (wBlockNum and payload chosen by the caller, unlike dfu-util and
pydfu which derive them from the alt string), DFU control requests and the two MCUboot vendor
requests (0x80 erase, 0x81 result), and reports the DFU status of every request. The hardware tests
of DFU write and erase confinement (hw/dfu_confine.py) use it. It also runs from the command line:

  dfu_raw.py [--serial UID] list
  dfu_raw.py [--serial UID] info                  device, DFU functional descriptor, alt strings
  dfu_raw.py [--serial UID] status | state | abort | clrstatus
  dfu_raw.py [--serial UID] [--alt N] dnload BLOCK (--file F | --hex HEX | --fill BYTE:LEN)
  dfu_raw.py [--serial UID] [--alt N] erase ADDR LENGTH     (LENGTH 0xFFFFFFFF = mass erase)
  dfu_raw.py [--serial UID] result                vendor request 0x81, decoded
  dfu_raw.py [--serial UID] [--alt N] upload BLOCK LENGTH

The device is found by VID/PID (default f055:dfa5) and, if given, the iSerial string, so other DFU
devices on the host are never touched.
"""

import argparse
import struct
import sys
import time

import usb.core
import usb.util

DFU_INTERFACE = 0
DFU_DNLOAD = 1
DFU_UPLOAD = 2
DFU_GETSTATUS = 3
DFU_CLRSTATUS = 4
DFU_GETSTATE = 5
DFU_ABORT = 6
VREQ_ERASE = 0x80
VREQ_RESULT = 0x81

STATE_NAMES = {
    0: "appIDLE",
    1: "appDETACH",
    2: "dfuIDLE",
    3: "dfuDNLOAD-SYNC",
    4: "dfuDNBUSY",
    5: "dfuDNLOAD-IDLE",
    6: "dfuMANIFEST-SYNC",
    7: "dfuMANIFEST",
    8: "dfuMANIFEST-WAIT-RESET",
    9: "dfuUPLOAD-IDLE",
    10: "dfuERROR",
}
STATUS_NAMES = {
    0x00: "OK",
    0x01: "errTARGET",
    0x02: "errFILE",
    0x03: "errWRITE",
    0x04: "errERASE",
    0x05: "errCHECK_ERASED",
    0x06: "errPROG",
    0x07: "errVERIFY",
    0x08: "errADDRESS",
    0x09: "errNOTDONE",
    0x0A: "errFIRMWARE",
    0x0B: "errVENDOR",
    0x0C: "errUSBR",
    0x0D: "errPOR",
    0x0E: "errUNKNOWN",
    0x0F: "errSTALLEDPKT",
}
# mboot_result_t (shared/mboot/include/mboot_types.h)
RESULT_NAMES = {
    0: "OK",
    1: "ERR_FLASH",
    2: "ERR_HEADER",
    3: "ERR_HASH",
    4: "ERR_SIG",
    5: "ERR_DOWNGRADE",
    6: "ERR_TOO_BIG",
    7: "ERR_NOT_TARGET",
    8: "ERR_PENDING",
    9: "ERR_LAYOUT",
    10: "ERR_NO_IMAGE",
    11: "ERR_REQUEST",
    12: "ERR_TOO_SMALL",
}
PHASE_NAMES = {0: "NONE", 1: "BEGIN", 2: "VALIDATE", 3: "PENDING"}

DEFAULT_TIMEOUT_MS = 8000


class Stall(Exception):
    """A control request was answered with a STALL."""


def state_name(s):
    return STATE_NAMES.get(s, "state%d" % s)


def status_name(s):
    return STATUS_NAMES.get(s, "status0x%02x" % s)


def find_devices(vid=0xF055, pid=0xDFA5, serial=None):
    """All matching devices; serial None matches any iSerial."""
    found = []
    for d in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
        try:
            s = d.serial_number
        except (usb.core.USBError, ValueError):
            s = None
        if serial is None or s == serial:
            found.append(d)
    return found


class DfuRaw:
    def __init__(
        self, vid=0xF055, pid=0xDFA5, serial=None, timeout_ms=DEFAULT_TIMEOUT_MS, wait_s=0
    ):
        self.timeout = timeout_ms
        t0 = time.time()
        while True:
            devs = find_devices(vid, pid, serial)
            if devs or time.time() - t0 >= wait_s:
                break
            time.sleep(0.1)
        if not devs:
            raise RuntimeError("no DFU device %04x:%04x serial %s" % (vid, pid, serial))
        if len(devs) > 1:
            raise RuntimeError("%d DFU devices match, give --serial" % len(devs))
        self.dev = devs[0]
        self.dev.set_configuration()
        usb.util.claim_interface(self.dev, DFU_INTERFACE)
        self.alt = 0

    def close(self):
        try:
            usb.util.dispose_resources(self.dev)
        except usb.core.USBError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---- control helpers ----

    def _ctrl(self, rt, req, value, data_or_len, timeout=None):
        """Control transfer; a stall raises Stall, any other USB error propagates."""
        try:
            return self.dev.ctrl_transfer(
                rt, req, value, DFU_INTERFACE, data_or_len, timeout or self.timeout
            )
        except usb.core.USBError as e:
            if e.errno == 32 or "pipe" in str(e).lower():
                raise Stall("request 0x%02x wValue 0x%04x stalled" % (req, value))
            raise

    def set_alt(self, alt):
        self.dev.set_interface_altsetting(interface=DFU_INTERFACE, alternate_setting=alt)
        self.alt = alt

    def alt_strings(self):
        out = []
        for cfg in self.dev:
            for intf in cfg:
                out.append(
                    (
                        intf.bInterfaceNumber,
                        intf.bAlternateSetting,
                        usb.util.get_string(self.dev, intf.iInterface),
                    )
                )
        return out

    def dfu_descriptor(self):
        """(bmAttributes, wDetachTimeOut, wTransferSize, bcdDFUVersion) of the DFU functional descriptor."""
        for cfg in self.dev.configurations():
            for extra in [cfg.extra_descriptors] + [i.extra_descriptors for i in cfg.interfaces()]:
                b = bytes(extra)
                if len(b) >= 9 and b[0] == 9 and b[1] == 0x21:
                    return struct.unpack("<BHHH", b[2:9])
        return None

    def get_status(self):
        """(status, poll_timeout_ms, state, iString)."""
        b = bytes(self._ctrl(0xA1, DFU_GETSTATUS, 0, 6))
        return b[0], b[1] | (b[2] << 8) | (b[3] << 16), b[4], b[5]

    def get_state(self):
        return bytes(self._ctrl(0xA1, DFU_GETSTATE, 0, 1))[0]

    def clr_status(self):
        self._ctrl(0x21, DFU_CLRSTATUS, 0, None)

    def abort(self):
        self._ctrl(0x21, DFU_ABORT, 0, None)

    def to_idle(self):
        """Bring the device to dfuIDLE (abort or clear status as the state requires)."""
        for _ in range(6):
            status, _, state, _ = self.get_status()
            if state == 2:
                return status
            if state == 10:
                self.clr_status()
            else:
                self.abort()
        raise RuntimeError("cannot reach dfuIDLE")

    # ---- data requests ----

    def dnload_raw(self, block, data):
        """Send DFU_DNLOAD only; returns nothing (the status stage is up to the caller)."""
        self._ctrl(0x21, DFU_DNLOAD, block, bytes(data) if data is not None else None)

    def dnload(self, block, data, max_polls=2000):
        """DNLOAD one block and poll GETSTATUS until the device leaves dfuDNBUSY / dfuDNLOAD-SYNC.

        Returns (status, state) after the block was processed. A stalled DNLOAD returns
        (None, 'stall').
        """
        try:
            self.dnload_raw(block, data)
        except Stall:
            return None, "stall"
        for _ in range(max_polls):
            status, poll, state, _ = self.get_status()
            if state not in (3, 4):
                return status, state
            if poll > 0:
                time.sleep(min(poll, 20) / 1000.0)
        raise RuntimeError("DNLOAD block %d still busy" % block)

    def manifest(self, max_polls=4000):
        """Zero length DNLOAD plus GETSTATUS until dfuMANIFEST-WAIT-RESET or an error.

        Returns (status, state, seconds from the zero length DNLOAD to the final state).
        """
        t0 = time.time()
        self.dnload_raw(0, None)
        for _ in range(max_polls):
            status, poll, state, _ = self.get_status()
            if state in (8, 10, 2):
                return status, state, time.time() - t0
            if state == 6 and poll > 0:
                time.sleep(min(poll, 20) / 1000.0)
        raise RuntimeError("manifest did not finish")

    def erase(self, addr, length, alt=0):
        """Vendor request 0x80 with wValue = alt. Returns 'ok' or 'stall'."""
        try:
            self._ctrl(
                0x41, VREQ_ERASE, alt, struct.pack("<II", addr & 0xFFFFFFFF, length & 0xFFFFFFFF)
            )
        except Stall:
            return "stall"
        return "ok"

    def result(self):
        """Vendor request 0x81. Returns dict, or None if the request stalled."""
        try:
            b = bytes(self._ctrl(0xC1, VREQ_RESULT, 0, 16))
        except Stall:
            return None
        seq, code, source, phase, detail, reserved = struct.unpack("<IHBBII", b)
        return {
            "seq": seq,
            "code": code,
            "code_name": RESULT_NAMES.get(code, str(code)),
            "source": source,
            "phase": phase,
            "phase_name": PHASE_NAMES.get(phase, str(phase)),
            "detail": detail,
            "reserved": reserved,
        }

    def upload(self, block, length):
        """DFU_UPLOAD of one block; returns the bytes read or None on a stall."""
        try:
            return bytes(self._ctrl(0xA1, DFU_UPLOAD, block, length))
        except Stall:
            return None


def _data_arg(a):
    if a.file:
        return open(a.file, "rb").read()
    if a.hex:
        return bytes.fromhex(a.hex)
    if a.fill:
        byte, length = a.fill.split(":")
        return bytes([int(byte, 0)]) * int(length, 0)
    raise SystemExit("dnload needs --file, --hex or --fill")


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--vid", type=lambda x: int(x, 0), default=0xF055)
    p.add_argument("--pid", type=lambda x: int(x, 0), default=0xDFA5)
    p.add_argument("--serial", default=None, help="iSerial of the device (the chip unique id)")
    p.add_argument("--alt", type=int, default=0)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("info")
    sub.add_parser("status")
    sub.add_parser("state")
    sub.add_parser("abort")
    sub.add_parser("clrstatus")
    sub.add_parser("result")
    d = sub.add_parser("dnload")
    d.add_argument("block", type=lambda x: int(x, 0))
    d.add_argument("--file")
    d.add_argument("--hex")
    d.add_argument("--fill")
    e = sub.add_parser("erase")
    e.add_argument("addr", type=lambda x: int(x, 0))
    e.add_argument("length", type=lambda x: int(x, 0))
    u = sub.add_parser("upload")
    u.add_argument("block", type=lambda x: int(x, 0))
    u.add_argument("length", type=lambda x: int(x, 0))
    a = p.parse_args()

    if a.cmd == "list":
        for dev in find_devices(a.vid, a.pid, a.serial):
            print(
                "bus %d address %d port %s serial %s"
                % (dev.bus, dev.address, dev.port_numbers, dev.serial_number)
            )
        return 0

    with DfuRaw(a.vid, a.pid, a.serial) as dfu:
        if a.alt:
            dfu.set_alt(a.alt)
        if a.cmd == "info":
            print(
                "device %04x:%04x serial %s"
                % (dfu.dev.idVendor, dfu.dev.idProduct, dfu.dev.serial_number)
            )
            print("product %r manufacturer %r" % (dfu.dev.product, dfu.dev.manufacturer))
            desc = dfu.dfu_descriptor()
            if desc:
                print(
                    "DFU functional: bmAttributes 0x%02x wDetachTimeOut %d wTransferSize %d bcdDFUVersion 0x%04x"
                    % desc
                )
            for ifn, alt, s in dfu.alt_strings():
                print("interface %d alt %d: %s" % (ifn, alt, s))
        elif a.cmd == "status":
            st, poll, state, istr = dfu.get_status()
            print(
                "status %s poll %d ms state %s iString %d"
                % (status_name(st), poll, state_name(state), istr)
            )
        elif a.cmd == "state":
            print(state_name(dfu.get_state()))
        elif a.cmd == "abort":
            dfu.abort()
        elif a.cmd == "clrstatus":
            dfu.clr_status()
        elif a.cmd == "result":
            print(dfu.result())
        elif a.cmd == "dnload":
            st, state = dfu.dnload(a.block, _data_arg(a))
            print(
                "status %s state %s"
                % (
                    status_name(st) if st is not None else "-",
                    state if st is None else state_name(state),
                )
            )
        elif a.cmd == "erase":
            print(dfu.erase(a.addr, a.length, a.alt))
        elif a.cmd == "upload":
            data = dfu.upload(a.block, a.length)
            print("stall" if data is None else "%d bytes %s" % (len(data), data[:32].hex()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
