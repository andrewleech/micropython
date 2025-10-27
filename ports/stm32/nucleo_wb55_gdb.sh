#!/bin/bash
# GDB debug script for NUCLEO_WB55 board
# Usage: ./nucleo_wb55_gdb.sh [start-server|attach|both]

set -e

CHIP="STM32WB55RG"
PROBE="0483:374b"
GDB_PORT="1337"
FIRMWARE="build-NUCLEO_WB55/firmware.elf"
DEBUG_SCRIPT="build-NUCLEO_WB55/debug.gdb"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="${1:-both}"

start_gdb_server() {
    echo "================================================"
    echo "Starting probe-rs GDB server"
    echo "================================================"
    echo "Chip:    $CHIP"
    echo "Probe:   ST-Link V2-1 ($PROBE)"
    echo "Port:    127.0.0.1:$GDB_PORT"
    echo ""
    echo "GDB server will remain running."
    echo "Press Ctrl+C to stop."
    echo "================================================"
    echo ""
    
    probe-rs gdb --chip "$CHIP" --probe "$PROBE" --gdb-connection-string "127.0.0.1:$GDB_PORT"
}

attach_gdb() {
    echo "================================================"
    echo "Attaching GDB to probe-rs server"
    echo "================================================"
    echo "Firmware: $FIRMWARE"
    echo "Script:   $DEBUG_SCRIPT"
    echo ""
    
    cd "$SCRIPT_DIR"
    
    if [ -f "$DEBUG_SCRIPT" ]; then
        arm-none-eabi-gdb -x "$DEBUG_SCRIPT" "$FIRMWARE"
    else
        echo "No debug script found, starting plain GDB..."
        arm-none-eabi-gdb "$FIRMWARE" -ex "target extended-remote :$GDB_PORT" -ex "monitor reset halt"
    fi
}

case "$MODE" in
    start-server)
        start_gdb_server
        ;;
    attach)
        attach_gdb
        ;;
    both)
        echo "Starting GDB server in background..."
        probe-rs gdb --chip "$CHIP" --probe "$PROBE" --gdb-connection-string "127.0.0.1:$GDB_PORT" > /tmp/probe-rs-gdb-server.log 2>&1 &
        GDB_SERVER_PID=$!
        echo "GDB server PID: $GDB_SERVER_PID"
        echo "Log file: /tmp/probe-rs-gdb-server.log"
        sleep 2
        
        echo ""
        attach_gdb
        
        # Kill server on exit
        echo ""
        echo "Stopping GDB server (PID: $GDB_SERVER_PID)..."
        kill $GDB_SERVER_PID 2>/dev/null || true
        ;;
    *)
        echo "Usage: $0 [start-server|attach|both]"
        echo ""
        echo "  start-server - Start GDB server only (runs in foreground)"
        echo "  attach       - Attach GDB client to existing server"
        echo "  both         - Start server in background and attach client (default)"
        exit 1
        ;;
esac
