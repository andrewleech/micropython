# shared/mboot/dfu/tests - host unit tests

Host tests for the DFU bootloader core in `shared/mboot/dfu` and for `shared/mboot/src/dfu_glue.c`. They build with plain `gcc` and `make` on Linux, no MCU toolchain needed.

## Building and running

```bash
# Run every test binary (exits nonzero on any failure)
make -C shared/mboot/dfu/tests check

# Only the portable core and parser, or only the Mboot DFU policy binding tests
make -C shared/mboot/dfu/tests check_core
make -C shared/mboot/dfu/tests check_glue

# Build only
make -C shared/mboot/dfu/tests

# Clean
make -C shared/mboot/dfu/tests clean
```

## Test modules

### test_dfu.c - DFU download flash dispatch

| Name | Description |
|------|-------------|
| test_1_dnload_eot | Zero-length DNLOAD returns 0 and does not touch flash |
| test_2_dnload_too_large | DNLOAD with wLength > MBOOT_DFU_XFER_SIZE returns negative and does not touch flash |
| test_3_dnload_padding | DNLOAD length that is not a multiple of 4 is padded with 0xFF (default MBOOT_DFU_WRITE_ALIGN) |
| test_4_implicit_erase | 4 sequential writes across 2 sectors give 2 erase calls |
| test_5_notify_set_interface | notify_set_interface clears the bitmap, so the same sector is erased again |
| test_6_mixed_sector_sizes_dnload | One alt of 4 x 4 KiB + 2 x 8 KiB regions: a sector of the second region is erased although a sector of the first at the same offset index is touched |
| test_7_mixed_sector_sizes_range_erase | Same alt: a range erase in the first region does not mark a sector of the second region as touched |

### test_vendor.c - vendor erase dispatch

| Name | Description |
|------|-------------|
| test_10_erase_one_sector | Vendor 0x80 with len=4096 gives 1 erase call |
| test_11_erase_sixteen_sectors | Vendor 0x80 with len=16*4096 gives 16 erase calls |
| test_12_mass_erase | Vendor 0x80 with len=0xFFFFFFFF erases every sector once (checked per sector) |
| test_13_out_of_range | An address outside any region (below and above) returns negative and erases nothing |
| test_14_unknown_opcode | Unknown vendor opcode 0x81 returns -EINVAL and changes nothing |
| test_34_erase_alt_nonzero | Vendor 0x80 with wValue=1 erases alt 1 only; alt 0 is untouched |
| test_34b_erase_alt_out_of_range | wValue >= region count returns -EINVAL |
| test_36_erase_bad_payload | A payload that is not 8 bytes returns -EINVAL |
| test_37_erase_zero_length | len=0 does nothing |

### test_region.c - region table and alt-string generation

| Name | Description |
|------|-------------|
| test_15_init_validation | Valid table returns 0; non-writable region returns -EINVAL |
| test_16_alt_string_64k | 64 KiB x 128 region gives the expected dfu-util alt string (permission character 'g') |
| test_17_alt_string_bytes | 256 B x 16 region uses the 'B' unit in the descriptor |
| test_18_set_active_out_of_range | set_active(N) with N >= region_count fails |
| test_19_sector_iter | sector_iter yields sector_count entries in ascending order |
| test_20_write_out_of_range | mboot_region_write rejects an address one byte below the region base |
| test_36_init_gap_rejected | A gap between same-name regions is rejected |
| test_37_multi_region_fold | Two adjacent same-name regions fold into one alt, with comma-separated geometry runs and the 'M' unit in the alt string |
| test_38_read_not_readable | mboot_region_read on a region without READABLE fails |
| test_39_init_group_sector_total | Two folded regions totalling MBOOT_DFU_MAX_SECTOR_COUNT sectors are accepted; one sector more is rejected |
| test_40_sector_index_mixed | mboot_region_sector_index numbers sectors across regions with different sector sizes and refuses addresses outside the alt |

### test_elem.c - element stream (TLV) parser

| Name | Description |
|------|-------------|
| test_21_empty_buffer | mboot_elem_search on a NULL or empty buffer returns NULL |
| test_22_only_end | Searching a stream that is only END returns NULL |
| test_23_truncated_header | buf_len=1 (truncated header) returns NULL |
| test_24_length_overflow | A declared payload length past the end of the buffer returns NULL |
| test_25_search_middle | Finds a type in the middle of a well-formed stream |
| test_26_validate | validate accepts a well-formed stream and rejects a missing END or an END with non-zero length |
| test_27_wire_numbers | END=1, MOUNT=2, FSLOAD=3, STATUS=4 on a raw stream as written by fwupdate.py, and type 0 is not a terminator |

### test_usbd.c - descriptors and USB callbacks

Built with `MBOOT_TESTS_FAKE_TUSB=1`, so `mboot_usbd.c` includes `fake_tusb.h` instead of the TinyUSB headers. `fake_tusb.{c,h}` has the few types `mboot_usbd.c` needs and recording stubs for `tud_control_xfer`, `tud_control_status` and `tud_dfu_finish_flashing`, plus `tud_mounted`.

