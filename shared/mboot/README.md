<!-- SPDX-License-Identifier: MIT -->

# Mboot shared bootloader and update support

Mboot is MicroPython's bootloader/update facility. MCUboot is the upstream engine that supplies signed-image validation, slot state and swap/revert. `lib/mcuboot` and `lib/tinyusb` are upstream dependencies; this tree contains MicroPython-owned configuration, flash/update policy, application support and transport integration.

## Layout and ownership

- `mboot_common.mk` selects sources and generates the board layout; `mboot_rules.mk` provides generation, signing and packaging rules; `mboot_bootloader.mk`, `mboot_bootloader.ld` and `mboot_sram.mk` build and link a bootloader on a GNU toolchain.
- `include/` defines the `mboot_*` contracts, `MICROPY_MBOOT_*` shared feature settings and `MICROPY_HW_MBOOT_*` hardware configuration. `mcuboot_config/`, `flash_map_backend/`, `sysflash/` and `os/` retain the include names required by bootutil. Exact upstream `MCUBOOT_*` switches remain engine configuration, translated from product choices where needed.
- `src/` implements boot flow, signed-update validation and policy, flash access, application handoff, filesystem loading and the shared bounded element parser.
- `dfu/core/` implements portable flash dispatch, region handling and TinyUSB callbacks. `dfu/include/` is its transport contract; `dfu/tests/test_pydfu_wire.py` checks the independent pydfu wire format. See [DFU transport details](dfu/README.md).

The bounded element parser is `src/mboot_elem.c` and `include/mboot_elem.h`. Filesystem loading consumes it without requiring DFU or TinyUSB. The build selects it exactly once when either update frontend needs it.

The DFU policy contract is `include/mboot_dfu_recovery.h` with `mboot_dfu_recovery_*` entrypoints, distinct from the transport's `dfu/include/mboot_dfu.h` and `mboot_dfu_*` dispatch functions. Device-relative flash operations use `mboot_port_flash_dev_*`; transport address-relative operations use `mboot_port_flash_*`. The binding enforces the generated write ranges before accessing flash.

## Porting

A port provides the functions of `include/mboot_port.h`, `mboot_dev.h` (the flash geometry, read through `include/mboot_layout.h`), `mboot_sram.ld.S` (the SRAM bounds) and a bootloader Makefile that includes `mboot_common.mk` and `mboot_bootloader.mk`. What does not depend on the MCU is shared. `src/flash_internal.c` is the policy for a memory mapped internal flash, on two controller primitives of the port. `src/ecc.c` counts flash ECC events from two controller flags. `src/backup_words.c` keeps the handoff word and the reset flags in the words behind `mboot_port_bkp_read/write()`. `src/fault.c` is the Cortex-M fault policy and `src/boot_info.c` the info block and `main()`.

A port should implement the rest on the services its application already has, not beside them. The STM32 port (`ports/stm32/mboot_port.c`, `mboot_boot.c`) runs the bootloader on `SystemClock_Config()` with the board's `MICROPY_HW_CLK_*` values, the HAL tick, `mp_hal_pin_config()`, `uart.c`, `mp_usbd_ll_init()`, `powerctrl.c` and `flash.c`, so a board needs no bootloader specific clock, pin or USB settings. It does need the settings its application already has that the bootloader reads: `MICROPY_HW_LED1` to `MICROPY_HW_LED3` with `MICROPY_HW_LED_ON/OFF`, `MICROPY_HW_USRSW_PIN`, `_PULL` and `_PRESSED` (the forced entry button, sampled at reset), and `MICROPY_HW_UART_REPL` with `_BAUD` for the bootloader log. On STM32H5 the bootloader runs with TrustZone disabled (TZEN=0).

## Update audit log

`src/updatelog.c` and `include/mboot_updatelog.h` provide the persistent update audit log in `MBOOT_AREA_LOG`. The bootloader and application append the same 32-byte record format, with a sequence number, event, result, source, image version, detail and hash prefix. A CRC-32/ISO-HDLC covers bytes 0 through 27. Two erase units alternate as the active ring; a torn or unreadable record consumes its slot but is not returned as valid.

The log records DFU and filesystem update outcomes, swap/revert decisions, application requests and confirmation, assertion failures and security-counter initialization failures. Log-write failure does not fail the operation being recorded. `mboot_updatelog_read()` and the application wrapper `mboot_app_log_get()` return records newest-first, with index zero naming the newest valid record.

