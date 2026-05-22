<!-- SPDX-License-Identifier: MIT -->

shared/tinyusb/mboot — port-agnostic DFU 1.1 bootloader foundation
====================================================================

This directory contains the port-agnostic building blocks for a TinyUSB-based
DFU 1.1 bootloader for MicroPython.  Per-port adapters live under
`ports/<port>/mboot/`.

TinyUSB compatibility
---------------------

Builds against upstream TinyUSB unmodified.  TinyUSB's DFU class driver does
not give the application a callback on `SET_INTERFACE`, so the per-alt
touched-sector bitmap reset is performed lazily inside the next
`tud_dfu_download_cb` / `tud_dfu_upload_cb` / `tud_dfu_manifest_cb` /
`tud_dfu_abort_cb` invocation by comparing the supplied `alt` against the
last-seen value.  An out-of-range alt is rejected at the same point with
`DFU_STATUS_ERR_TARGET`.

Architecture
------------

Two-level layout, mirroring how `shared/tinyusb/mp_usbd.*` separates the
port-agnostic USB device glue from per-port adapters:

```
shared/tinyusb/mboot/
  include/
    mboot_api.h        Port-binding contract (header only).  Every per-port
                       adapter must implement the functions declared here.
    mboot_dfu.h        Flash dispatch helper API (DNLOAD + vendor erase).
    mboot_region.h     Region/alt-setting API.
    mboot_elem.h       TLV element parser API.
    mboot_pack.h       Optional pack/unpack hook surface.
  common/
    mboot_dfu.c        DNLOAD flash dispatch + touched-sector bitmap;
                       vendor opcode 0x80 erase dispatch.  TinyUSB's
                       dfu_device.c owns the wire-visible DFU state
                       machine; this file is the per-block flash work.
    mboot_region.c     Region table to alt-setting descriptor strings.
    mboot_elem.c       TLV element parser.
    mboot_usbd.c       TinyUSB USB device glue (descriptor builder, DFU
                       class callbacks, vendor request handler).
  tests/
    Makefile           Plain-host gcc build for unit tests.
    fake_flash.{c,h}   In-memory mboot_port_flash_* implementation.
    fake_transport.{c,h} Mock control-transfer driver for mboot_dfu.
    fake_tusb.{c,h}   Minimal TinyUSB stubs for mboot_usbd host tests.
    test_dfu.c         DNLOAD flash dispatch + bitmap tests.
    test_region.c      Region lookup and alt-setting string generation tests.
    test_elem.c        TLV parser edge-case tests.
    test_vendor.c      Vendor-erase opcode dispatch tests.
    test_usbd.c        Descriptor builder and USB callback tests.
  mboot_common.mk      Build fragment; per-port Makefiles include this.
```

Contract layer
--------------

`include/mboot_api.h` is the sole interface between common code and the
per-port adapter.  Common code under `common/` compiles with:

    gcc -Wall -Werror -std=gnu99 -ffreestanding \
        -I shared/tinyusb/mboot/include

No include of `py/`, `extmod/`, `shared/runtime/`, `tusb.h`, or any port
directory is permitted in common code.  The TinyUSB submodule (`lib/tinyusb`)
is not required to build common code or the host unit tests; it is only needed
by the per-port adapter that wires DFU class callbacks into the TinyUSB device
stack (Stage 1).

Protocol
--------

The bootloader speaks pure DFU 1.1 (USB DFU Specification 1.1, Section 3).
Erase is moved out of the DFU_DNLOAD payload and into a separate vendor
request on the DFU interface:

    bRequestType = 0x41 (host-to-device, class, interface)
    bRequest     = 0x80 (MBOOT_VREQ_ERASE)
    Payload:     <addr> <length> (each 4 or 8 bytes LE, matching mboot_addr_t)

A length value of all-ones (0xFFFFFFFF for 32-bit or 0xFFFFFFFFFFFFFFFF for
64-bit) triggers a mass erase of the entire writable address space.

Opcodes 0x81..0x8F are reserved for future mboot extensions.

The DfuSe block-0 commands (set-address 0x21, erase-page 0x41) used by
`ports/stm32/mboot/` are intentionally not supported.  Use `--dfuse` with
`tools/pydfu.py` to communicate with legacy STM32 mboot devices.

Host unit tests
---------------

The test harness under `tests/` builds and runs on plain Linux x86_64 with no
MCU toolchain:

    make -C shared/tinyusb/mboot/tests

All tests must return 0.

Relationship to ports/stm32/mboot/
-----------------------------------

`ports/stm32/mboot/` is the user-experience reference for feature scope and
flash-region conventions.  Its code is not reused; the implementations are
independent.  The dfu-util interface-string format (`@Name /addr/N*SizeKg`) is
shared by convention with the existing STM32 mboot so that the same host tools
work with both bootloaders.

Licence
-------

All files in this directory are MIT licensed.  See the SPDX header in each
source file.
