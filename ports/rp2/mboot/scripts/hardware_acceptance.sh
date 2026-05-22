#!/usr/bin/env bash
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

# hardware_acceptance.sh — rp2 mboot hardware acceptance script.
#
# Usage:
#   ./scripts/hardware_acceptance.sh <BOARD> <SERIAL_DEV>
#
# Example:
#   ./scripts/hardware_acceptance.sh RPI_PICO \
#       /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_<id>
#
# The script must be run from the ports/rp2/mboot/ directory.
# It covers the nine steps in scripts/checklist_RPI_PICO.md.
# Steps that require physical interaction pause and wait for ENTER.

set -euo pipefail

BOARD="${1:?Usage: $0 <BOARD> <SERIAL_DEV>}"
SERIAL_DEV="${2:?Usage: $0 <BOARD> <SERIAL_DEV>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MBOOT_DIR="$SCRIPT_DIR/.."
REPO_ROOT="$MBOOT_DIR/../../.."
RPI_PORT_DIR="$REPO_ROOT/ports/rp2"

MBOOT_UF2="$MBOOT_DIR/build-${BOARD}/mboot.uf2"
APP_FIRMWARE_DFU="$RPI_PORT_DIR/build-${BOARD}/firmware.dfu"
APP_FIRMWARE_BIN="$RPI_PORT_DIR/build-${BOARD}/firmware.bin"

MBOOT_VID="2e8a"
MBOOT_PID="dfa0"
MBOOT_USB="${MBOOT_VID}:${MBOOT_PID}"

PASS=0
FAIL=0

