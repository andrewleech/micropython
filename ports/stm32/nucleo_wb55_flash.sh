#!/bin/bash
# Flash script for NUCLEO_WB55 board
# Usage: ./nucleo_wb55_flash.sh [firmware.elf]

set -e

CHIP="STM32WB55RG"
PROBE="0483:374b"
FIRMWARE="${1:-build-NUCLEO_WB55/firmware.elf}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$FIRMWARE" ]; then
    FIRMWARE="$SCRIPT_DIR/$FIRMWARE"
fi

if [ ! -f "$FIRMWARE" ]; then
    echo "Error: Firmware file not found: $FIRMWARE"
    exit 1
fi

echo "================================================"
echo "Flashing NUCLEO_WB55 (STM32WB55RG)"
echo "================================================"
echo "Probe:    ST-Link V2-1 ($PROBE)"
echo "Chip:     $CHIP"
echo "Firmware: $FIRMWARE"
echo ""

# Display firmware size
echo "Firmware size:"
arm-none-eabi-size "$FIRMWARE"
echo ""

# Flash with verification
echo "Flashing firmware..."
probe-rs download --chip "$CHIP" --probe "$PROBE" --verify "$FIRMWARE"

# Reset target
echo ""
echo "Resetting target..."
probe-rs reset --chip "$CHIP" --probe "$PROBE"

echo ""
echo "================================================"
echo "Flash complete! Device is running."
echo "================================================"
