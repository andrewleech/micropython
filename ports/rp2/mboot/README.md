# rp2 mboot — USB DFU bootloader for RP2040/RP2350

Standalone TinyUSB-based DFU 1.1 bootloader for Raspberry Pi RP2040/RP2350 boards.
Part of `shared/tinyusb/mboot` Stage 1.

## Prerequisites

- `lib/pico-sdk` submodule initialised:
  ```bash
  git submodule update --init lib/pico-sdk
  ```
- `lib/tinyusb` submodule initialised (required by the main rp2 port as well):
  ```bash
  git submodule update --init lib/tinyusb
  ```
- `arm-none-eabi-gcc` toolchain in PATH.
- `cmake` >= 3.13.

## Building mboot

Run from the `ports/rp2/mboot/` directory:

```bash
make BOARD=RPI_PICO
```

Output artefacts:

| File | Description |
|------|-------------|
| `build-RPI_PICO/mboot.elf` | ELF with debug info |
| `build-RPI_PICO/mboot.uf2` | UF2 for drag-and-drop install |
| `build-RPI_PICO/mboot.bin` | raw binary |

Other supported boards:

```bash
make BOARD=RPI_PICO2
make BOARD=PIMORONI_PICOLIPO
```

To build all boards at once:

```bash
scripts/build_all.sh
```

## Flashing mboot itself (first-time install)

Hold the BOOTSEL button while plugging in the board.  The RP2040/RP2350 ROM
presents a mass-storage drive.  Copy the UF2:

```bash
cp build-RPI_PICO/mboot.uf2 /media/$USER/RPI-RP2/
```

Or use `picotool` (board must be in BOOTSEL mode):

```bash
picotool load -x build-RPI_PICO/mboot.uf2
```

Once mboot is installed, subsequent mboot reflashes can be triggered
programmatically (see "Entering mboot from a running application" below) and
then flashed with `picotool load -x` or a DFU tool.

## Flashing an application via mboot

With mboot running and the board enumerated as a DFU device:

```bash
# List available DFU targets:
dfu-util -l

# Flash using dfu-util (raw .bin):
dfu-util -d 2e8a:dfa0 -a 0 -D firmware.bin

# Flash using pydfu.py (DFU-format file):
python3 tools/pydfu.py -u firmware.dfu
```

The application must be built with `APPLICATION_ADDR = XIP_BASE + 0x10000`
(the default when `MBOOT_RESERVED = 64 KiB`) as its vector-table origin.

## Entering mboot from a running application

### With `machine.bootloader()` (mboot-enabled app build)

The four mboot-enabled boards (RPI_PICO, RPI_PICO2, RPI_PICO2_W,
PIMORONI_PICOLIPO) declare `set(USE_MBOOT 1)` + `add_compile_definitions(USE_MBOOT)`
in their `mpconfigboard.cmake`.  Build the application normally:

```bash
make -C ports/rp2 BOARD=RPI_PICO
```

From the MicroPython REPL:

```python
import machine
machine.bootloader()       # enter mboot (write magic + watchdog reset)
machine.bootloader(True)   # enter ROM BOOTSEL (drag-and-drop UF2 mode)
```

The hook writes `0x70AD0000` to `watchdog_hw->scratch[0]` and calls
`watchdog_reboot(0, 0, 0)`.  mboot reads `scratch[0]` in `mboot_port_early_init`
and enters DFU mode when the key matches.

Boards that do not set `USE_MBOOT` keep the existing behaviour:
`machine.bootloader()` always goes to ROM BOOTSEL.

### Enabling mboot entry on a new board

1. In the board's `mpconfigboard.cmake`:
   ```cmake
   set(USE_MBOOT 1)
   add_compile_definitions(USE_MBOOT)
   ```
2. In the board's `mpconfigboard.h`, add a per-board mboot block:
   ```c
   #if defined(USE_MBOOT)
   #define MBOOT_USB_PID         (0xDFA?u)  // unique PID for this board
   #define MBOOT_LED_PIN         (25)        // -1 if no GPIO LED available
   #define MBOOT_PRODUCT_STRING  "<BOARD> Bootloader"
   #endif
   ```
3. Build the application with `make BOARD=<BOARD>`.  The mboot_boardctrl.h
   include is auto-pulled by `ports/rp2/mpconfigport.h` when `USE_MBOOT`
   is defined; the central linker-script switch in `ports/rp2/CMakeLists.txt`
   picks the correct `memmap_mp_rp2*_mboot.ld` based on PICO_PLATFORM.

## Recovery if mboot is bricked

RP2040/RP2350 always retain access to the ROM BOOTSEL loader regardless of
what is installed in QSPI flash.  If mboot is broken:

1. Hold BOOTSEL and power-cycle the board.
2. The ROM mass-storage drive appears.
3. Copy a known-good UF2 (another mboot build or the standard MicroPython
   firmware) to the drive.

No SWD or JTAG access is required.  If `picotool` is available:

```bash
picotool erase          # erase all flash
picotool load -x build-RPI_PICO/mboot.uf2
```

## Flash layout (RPI_PICO, 2 MiB)

| Region      | Address     | Size      |
|-------------|-------------|-----------|
| mboot slot  | 0x10000000  | 64 KiB    |
| Application | 0x10010000  | ~1984 KiB |

## Watchdog scratch register usage

| Register     | Owner          | Purpose                         |
|--------------|----------------|---------------------------------|
| scratch[0]   | mboot          | Magic entry key (0x70AD0000)    |
| scratch[4]   | pico-sdk       | watchdog_enable cause tracking  |
| scratch[5..7]| pico-sdk / UF2 | Double-reset / UF2 magic        |

## Known limitations

- No UF2 generation or UF2-format DFU receive in Stage 1.  The DFU payload
  must be a raw binary at the correct flash offset (Stage 2 will add UF2 mode).
- No signed-image verification in Stage 1 (Stage 3 deliverable).
- `machine.bootloader(True)` continues to work on mboot-enabled boards and
  puts the board into ROM BOOTSEL / drag-and-drop UF2 mode; users who relied on
  the old argless behaviour for UF2 must now pass `True` explicitly.
