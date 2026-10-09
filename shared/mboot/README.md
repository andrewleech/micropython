<!-- SPDX-License-Identifier: MIT -->

# Mboot shared bootloader and update support

Mboot is MicroPython's bootloader/update facility. MCUboot is the upstream engine that supplies signed-image validation, slot state and swap/revert. `lib/mcuboot` and `lib/tinyusb` are upstream dependencies; this tree contains MicroPython-owned configuration, flash/update policy, application support and transport integration.

## Layout and ownership

- `mboot_common.mk` selects sources and generates the board layout; `mboot_rules.mk` provides generation, signing and packaging rules.
- `include/` defines the `mboot_*` contracts and `MBOOT_*` product configuration. `mcuboot_config/`, `flash_map_backend/`, `sysflash/` and `os/` retain the include names required by bootutil. Exact upstream `MCUBOOT_*` switches remain engine configuration, translated from product choices where needed.
- `src/` implements boot flow, signed-update validation and policy, flash access, application handoff, filesystem loading and the shared bounded element parser.
- `dfu/core/` implements portable flash dispatch, region handling and TinyUSB callbacks. `dfu/include/` is its transport contract; `dfu/tests/` covers the core, wire format and actual policy binding. See [DFU transport details](dfu/README.md).

The bounded element parser is `src/mboot_elem.c` and `include/mboot_elem.h`. Filesystem loading consumes it without requiring DFU or TinyUSB. The build selects it exactly once when either update frontend needs it.

The DFU policy contract is `include/mboot_dfu_recovery.h` with `mboot_dfu_recovery_*` entrypoints, distinct from the transport's `dfu/include/mboot_dfu.h` and `mboot_dfu_*` dispatch functions. Device-relative flash operations use `mboot_port_flash_dev_*`; transport address-relative operations use `mboot_port_flash_*`. The binding enforces the generated write ranges before accessing flash.

## Update audit log

`src/updatelog.c` and `include/mboot_updatelog.h` provide the persistent update audit log in `MBOOT_AREA_LOG`. The bootloader and application append the same 32-byte record format, with a sequence number, event, result, source, image version, detail and hash prefix. A CRC-32/ISO-HDLC covers bytes 0 through 27. Two erase units alternate as the active ring; a torn or unreadable record consumes its slot but is not returned as valid.

The log records DFU and filesystem update outcomes, swap/revert decisions, application requests and confirmation, assertion failures and security-counter initialization failures. Log-write failure does not fail the operation being recorded. `mboot_updatelog_read()` and the application wrapper `mboot_app_log_get()` return records newest-first, with index zero naming the newest valid record.

The Mboot DFU binding exposes a read-only alternate setting named `Update audit log`. Decode its raw flash dump with `python3 tools/mboot_log.py DUMP.bin`; `--unit` adds erase-unit locations and `--json` selects JSON output. The tool also decodes the 16-byte vendor-request 0x81 reply with `--result HEX`. The live DFU result sequence counts session outcomes independently of the persistent log sequence.

## STM32 builds

The MCUboot-backed STM32 bootloader is built with `make -C ports/stm32/mboot/mcuboot BOARD=NUCLEO_H563ZI`. Its application is built with `make -C ports/stm32 BOARD=NUCLEO_H563ZI MBOOT_BACKEND=mcuboot`, using a separate `build-NUCLEO_H563ZI-mboot` directory by default. PYBD_SF6 supports the single-slot policy.

`tools/ci.sh mboot_setup` initializes MCUboot, STM32/CMSIS, Mbed TLS and TinyUSB dependencies for the host and embedded checks. The Python suite also compiles the F7 DFU bootloader, so it needs TinyUSB even when the other host tests use a fake transport.

The established `ports/stm32/mboot` implementation and its default make entrypoint are independent. `USE_MBOOT`, `BUILDING_MBOOT`, legacy board settings and legacy DfuSe/`fwupdate` behavior retain their existing contracts. The MCUboot-backed application explicitly sets `USE_MBOOT=0`; selecting it does not change the legacy bootloader.

Board layout/signing configuration and generated filenames use the Mboot prefix: `MBOOT_PUBKEY`, `MBOOT_SIGN_KEY`, `MBOOT_PRODUCTION`, `mboot_gen/mboot_layout.{json,mk,ld}` and `mboot_keys_gen.c`. The generated filesystem-reader selection is `MBOOT_MCUBOOT_FSLOAD`, separate from legacy `MBOOT_FSLOAD`. `MBOOT_DFU_FLASH_WRITE_ALIGN` is the derived device requirement; the transport is compiled with matching `MBOOT_DFU_WRITE_ALIGN` and the binding checks their agreement.

Layout IDs, image TLVs, magic values, request packing and result numbers are format contracts independent of product naming. Signing uses `tools/mboot_sign.py` and the repository's MCUboot imgtool. See [the reference documentation](../../docs/reference/mboot.rst) and [host test commands](../../tests/mboot/README.md).
