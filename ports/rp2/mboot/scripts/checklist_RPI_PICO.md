# Hardware acceptance: RPI_PICO

Paired with `hardware_acceptance.sh RPI_PICO <SERIAL_DEV>`.  Complete each
step manually or via the script.  Mark each row `[x]` when the pass criterion
is met.  Any unchecked row at the end of the run is a blocking failure.

## Pre-conditions

- USB-C (or micro-USB) cable connecting RPI_PICO to the host.
- Board accessible at a known serial path, e.g.
  `/dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_<id>`.
- Tools installed and in PATH: `make`, `cmake`, `arm-none-eabi-gcc`,
  `picotool`, `dfu-util`, `mpremote`, `python3`.
- `lib/pico-sdk` and `lib/tinyusb` submodules initialised.

## Checklist

| # | Step | Pass criterion | Status |
|---|------|----------------|--------|
| 1 | Build mboot: `make -C ports/rp2/mboot BOARD=RPI_PICO` | `build-RPI_PICO/mboot.uf2` exists and is < 192 KiB (raw image < 64 KiB) | [ ] |
| 2 | Flash mboot via BOOTSEL: hold BOOTSEL, power-cycle, `picotool load -x build-RPI_PICO/mboot.uf2` | `picotool` reports success; LED blink pattern changes (mboot running) | [ ] |
| 3 | Verify mboot enumerates: `lsusb -d 2e8a:dfa0` and `dfu-util -l` | `lsusb` shows `2e8a:dfa0`; `dfu-util -l` shows alt 0 with non-empty `iInterface` | [ ] |
| 4 | Build app firmware: `make -C ports/rp2 BOARD=RPI_PICO USE_MBOOT=1` | `build-RPI_PICO/firmware.dfu` and `firmware.bin` exist | [ ] |
| 5 | Program app via pydfu.py: `python3 tools/pydfu.py -u ports/rp2/build-RPI_PICO/firmware.dfu` | Tool reports success; board reboots into MicroPython | [ ] |
| 6 | Confirm app boots: `mpremote connect <DEV> eval 'import sys; sys.platform'` | Returns `'rp2'` | [ ] |
| 7 | Re-enter mboot: `mpremote connect <DEV> eval 'import machine; machine.bootloader()'` then `dfu-util -l` | `dfu-util -l` shows mboot interface within 5 seconds | [ ] |
| 8 | True fallback: after step 7 reflash app, then `machine.bootloader(True)` | `lsusb -d 2e8a:0003` shows ROM BOOTSEL device; drag-and-drop drive appears | [ ] |
| 9 | mboot self-protect: `python3 tools/pydfu.py --address 0x10000000 -u firmware.bin` | Tool reports address error / `errADDRESS`; mboot region is not overwritten | [ ] |

## Notes

- After step 8, hold BOOTSEL and power-cycle to return to mboot, then repeat
  step 5 to restore the app before the next test run.
- Step 9 requires mboot to be running (trigger via `machine.bootloader()` first).
- `dfu-util` alternative for step 5:
  `dfu-util -d 2e8a:dfa0 -a 0 -D ports/rp2/build-RPI_PICO/firmware.bin`