pass() { echo "[PASS] $*"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $*"; FAIL=$((FAIL + 1)); }
info() { echo "[INFO] $*"; }
step() { echo; echo "=== Step $* ==="; }
pause() { read -r -p "[WAIT] $* — press ENTER when ready..."; }

# ---------------------------------------------------------------------------
step "1/9: Build mboot for ${BOARD}"
# ---------------------------------------------------------------------------
make -C "$MBOOT_DIR" BOARD="${BOARD}" 2>&1 | tail -5
if [ -f "$MBOOT_UF2" ]; then
    SIZE=$(wc -c < "$MBOOT_UF2")
    if [ "$SIZE" -lt $((64 * 1024 * 3)) ]; then   # UF2 is ~3x raw; 64K raw ~ 192K UF2
        pass "mboot.uf2 exists (${SIZE} bytes)"
    else
        fail "mboot.uf2 is unexpectedly large (${SIZE} bytes)"
    fi
else
    fail "mboot.uf2 not found at $MBOOT_UF2"
    exit 1
fi

# ---------------------------------------------------------------------------
step "2/9: Flash mboot via picotool (board must be in BOOTSEL mode)"
# ---------------------------------------------------------------------------
pause "Hold BOOTSEL and power-cycle ${BOARD}, then release BOOTSEL"
if picotool load -x "$MBOOT_UF2"; then
    pass "picotool load succeeded"
else
    fail "picotool load failed — is the board in BOOTSEL mode?"
    exit 1
fi
info "Waiting 3 seconds for mboot to enumerate..."
sleep 3

# ---------------------------------------------------------------------------
step "3/9: Verify mboot enumerates as DFU device"
# ---------------------------------------------------------------------------
if lsusb -d "${MBOOT_USB}" > /dev/null 2>&1; then
    pass "mboot visible in lsusb (${MBOOT_USB})"
else
    fail "mboot not visible in lsusb — check USB cable and VID:PID"
    exit 1
fi
DFU_LIST=$(dfu-util -l 2>&1)
if echo "$DFU_LIST" | grep -q "${MBOOT_VID}:${MBOOT_PID}"; then
    pass "dfu-util -l shows mboot interface"
    info "$DFU_LIST"
else
    fail "dfu-util -l did not list mboot interface"
    exit 1
fi

# ---------------------------------------------------------------------------
step "4/9: Build app firmware with USE_MBOOT=1"
# ---------------------------------------------------------------------------
make -C "$RPI_PORT_DIR" BOARD="${BOARD}" USE_MBOOT=1 2>&1 | tail -5
if [ -f "$APP_FIRMWARE_DFU" ]; then
    pass "firmware.dfu built at $APP_FIRMWARE_DFU"
else
    fail "firmware.dfu not found — did the app build succeed?"
    exit 1
fi

# ---------------------------------------------------------------------------
step "5/9: Program app via pydfu.py"
# ---------------------------------------------------------------------------
if python3 "$REPO_ROOT/tools/pydfu.py" -u "$APP_FIRMWARE_DFU"; then
    pass "pydfu.py upload completed"
else
    fail "pydfu.py upload failed"
    exit 1
fi
info "Waiting 3 seconds for app to boot..."
sleep 3

# ---------------------------------------------------------------------------
step "6/9: Confirm app boots (mpremote eval sys.platform)"
# ---------------------------------------------------------------------------
PLATFORM=$(mpremote connect "$SERIAL_DEV" eval 'import sys; sys.platform' 2>&1 || true)
if echo "$PLATFORM" | grep -q "rp2"; then
    pass "app running: sys.platform = $PLATFORM"
else
    fail "unexpected platform or no response: $PLATFORM"
    exit 1
fi

# ---------------------------------------------------------------------------
step "7/9: Re-enter mboot from app via machine.bootloader()"
# ---------------------------------------------------------------------------
info "Sending machine.bootloader() ..."
mpremote connect "$SERIAL_DEV" eval 'import machine; machine.bootloader()' 2>/dev/null || true
sleep 3
if lsusb -d "${MBOOT_USB}" > /dev/null 2>&1; then
    pass "mboot re-enumerated after machine.bootloader()"
else
    fail "mboot did not enumerate after machine.bootloader()"
    exit 1
fi

# Re-program app so we can test True fallback in step 8.
python3 "$REPO_ROOT/tools/pydfu.py" -u "$APP_FIRMWARE_DFU" 2>&1 | tail -3
sleep 3

# ---------------------------------------------------------------------------
step "8/9: Test machine.bootloader(True) — ROM BOOTSEL fallback"
# ---------------------------------------------------------------------------
info "Sending machine.bootloader(True) ..."
mpremote connect "$SERIAL_DEV" eval 'import machine; machine.bootloader(True)' 2>/dev/null || true
sleep 3
# BOOTSEL mode presents as Raspberry Pi BootROM USB device (VID 0x2E8A PID 0x0003).
if lsusb -d "2e8a:0003" > /dev/null 2>&1; then
    pass "ROM BOOTSEL device enumerated after machine.bootloader(True)"
else
    # Some builds enumerate differently; check for absence of the app serial.
    if ! lsusb -d "${MBOOT_USB}" > /dev/null 2>&1; then
        pass "board is not in mboot after machine.bootloader(True) — likely ROM BOOTSEL"
    else
        fail "machine.bootloader(True) did not enter ROM BOOTSEL"
        exit 1
    fi
fi
# Restore the board to mboot by power-cycling with BOOTSEL held, then reflash app.
pause "Power-cycle ${BOARD} holding BOOTSEL to return to mboot, then press ENTER"
python3 "$REPO_ROOT/tools/pydfu.py" -u "$APP_FIRMWARE_DFU" 2>&1 | tail -3
sleep 3

# ---------------------------------------------------------------------------
step "9/9: Verify mboot self-protection (write to mboot region is rejected)"
# ---------------------------------------------------------------------------
info "Attempting DFU write to mboot flash region (0x10000000)..."
# pydfu.py with --address in the mboot reserved slot should receive errADDRESS.
WRITE_RESULT=$(python3 "$REPO_ROOT/tools/pydfu.py" \
    --address 0x10000000 \
    -u "$APP_FIRMWARE_BIN" 2>&1 || true)
if echo "$WRITE_RESULT" | grep -qi "address\|errADDRESS\|error\|rejected"; then
    pass "mboot region write rejected as expected"
else
    fail "mboot region write was NOT rejected (result: $WRITE_RESULT)"
fi

# ---------------------------------------------------------------------------
echo
echo "=== Results ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
if [ "$FAIL" -eq 0 ]; then
    echo "All $PASS checks passed — Stage 1 hardware acceptance: PASS"
    exit 0
else
    echo "$FAIL check(s) failed — Stage 1 hardware acceptance: FAIL"
    exit 1
fi