| Name | Description |
|------|-------------|
| test_40_init | After mboot_usbd_init, mboot_usbd_leave_requested is false |
| test_41_set_alt_valid | tud_dfu_download_cb with a valid alt makes it the active region (the switch is detected in the callback) |
| test_42_set_alt_invalid | tud_dfu_download_cb with an out-of-range alt finishes with DFU_STATUS_ERR_TARGET, leaves the active alt alone and does not touch flash |
| test_43_set_alt_notify | A DNLOAD for another alt mid-session clears the touched-sector bitmap, so the first sector of the new alt is erased |
| test_44_download_cb | tud_dfu_download_cb erases and writes flash and calls tud_dfu_finish_flashing(OK); a second download to the same sector does not erase again |
| test_45_manifest_cb | tud_dfu_manifest_cb calls tud_dfu_finish_flashing(OK) |
| test_46_vendor_setup | Vendor opcode 0x80 at SETUP requests the 8-byte DATA stage |
| test_47_vendor_unknown_opcode | Opcode 0x81 with bmRequestType 0x41 (wrong direction) returns false (STALL) at SETUP |
| test_48_vendor_wrong_length | Vendor 0x80 with wLength != 8 returns false (STALL) |
| test_49_cfg_desc_1alt_len | Configuration descriptor with one alt is 27 bytes |
| test_50_cfg_desc_2alt_len | Configuration descriptor with two alts is 36 bytes |
| test_51_str_langid | String index 0 is the LANGID descriptor (0x0409) |
| test_52_str_alt | String index 4 (alt 0) is a valid USB string descriptor |
| test_53_str_alt_out_of_range | String index 5 with one alt returns NULL |

### test_hooks.c - hooks, read-only regions, status mapping

The `mboot_hook_*` functions record their calls and return configurable values. By default session begin and manifest accept and the result request stalls. `test_hooks` runs before `test_usbd` because a successful manifest in `test_45_manifest_cb` latches the leave request in `mboot_usbd.c` for the rest of the process.

| Name | Description |
|------|-------------|
| test_60_alt_order_and_read_only_init | Alts in descending address order, and a read-only alt on non-writable flash, initialise |
| test_61_overlap_rejected | Overlapping regions and a gap between same-name regions are rejected; non-overlapping regions with different names are accepted |
| test_62_read_only_region | Write and erase of a read-only region return -EACCES without touching flash, read works, and the alt strings end in 'a' and 'g' |
| test_63_read_only_part_of_alt | A write that reaches into a read-only part of a mixed alt is refused |
| test_64_session_begin_order | The session-begin hook runs before the first erase and write, once per session, and again after an alt switch or abort |
| test_65_session_begin_failure | A failing hook gives errERASE (DNLOAD) or a stall (vendor erase) without touching flash, and is retried |
| test_66_vendor_erase_hook | The hook runs before the first vendor erase, refused requests (read-only alt, start outside, wrapped range) change nothing, and a range running past the end erases the inside part and fails |
| test_67_cross_alt_erase_resets_state | Erasing an alt other than the session's does not leave touched bits behind |
| test_68_dnload_status | DNLOAD past the alt, block 65535 and the read-only alt give errADDRESS without calling the hook |
| test_69_manifest_hook | Hook status reaches tud_dfu_finish_flashing(); a failure does not request the leave |
| test_70_result_request | Vendor 0x81 gives a 16-byte reply, and stalls on the default hook, a wrong length, direction or interface, or a short reply |
| test_71_session_ends | A failed block, a rejected manifest and a bus reset each end the session; the next block runs the begin hook and erases again |
| test_72_bus_reset_is_polled | A bus reset is seen by polling mboot_usbd_leave_requested() through tud_mounted(); it requests the leave only after a successful manifest |

### test_align.c - MBOOT_DFU_WRITE_ALIGN (separate binary)

Built as `test_runner_align` with `-DMBOOT_DFU_WRITE_ALIGN=16` (it defines its own `mboot_hook_session_begin`). DNLOAD blocks are padded with 0xFF to 16 bytes and consecutive blocks stay aligned.

### glue/test_glue.c - shared/mboot/src/dfu_glue.c

`glue/Makefile` builds the glue with the NUCLEO-H563ZI configuration (its `mpconfigboard.h` and `ports/stm32/mboot/mcuboot/mboot_dev.h`, through `shared/mboot/mboot_common.mk`), so the layout, the device table (`flash_map.c`) and the DFU region and write range tables (`dfu_regions.c`) are the ones the real build uses. The slot policy comes from `BOARD`: `h5` (swap), `glue_ow` (overwrite-external) or `glue_single` (see `tests/mboot/host/boards.mk`), and `make test` runs all three. `glue/fake_mboot.c` is an in-memory Mboot environment around them: the 2 MiB flash device, a writable copy of the write range table that the tests can corrupt, and recording fakes for the update audit log, validation, the update slot functions, the port and the TinyUSB task. `glue/stubs/tusb.h` stubs the TinyUSB functions `dfu_glue.c` uses. The output is `glue/build/<BOARD>/test_glue` and, with the core built for a different write align, `glue/build/<BOARD>/test_glue_align_mismatch`.