The Mboot DFU binding exposes a read-only alternate setting named `Update audit log`. Decode its raw flash dump with `python3 tools/mboot_log.py DUMP.bin`; `--unit` adds erase-unit locations and `--json` selects JSON output. The tool also decodes the 16-byte vendor-request 0x81 reply with `--result HEX`. The live DFU result sequence counts session outcomes independently of the persistent log sequence.

## STM32 builds

The STM32 port implements Mboot for STM32H5 and STM32F7 only. The bootloader is built with `make -C ports/stm32 -f Makefile.mboot BOARD=NUCLEO_H563ZI` into `build-<BOARD>-mboot`. Its application is built with `make -C ports/stm32 BOARD=NUCLEO_H563ZI` into `build-<BOARD>`; a board selects this by including `ports/stm32/mboot_app.mk` from its `mpconfigvariant.mk`. NUCLEO_H563ZI (two slots with swap) and PYBD_SF6 (STM32F767, single-slot policy with the update image in SPI flash) are the boards configured so far. `ports/stm32/mboot_dev.h` describes their flash.

`tools/ci.sh mboot_setup` initializes MCUboot, STM32/CMSIS, Mbed TLS and TinyUSB dependencies for production bootloader/application builds. Production CI builds the STM32 targets and runs the independent pydfu wire check.

The established `ports/stm32/mboot` implementation and its default make entrypoint are independent. Other boards retain their legacy `USE_MBOOT`, `BUILDING_MBOOT` and DfuSe/`fwupdate` contracts. The MCUboot-backed application sets `USE_MBOOT=0` because it uses its own image layout, and `Makefile.mboot` sets `BUILDING_MBOOT=1` only so that `stm32.mk` selects the bootloader's soft-float flags.

Build/signing make variables and generated filenames use the Mboot prefix: `MBOOT_PUBKEY`, `MBOOT_SIGN_KEY`, `MBOOT_PRODUCTION`, `mboot_gen/mboot_layout.{json,mk,ld}` and `mboot_keys_gen.c`. The production make setting is forwarded to C as `MICROPY_MBOOT_PRODUCTION`. The generated filesystem-reader selection is `MBOOT_MCUBOOT_FSLOAD`, separate from legacy `MBOOT_FSLOAD`. `MBOOT_DFU_FLASH_WRITE_ALIGN` is the derived device requirement; the transport is compiled with matching `MICROPY_HW_MBOOT_DFU_WRITE_ALIGN` and the binding checks their agreement.

Layout IDs, image TLVs, magic values, request packing and result numbers are format contracts independent of product naming. Signing uses `tools/mboot_sign.py` and the repository's MCUboot imgtool. See [the reference documentation](../../docs/reference/mboot.rst).

## Bootloader info block

The last 128 bytes of the boot area hold the bootloader info block, written by `src/boot_info.c` in the format of `include/mboot_bl_info.h`: magic, info version, the size of the defined fields, feature flags, layout ID, `MBOOT_API_VERSION`, version string, the bootloader's security counter image ID and value, and the SHA-256 of the trusted public key (`mboot_keyhash_gen.h`, generated by `tools/mboot_gen.py` with `mboot_keys_gen.c`). The flags carry the DFU, fsload and gzip front ends, a two-bit bootloader update method (0, none, for now) and reserved bits for the key source, TrustZone and encrypted images. The application reads the block only through `mboot_app_bootloader_info()`.

## Rules for opt-in features

Features such as bootloader update, a hardware key hash, TrustZone and encrypted images are board options, off by default. They follow these rules so that boards that do not enable them keep working with bootloaders already in the field:

- A feature adds layout-ID steps in `include/mboot_layout.h` only when it is enabled, as the existing chain does, so boards that do not opt in keep their layout ID and stay updatable by deployed bootloaders.
- `MBOOT_API_VERSION` is part of every layout ID. It is raised only for an incompatible change between the bootloader and the application; additive calls or fields are detected through the info block flags and size.
- Only application images carry the 0x00A1 layout TLV. Other image kinds, such as bootloader update images, carry a distinct protected TLV and never 0x00A1, so every intake path rejects them as applications.
- The application reaches bootloader state only through the `mboot_app_*` calls of `include/mboot_app.h`. Their arguments are scalars or caller buffers with explicit lengths, results are copied out, there are no callbacks, and no pointers into bootloader memory are taken or handed out, so that the calls can become secure-world entry points.
- Security counter image IDs are `MBOOT_SECCNT_ID_APP` and `MBOOT_SECCNT_ID_BOOTLOADER` in `include/mboot_port.h`; the flash counter keeps the largest value of each ID that is not 0, for up to four IDs.
