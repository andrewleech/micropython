# mboot Hardware Acceptance Runbook

A short procedure for confirming that a port's mboot bootloader can flash an
application image and hand off to it.

## Boards covered

| Board                  | MCU    | Bootloader USB ID |
|------------------------|--------|-------------------|
| RPI_PICO               | RP2040 | 2e8a:dfa0         |
| RPI_PICO2              | RP2350 | 2e8a:dfa1         |
| PIMORONI_PICOLIPO      | RP2040 | 2e8a:dfa2         |
| RPI_PICO2_W            | RP2350 | 2e8a:dfa3         |
| MIMXRT1010_EVK         | RT1011 | f055:0014         |

Other boards are not covered by this runbook.

## 1. Build

For each board, build both the bootloader and the application image.

rp2:
```sh
make -C ports/rp2/mboot BOARD=<board>     # bootloader (mboot)
make -C ports/rp2 BOARD=<board>           # application (uses memmap_mp_*_mboot.ld)
tools/dfu.py -b 0x10010000:ports/rp2/build-<board>/firmware.bin \
    ports/rp2/build-<board>/firmware.dfu
```

mimxrt:
```sh
make -C ports/mimxrt/mboot BOARD=MIMXRT1010_EVK
make -C ports/mimxrt BOARD=MIMXRT1010_EVK
tools/dfu.py -b 0x60010000:ports/mimxrt/build-MIMXRT1010_EVK/firmware.bin \
    ports/mimxrt/build-MIMXRT1010_EVK/firmware.dfu
```

## 2. Flash the bootloader

The bootloader must be flashed with an external programmer (it cannot flash
itself).  Use the toolchain appropriate for the board (probe-rs / openocd /
JLink) targeting the bootloader's load address (`XIP_BASE` on rp2,
`0x60000000` on RT1011).

## 3. Enter mboot

Double-tap the reset button within 500 ms, or from a running MicroPython
application call `machine.bootloader()`.  Confirm enumeration:

```sh
lsusb -d <bootloader-id>
```

The string descriptors should read `MicroPython` / `<board> Bootloader` and a
16-character hex serial derived from the MCU unique ID.

## 4. Flash the application

```sh
python3 tools/pydfu.py ports/<port>/build-<board>/firmware.dfu
```

Confirm progress output and successful manifest.  The device reboots and the
application starts; verify by connecting to its USB CDC (or whatever interface
the application exposes).

## 5. Bootloader self-protection

The bootloader must refuse writes to its own flash region.  With the
bootloader running, attempt to flash a `.dfu` image whose element base address
overlaps the bootloader slot.  Construct it with:

```sh
tools/dfu.py -b 0x10000000:<some-binary> bad.dfu      # rp2
tools/dfu.py -b 0x60000000:<some-binary> bad.dfu      # mimxrt
```

Then:

```sh
python3 tools/pydfu.py bad.dfu
```

The transfer must fail; the bootloader stays in DFU mode and the existing
application is intact.

## Acceptance checklist

| Check                                                  | Pass? |
|--------------------------------------------------------|-------|
| Bootloader and application both build for each board   |       |
| Bootloader enumerates with the expected USB strings    |       |
| Application `.dfu` flashes via `pydfu.py` and runs     |       |
| Out-of-range `.dfu` is rejected (self-protection)      |       |
