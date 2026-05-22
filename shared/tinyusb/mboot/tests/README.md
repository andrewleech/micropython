# shared/tinyusb/mboot/tests — host unit-test harness

Tests for the port-agnostic Stage 0 DFU bootloader building blocks.
Runnable with plain `gcc` and `make` on Linux x86_64; no MCU toolchain needed.

## Building and running

```bash
# Run both test binaries (exits nonzero on any failure)
make -C shared/tinyusb/mboot/tests check

# Build only
make -C shared/tinyusb/mboot/tests

# Clean
make -C shared/tinyusb/mboot/tests clean
```

## Test modules

### test_dfu.c — DFU download flash dispatch

| Name | Description |
|------|-------------|
| test_1_dnload_eot | Zero-length DNLOAD returns 0 with no flash side effect |
| test_2_dnload_too_large | DNLOAD with wLength > MBOOT_DFU_XFER_SIZE returns negative; no flash side effect |
| test_3_dnload_padding | DNLOAD with non-4-byte-multiple length pads with 0xFF correctly |
| test_4_implicit_erase | 4 sequential writes across 2 sectors trigger exactly 2 erase calls |
| test_5_notify_set_interface | notify_set_interface clears bitmap; same sector erases again |

### test_vendor.c — vendor erase dispatch

| Name | Description |
|------|-------------|
| test_10_erase_one_sector | Vendor 0x80 with len=4096 triggers 1 erase call |
| test_11_erase_sixteen_sectors | Vendor 0x80 with len=16*4096 triggers 16 erase calls |
| test_12_mass_erase | Vendor 0x80 with len=0xFFFFFFFF erases every sector exactly once (per-sector count verified) |
| test_13_out_of_range | Addr outside any region (lower and upper bound) returns negative; zero erases |
| test_14_unknown_opcode | Vendor bRequest=0x81 returns -EINVAL; no flash or state side effects |
| test_34_erase_alt_nonzero | Vendor 0x80 with wValue=1 erases alt 1 only; alt 0 is untouched |
| test_34b_erase_alt_out_of_range | wValue >= region count returns -EINVAL |
| test_36_erase_bad_payload | Payload not 8 bytes returns -EINVAL |
| test_37_erase_zero_length | len=0 is a no-op; zero erase calls |

### test_region.c — region table and alt-string generation

| Name | Description |
|------|-------------|
| test_15_init_validation | Valid table returns 0; non-writable region returns -EINVAL |
| test_16_alt_string_64k | 64 KiB x 128 region produces correct dfu-util descriptor string |
| test_17_alt_string_bytes | 256 B x 16 region uses the 'B' unit in the descriptor |
| test_18_set_active_out_of_range | set_active(N) where N >= region_count returns -EINVAL |
| test_19_sector_iter | sector_iter yields exactly sector_count entries in ascending order |
| test_20_write_out_of_range | mboot_region_write rejects addr one byte before region base |
| test_36_init_gap_rejected | Region table with address gap between same-name regions returns -EINVAL |
| test_37_multi_region_fold | Two same-name adjacent regions fold to one alt; alt string has comma-joined geometry runs and 'M' unit |
| test_38_read_not_readable | mboot_region_read on a region without READABLE flag returns error |

### test_elem.c — TLV element parser (items 21-26)

| Name | Description |
|------|-------------|
| test_21_empty_buffer | mboot_elem_search on NULL/empty buffer returns NULL |
| test_22_only_end | Search for type X in a stream with only END returns NULL |
| test_23_truncated_header | buf_len=1 (truncated header) returns NULL safely |
| test_24_length_overflow | Declared payload length exceeding buffer returns NULL |
| test_25_search_middle | Search succeeds on well-formed stream with target type in middle |
| test_26_validate | validate: true on well-formed, false on missing END, false on END with non-zero length |

### test_usbd.c — descriptor builder and USB callbacks

Compiled with `MBOOT_TESTS_FAKE_TUSB=1`, which causes `mboot_usbd.c` to include
`fake_tusb.h` instead of the real TinyUSB headers.  `fake_tusb.{c,h}` provides
minimal type definitions and recording stubs for `tud_control_xfer`,
`tud_control_status`, and `tud_dfu_finish_flashing`.