| Name | Description |
|------|-------------|
| test_g1_regions_init | Tables are accepted; the image alt is 'g' and the log alt below it is 'a' |
| test_g2_regions_init_rejects | Rejected: boot area as a region, sector size mismatch, region off the device, image not inside a write range, empty write range table, write range beyond the device, misaligned, or on a bad device, and a trailer that DFU could write |
| test_g3_session_begin_order | Trailer sector, spare sector, DFU_BEGIN log, then the core's erase and write, once per session |
| test_g4_begin_failure | A failed trailer erase gives errERASE, is logged and reported by vendor 0x81, and the next block retries |
| test_g5_trailer_sectors | A trailer spanning three sectors is erased last sector first |
| test_g6_read_only_alt | Log alt: write gives errADDRESS, mass and range erase stall, upload returns the log, no flash writes or erases |
| test_g7_dnload_confinement | The last block is accepted; the first block past the end, a far block and block 65535 are refused; nothing outside the slot changes |
| test_g8_shims_refuse_outside | The flash shims refuse boot, primary, seccnt, shadow, FS, log, gaps, device ends, spanning and wrapping requests, without any flash operation |
| test_g9_mass_erase | Mass erase blanks the data region (plus the begin-hook sectors inside the slot) and nothing outside the slot |
| test_g10_range_erase | Ranges starting outside the alt, or wrapping, are refused and change nothing; a range running past the end erases only the inside part |
| test_g11_manifest_rejected | Each validation result code maps to its DFU status; the image header is erased, nothing is marked pending, and the rejection is logged and reported by 0x81 without requesting the leave |
| test_g12_pending_failure | A failed mark-pending gives errWRITE, is logged as ERR_PENDING, and the header is kept |
| test_g13_result_request | The 0x81 reply is 16 bytes, little endian; a wrong length or direction stalls |
| test_g14_target_view | The target view (the update slot without its spare sector) matches the DFU region |
| test_g15_timeout | Idle timeout for forced and app-requested recovery, none for no-image, fsload-failed and fault. Activity restarts it and an open write session suppresses it |
| test_g16_init_failure | Inconsistent tables end in a reset without running the USB loop |
| test_g17_manifest_accepted_and_leave | Good image: validate, mark pending (a test swap for the swap policy, permanent for overwrite-external), log; the loop leaves by reset |
| test_g18_trailer_confinement | The update slot trailer is refused by the shims, blocks and range erases, and is erased only once, at session begin |

### test_pydfu_wire.py - pydfu wire-protocol contract test

Checks the `ctrl_transfer` arguments `tools/pydfu.py` sends for `mass_erase()`, `page_erase()` and `exit_dfu()`, in the default (pure DFU 1.1 with vendor erase) and `--dfuse` modes. Runs with plain `python3`; `usb.core` and `usb.util` are mocked, so pyusb is not needed.

## Adding a test

Add a `static int test_<name>(int *failures)` function to the matching `test_<module>.c` and a `RUN_TEST(test_<name>);` line in that module's `test_<module>()` entry point. `test_main.c` and the Makefile do not change. The assertion macros are in `test_main.h`:

- `TEST_ASSERT(expr)` - fail if expr is false; return -1.
- `TEST_ASSERT_EQ(a, b)` - fail if a != b; print both values.
- `TEST_ASSERT_NULL(ptr)` - fail if ptr != NULL.
- `TEST_ASSERT_NOTNULL(ptr)` - fail if ptr == NULL.
- `TEST_ASSERT_GE(a, b)` - fail if a < b.

## Region table configuration

Tests call `TEST_REGIONS_SET(array, count)` (from `test_regions_config.h`) to install a region table before each `mboot_region_init()`. It copies the array into the table that the test version of `mboot_port_get_regions()` returns.

## Coverage notes

Lines the host tests do not reach:

- `block_to_addr()` overflow path (`result < base`). The region bounds check rejects the block before `base + wBlockNum * XFER_SIZE` can overflow a 32-bit address.
- `bitmap_set()` guard `sector_idx < MBOOT_DFU_MAX_SECTOR_COUNT`. `mboot_region_init()` already rejects an alt setting with more sectors than that.
- `mboot_region_init()` check for `s_regions == NULL` after `mboot_port_get_regions()`. The test version never returns NULL.

A real bus reset after a manifest is not modelled on the host. The stubs fake it with `fake_tusb_mounted` (`test_72_bus_reset_is_polled`, `test_g17_manifest_accepted_and_leave`); `tests/mboot/hw/dfu_confine.py` and `tests/mboot/dfu_raw.py` do it against a board.
