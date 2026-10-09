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

## STM32 builds

The MCUboot-backed STM32 bootloader is built with `make -C ports/stm32/mboot/mcuboot BOARD=NUCLEO_H563ZI`. Its application is built with `make -C ports/stm32 BOARD=NUCLEO_H563ZI MBOOT_BACKEND=mcuboot`, using a separate `build-NUCLEO_H563ZI-mboot` directory by default. PYBD_SF6 supports the single-slot policy.

The established `ports/stm32/mboot` implementation and its default make entrypoint are independent. `USE_MBOOT`, `BUILDING_MBOOT`, legacy board settings and legacy DfuSe/`fwupdate` behavior retain their existing contracts. The MCUboot-backed application explicitly sets `USE_MBOOT=0`; selecting it does not change the legacy bootloader.

Board layout/signing configuration and generated filenames use the Mboot prefix: `MBOOT_PUBKEY`, `MBOOT_SIGN_KEY`, `MBOOT_PRODUCTION`, `mboot_gen/mboot_layout.{json,mk,ld}` and `mboot_keys_gen.c`. The generated filesystem-reader selection is `MBOOT_MCUBOOT_FSLOAD`, separate from legacy `MBOOT_FSLOAD`. `MBOOT_DFU_FLASH_WRITE_ALIGN` is the derived device requirement; the transport is compiled with matching `MBOOT_DFU_WRITE_ALIGN` and the binding checks their agreement.

Layout IDs, image TLVs, magic values, request packing and result numbers are format contracts independent of product naming. Signing uses `tools/mboot_sign.py` and the repository's MCUboot imgtool. See [the reference documentation](../../docs/reference/mboot.rst) and [host test commands](../../tests/mboot/README.md).
