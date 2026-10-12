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

A port should implement the rest on the services its application already has, not beside them. The STM32 port (`ports/stm32/mboot_port.c`, `mboot_boot.c`) runs the bootloader on `SystemClock_Config()` with the board's `MICROPY_HW_CLK_*` values, the HAL tick, `mp_hal_pin_config()`, `mp_usbd_ll_init()` and `flash.c`, so a board needs no bootloader specific clock, pin or USB settings.

## STM32 builds

The STM32 bootloader is built with `make -C ports/stm32 -f Makefile.mboot BOARD=NUCLEO_H563ZI`. Its application is built with `make -C ports/stm32 BOARD=NUCLEO_H563ZI`. NUCLEO_H563ZI and PYBD_SF6 configure Mboot applications by default; PYBD_SF6 supports the single-slot policy.

`tools/ci.sh mboot_setup` initializes MCUboot, STM32/CMSIS, Mbed TLS and TinyUSB dependencies for production bootloader/application builds. Production CI builds the STM32 targets and runs the independent pydfu wire check.

The established `ports/stm32/mboot` implementation and its default make entrypoint are independent. Other boards retain their legacy `USE_MBOOT`, `BUILDING_MBOOT` and DfuSe/`fwupdate` contracts. The MCUboot-backed application sets `USE_MBOOT=0` because it uses its own image layout.

Build/signing make variables and generated filenames use the Mboot prefix: `MBOOT_PUBKEY`, `MBOOT_SIGN_KEY`, `MBOOT_PRODUCTION`, `mboot_gen/mboot_layout.{json,mk,ld}` and `mboot_keys_gen.c`. The production make setting is forwarded to C as `MICROPY_MBOOT_PRODUCTION`. The generated filesystem-reader selection is `MBOOT_MCUBOOT_FSLOAD`, separate from legacy `MBOOT_FSLOAD`. `MBOOT_DFU_FLASH_WRITE_ALIGN` is the derived device requirement; the transport is compiled with matching `MICROPY_HW_MBOOT_DFU_WRITE_ALIGN` and the binding checks their agreement.

Layout IDs, image TLVs, magic values, request packing and result numbers are format contracts independent of product naming. Signing uses `tools/mboot_sign.py` and the repository's MCUboot imgtool. See [the reference documentation](../../docs/reference/mboot.rst).
