<!-- SPDX-License-Identifier: MIT -->

shared/tinyusb/mboot - port-agnostic DFU 1.1 bootloader core
=============================================================

The flash-facing part of a TinyUSB DFU 1.1 bootloader: region and alt-setting handling, DNLOAD/UPLOAD flash dispatch, a vendor erase request and the TinyUSB device callbacks. The only consumer in this tree is `shared/mcuboot/src/dfu_glue.c`, which binds the core to the generated MCUboot flash map for the bootloader in `ports/stm32/mcuboot`.

Layout
------

    include/mboot_api.h     Port contract: region table, flash, USB identity and hook declarations.
    include/mboot_dfu.h     DNLOAD and vendor erase dispatch API.
    include/mboot_region.h  Region and alt-setting API.
    include/mboot_elem.h    TLV element parser API.
    include/mboot_usbd.h    TinyUSB device glue API.
    common/mboot_dfu.c      DNLOAD flash dispatch, touched-sector bitmap, vendor erase.
    common/mboot_region.c   Region table to alt settings and dfu-util interface strings.
    common/mboot_elem.c     TLV element parser.
    common/mboot_usbd.c     Descriptor builder, DFU class callbacks, vendor request handler.
    tests/                  Host unit tests.

The common code includes no `py/`, `extmod/` or port header, and only `mboot_usbd.c` includes TinyUSB (`lib/tinyusb`, unmodified). TinyUSB's DFU driver has no callback for `SET_INTERFACE` or for a bus reset, so `mboot_usbd.c` detects an alt switch at the next data callback and `mboot_usbd_leave_requested()` polls `tud_mounted()` to see the reset that follows a successful manifest.

Port contract
-------------

`include/mboot_api.h` documents everything the binding code provides: `mboot_port_get_regions()`, the `mboot_port_flash_*` functions, the USB identity functions and the three hooks below. Regions of one alt setting share a name and must be contiguous and ascending; `MBOOT_REGION_FLAG_READ_ONLY` regions can be read and uploaded but not erased or written. `MBOOT_DFU_WRITE_ALIGN` (default 4) is the write unit; DNLOAD blocks are padded with 0xFF to a multiple of it.

Protocol
--------

Pure DFU 1.1. Erase is a vendor request on the DFU interface: `bmRequestType` 0x41, `bRequest` 0x80, payload `<addr:u32 LE> <length:u32 LE>`. A length of 0xFFFFFFFF mass-erases the alt setting selected by `wValue` (0 is the active one), any other length erases the sectors covering `[addr, addr+length)`. Vendor request 0x81 (`bmRequestType` 0xC1, 16 bytes) returns the result supplied by `mboot_hook_get_result()`. The DfuSe block-0 commands of `ports/stm32/mboot` are not supported; use `--dfuse` with `tools/pydfu.py` for those devices.

Hooks
-----

The core has no default for these; a link without definitions of all three fails.

- `mboot_hook_session_begin(alt)`: before the first DNLOAD block or vendor erase of a session on a writable alt. Non-zero aborts the request before any flash access.
- `mboot_hook_manifest(alt)`: from `tud_dfu_manifest_cb()`. The returned DFU status goes to `tud_dfu_finish_flashing()`; the leave request is raised only for `DFU_STATUS_OK`.
- `mboot_hook_get_result(buf, len)`: fills the reply of vendor request 0x81.

Tests
-----

    make -C shared/tinyusb/mboot/tests check

Builds and runs the core tests, the `dfu_glue.c` tests and the pydfu wire test on plain Linux; see `tests/README.md`.