| Name | Description |
|------|-------------|
| test_40_init | After mboot_usbd_init, mboot_usbd_leave_requested is false |
| test_41_set_alt_valid | tud_dfu_set_alt_cb with valid alt returns true and updates active region |
| test_42_set_alt_invalid | tud_dfu_set_alt_cb with out-of-range alt returns false (STALL) |
| test_43_set_alt_notify | set_alt_cb with valid alt twice correctly tracks active region |
| test_44_download_cb | tud_dfu_download_cb writes to flash and calls tud_dfu_finish_flashing(OK) |
| test_45_manifest_cb | tud_dfu_manifest_cb calls tud_dfu_finish_flashing(OK) |
| test_46_vendor_setup | vendor opcode 0x80 SETUP stage requests 8-byte DATA stage |
| test_47_vendor_unknown_opcode | vendor opcode 0x81 SETUP returns false (STALL) |
| test_48_vendor_wrong_length | vendor 0x80 with wLength != 8 returns false (STALL) |
| test_49_cfg_desc_1alt_len | configuration descriptor for 1-alt setup is 27 bytes |
| test_50_cfg_desc_2alt_len | configuration descriptor for 2-alt setup is 36 bytes |
| test_51_str_langid | string index 0 returns LANGID descriptor (0x0409) |
| test_52_str_alt | string index 4 (alt 0) returns a valid USB string descriptor |
| test_53_str_alt_out_of_range | string index 5 in a 1-alt setup returns NULL |

### test_pydfu_wire.py — pydfu wire-protocol contract test

Verifies that `tools/pydfu.py` issues the correct `ctrl_transfer` arguments for `mass_erase()` and `page_erase()` in both default (pure DFU 1.1 / vendor-erase) and legacy `--dfuse` (DfuSe) modes.  Runs with plain `python3`; `pyusb` is not required — `usb.core` and `usb.util` are mocked via `sys.modules` before import.

### test_pack_hook.c — pack hook (item 9, separate binary)

Built as `test_runner_pack` with `-DMBOOT_ENABLE_PACKING=1`.

| Name | Description |
|------|-------------|
| test_9_pack_hook | DFU_DNLOAD reaches mboot_pack_write stub when MBOOT_ENABLE_PACKING=1 |

## How to add a new test

1. Add a `static int test_<name>(int *failures)` function to the appropriate
   `test_<module>.c` file.
2. Add a `RUN_TEST(test_<name>);` call in that module's `test_<module>()`
   entry point.
3. No changes to `test_main.c` or `Makefile` are needed.

Use the assertion macros from `test_main.h`:
- `TEST_ASSERT(expr)` — fail if expr is false; return -1.
- `TEST_ASSERT_EQ(a, b)` — fail if a != b; print both values.
- `TEST_ASSERT_NULL(ptr)` — fail if ptr != NULL.
- `TEST_ASSERT_NOTNULL(ptr)` — fail if ptr == NULL.
- `TEST_ASSERT_GE(a, b)` — fail if a < b.

## Region table configuration

Test modules use `TEST_REGIONS_SET(array, count)` (from `test_regions_config.h`)
to install a region table before each `mboot_region_init()` call.  The macro
copies the array into the global `mboot_port_regions[]` and updates
`mboot_port_regions_count`.

## Coverage notes

The following lines and branches are not covered by the tests.  Each entry
documents why coverage is absent and whether a future test is expected.

### Unreachable defensive paths

- `block_to_addr()` overflow path (`result < base`): unreachable on a 32-bit
  address space with a sane region table where `base + wBlockNum * XFER_SIZE`
  cannot overflow before exceeding the region bounds (which is caught first).
- `mboot_port_leave()` stub in `fake_flash.c`: calls `abort()` and is only
  reachable if a test incorrectly triggers bootloader exit.
- `bitmap_set()` guard `sector_idx < MBOOT_DFU_MAX_SECTOR_COUNT`: unreachable
  because `ensure_sector_erased()` only reaches `bitmap_set()` after
  successfully erasing a sector in a region whose total sector count is bounded
  by the region table, which is already bounded by `MBOOT_DFU_MAX_SECTOR_COUNT`.
- `mboot_region_init()` — `s_regions == NULL` check after `mboot_port_get_regions()`:
  the test implementation never returns NULL; the guard is defensive for ports
  that might.

### Deferred to on-target acceptance (no host coverage)

- `vendor_erase()` with `wValue != 0` (non-zero alt selection): covered by
  `test_34_erase_alt_nonzero` for the dispatch path; multi-region folding
  inside an alt is exercised in `test_region.c::test_37_multi_region_fold`.
- Manifest sequencing through TinyUSB (`tud_dfu_manifest_cb` ->
  `tud_dfu_reset_cb` -> `mboot_usbd_leave_requested`): exercised by the
  on-device acceptance tests in `acceptance/RUNBOOK.md`; the host stubs
  cannot drive a real USB bus reset.


