/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_API_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_API_H

// mboot_api.h — port-binding contract for shared/tinyusb/mboot
//
// This header is the sole interface between the port-agnostic common code
// (shared/tinyusb/mboot/common/) and the per-port adapter that links against
// it.  Common code must not include py/, extmod/, shared/runtime/, tusb.h, or
// any port-specific header.  This file intentionally depends only on stdint.h,
// stddef.h, and stdbool.h so that it compiles with a libc-only include path
// and -ffreestanding.
//
// Design notes
// ------------
// The contract deliberately mirrors the user-experience reference in
// ports/stm32/mboot/ while being independent of its implementation.
//
// TinyUSB submodule note: the lib/tinyusb submodule is not required to compile
// this header or the common/*.c sources.  It is only needed by the per-port
// adapter that wires the DFU class callbacks into the TinyUSB device stack.
// Stage 0 common code compiles against libc only; TinyUSB is integrated in
// Stage 1 per-port adapters.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// Address type
// ---------------------------------------------------------------------------

// mboot_addr_t is the address type used for all flash operations.
//
// By default it is uint32_t.  Define MBOOT_ADDRESS_SPACE_64BIT=1 before
// including this header (or in the build flags) to widen it to uint64_t.
// This mirrors the pattern in ports/stm32/mboot/mboot.h:81-83 and is needed
// when a port maps storage (e.g. SD card) above the 4 GiB boundary.

#ifndef MBOOT_ADDRESS_SPACE_64BIT
#define MBOOT_ADDRESS_SPACE_64BIT (0)
#endif

#if MBOOT_ADDRESS_SPACE_64BIT
typedef uint64_t mboot_addr_t;
#else
typedef uint32_t mboot_addr_t;
#endif

// ---------------------------------------------------------------------------
// Reset-mode enum
// ---------------------------------------------------------------------------

// mboot_reset_mode_t — values returned by mboot_port_get_reset_mode().
//
// MBOOT_RESET_MODE_NORMAL:       jump to the application after bootloader
//                                logic completes (or immediately if no DFU
//                                activity and entry was not forced).
// MBOOT_RESET_MODE_BOOTLOADER:   stay in the bootloader and wait for a DFU
//                                transfer over USB.
// MBOOT_RESET_MODE_BOOTLOADER_FSLOAD: stay in the bootloader and attempt to
//                                load firmware from a filesystem (TLV element
//                                data passed via retention RAM), then jump to
//                                the application.
typedef enum {
    MBOOT_RESET_MODE_NORMAL = 0,
    MBOOT_RESET_MODE_BOOTLOADER = 1,
    MBOOT_RESET_MODE_BOOTLOADER_FSLOAD = 2,
} mboot_reset_mode_t;

// ---------------------------------------------------------------------------
// noreturn attribute
// ---------------------------------------------------------------------------

// MBOOT_NORETURN — marks a function that never returns.
// Defined independently of py/mpconfig.h so this header remains self-contained.
#ifndef MBOOT_NORETURN
#define MBOOT_NORETURN __attribute__((noreturn))
#endif

// ---------------------------------------------------------------------------
// Region descriptor
// ---------------------------------------------------------------------------

// Flags for mboot_region_t.flags.
#define MBOOT_REGION_FLAG_READABLE                  (0x01u)
#define MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE (0x02u)

// mboot_region_t — describes one contiguous, programmable memory region.
//
// Regions must not overlap.  A device with non-uniform sector geometry (e.g.
// STM32H7 with 4×16K + 1×64K + 7×128K) is expressed as multiple adjacent
// regions, one per uniform run.  The dfu-util interface string for the combined
// memory unit is built by mboot_region.c as a comma-joined list.  Each
// region's sector_count * sector_size must equal size.
//
// Fields
//   addr         Start address of the region.
//   size         Total byte size of the region.
//   sector_size  Erase granularity in bytes.  Must be a power of two.
//                dfu-util reads this to build the interface-string layout
//                descriptor, e.g. "8*004Kg" means 8 sectors of 4 KiB each.
//   sector_count Number of erasable sectors in this region (== size / sector_size).
//                Carried explicitly so the interface-string generator in
//                mboot_region.c does not have to divide.
//   name         Human-readable name used as the DFU interface string prefix,
//                e.g. "@Internal Flash" or "@QSPI Flash".  Must be a string
//                literal or a pointer that remains valid for the lifetime of
//                the bootloader.
//   flags        Bitmask of MBOOT_REGION_FLAG_* values.
typedef struct {
    mboot_addr_t addr;
    mboot_addr_t size;
    uint32_t sector_size;
    uint32_t sector_count;
    const char *name;
    uint8_t flags;
} mboot_region_t;

// mboot_port_get_regions — return the port's region table.
//
// Called once at bootloader init from mboot_region_init().  The returned
// pointer and count must remain valid for the lifetime of the bootloader;
// they are typically backed by a static const array compiled into the port.
//
//   regions_out  Receives the pointer to the region table (array of
//                mboot_region_t in ascending address order).
//   count_out    Receives the number of entries in the table.
//
// Port implementation note: define this function in the per-port adapter
// source file.  Typical pattern:
//
//   static const mboot_region_t k_regions[] = { ... };
//   void mboot_port_get_regions(const mboot_region_t **r, size_t *c) {
//       *r = k_regions;
//       *c = sizeof(k_regions) / sizeof(k_regions[0]);
//   }
extern void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out);

// ---------------------------------------------------------------------------
// Programmatic entry magic
// ---------------------------------------------------------------------------

// MBOOT_INITIAL_R0_KEY — magic value written by the application to a
// retention register before triggering a reset to signal that mboot should
// enter DFU mode rather than jumping to the application immediately.
//
// Per-port retention registers:
//   rp2:    watchdog_hw->scratch[0]
//   mimxrt: SNVS->LPGPR[3]
//
// This constant is the single canonical definition; all per-port board
// headers and app-side entry helpers must include this header rather than
// defining their own copy.
#define MBOOT_INITIAL_R0_KEY (0x70AD0000u)

// ---------------------------------------------------------------------------
// Vendor request opcode reservation
// ---------------------------------------------------------------------------

// DFU vendor requests use bRequestType = 0x41 (host-to-device, class, interface).
// The mboot extension protocol reserves opcodes 0x80..0x8F for its own use.
//
//   0x80  MBOOT_VREQ_ERASE — vendor erase request.
//           Payload: <addr:4 or 8 bytes LE> <length:4 or 8 bytes LE>
//           If length == 0xFFFFFFFF (32-bit) or 0xFFFFFFFFFFFFFFFF (64-bit),
//           the request is a mass-erase of the entire writable address space.
//           Otherwise, the request erases the sectors that contain the
//           half-open range [addr, addr+length).  Erase is sector-aligned;
//           the common code rounds addr down and addr+length up to sector
//           boundaries before dispatching to mboot_port_flash_page_erase.
//   0x81..0x8F  Reserved for future mboot extensions.
//
// DfuSe block-0 commands (0x21 = set-address, 0x41 = erase-page) carried
// inside DFU_DNLOAD payloads (ports/stm32/mboot/main.c:947-965) are
// intentionally NOT supported by this implementation.  Use --dfuse in
// tools/pydfu.py to communicate with legacy stm32/mboot devices.

#define MBOOT_VREQ_ERASE (0x80u)

// ---------------------------------------------------------------------------
// Lifecycle callbacks
// ---------------------------------------------------------------------------

// mboot_port_early_init — first port code to run.
//
// Called before USB initialisation, before any region is accessed, and before
// mboot_port_get_reset_mode.  Use this to configure clocks, watchdogs, and
// the retention-RAM region if programmatic entry (initial_r0 magic value) is
// supported.
//
// Arguments
//   initial_r0   Value of CPU register r0 at reset (or equivalent).  Non-zero
//                values signal programmatic bootloader entry from the running
//                application; see ports/stm32/mboot/mboot.h for the MBOOT_INITIAL_R0_KEY
//                constants.  Ports that do not support programmatic entry may
//                ignore this argument.
//
// Call context: called once at startup, before interrupts are enabled.
// Return: void.
extern void mboot_port_early_init(uint32_t *initial_r0);

// mboot_port_entry_forced — board-level forced-entry check.
//
// Returns true if a hardware signal (e.g. a dedicated BOOT pin held low, or a
// magic value written to retention RAM by the running application) indicates
// that the bootloader must remain active regardless of application validity.
//
// Call context: called once after mboot_port_early_init, before USB init.
// Not interrupt-safe; must not block.
// Return: true to force bootloader mode, false to apply normal entry logic.
extern bool mboot_port_entry_forced(void);

// mboot_port_app_valid — check whether the application vector table is valid.
//
// Inspects the initial SP and reset-handler entries in the application's vector
// table (at APPLICATION_ADDR) and returns true if they look plausible.  Used
// by the main loop to decide whether to attempt a jump to the application, and
// by mboot_dbl_tap_check to skip the double-tap window when no application is
// present (so the device enters DFU immediately rather than waiting 500 ms).
//
// Call context: may be called before USB init; must be non-blocking.
// Return: true if the application vector table appears valid.
extern bool mboot_port_app_valid(void);

// mboot_port_delay_ms — busy-wait for at least the given number of milliseconds.
//
// Used by mboot_dbl_tap_check() to hold the double-tap detection window open.
// Implementations should be accurate to within a few percent; exact precision
// is not required.
//
// Call context: called during early init before USB is started; must not yield
// or sleep (no RTOS, no WFI that requires an interrupt to wake).
extern void mboot_port_delay_ms(uint32_t ms);

// mboot_port_get_reset_mode — board UI for selecting the operating mode.
//
// Common code calls this once after mboot_port_entry_forced.  The port
// implements whatever user interaction is appropriate (e.g. read a button
// during a short window, inspect a GPIO, decode a retention-RAM value) and
// returns one of the MBOOT_RESET_MODE_* values.
//
// Call context: called once; may poll hardware for a bounded time window.
// Not interrupt-safe.
// Return: mboot_reset_mode_t value.
extern int mboot_port_get_reset_mode(void);

// mboot_port_cleanup — undo peripherals before leaving the bootloader.
//
// Called after DFU activity is complete (or immediately if
// MBOOT_RESET_MODE_NORMAL) and before either a jump to the application or a
// system reset.  The port should disable USB, restore clock configuration, and
// reset IRQ priorities to the state the application expects.
//
// Arguments
//   reset_mode   The mode that was selected; informs the port whether the
//                cleanup is before an app jump (NORMAL or FSLOAD) or before
//                a self-reset (BOOTLOADER if the host requested DETACH).
//
// Call context: called once; interrupts may be left in either state;
// mboot_port_leave is responsible for any final CPU-state setup.
//
// Required: on the fast path (valid app + no force-entry) main.c calls
// mboot_port_cleanup BEFORE tusb_init() has run.  Any tud_disconnect /
// tud_deinit call MUST be guarded with `if (tud_inited()) { ... }`; without
// the guard, tud_disconnect dereferences _usbd_rhport (RHPORT_INVALID until
// tud_init runs) and faults inside dcd_disconnect.
//
// Return: void.
extern void mboot_port_cleanup(int reset_mode);

// mboot_port_leave — perform the final jump or reset.
//
// Called after mboot_port_cleanup.  The port must not return.  Typical
// implementations either relocate the vector table and branch to the
// application's reset handler, or call NVIC_SystemReset().
//
// Arguments
//   reset_mode   As passed to mboot_port_cleanup.
//
// Call context: called once; the port implementation is responsible for
// disabling interrupts (or any other CPU-state setup) before the jump or reset.
//
// App-image layout requirement: the jump target must be a Cortex-M vector
// table (initial MSP in word 0, Reset_Handler with Thumb bit in word 1).
// If the port's toolchain or BSP emits a preamble at the start of the
// application image (rp2 ships a 256-byte `.boot2` second-stage XIP setup
// stub; mimxrt ships an FCB at +0x400 plus an IVT at +0x1000), the port
// must either drop the preamble from the app linker output or advance the
// jump address past it.  mboot_port_app_valid must use the SAME effective
// address as mboot_port_leave so the validity check and the jump agree.
//
// Return: never.
extern MBOOT_NORETURN void mboot_port_leave(int reset_mode);

// ---------------------------------------------------------------------------
// RAM-resident function attribute
// ---------------------------------------------------------------------------
//
// MBOOT_RAM_FUNC_ATTR — attribute applied to shared functions that may run
// while flash XIP is suspended (e.g. inside a flash-busy window).
//
// Each port whose flash backend suspends XIP during write/erase MUST define
// this macro to a section attribute (or equivalent) understood by its
// linker.  Currently shipped:
//
//   rp2     — `__not_in_flash("mboot")` (pico-sdk macro, collected into RAM
//             by ports/rp2/memmap_mp_rp2*.ld via `.time_critical*`).
//   mimxrt  — `__attribute__((section(".ram_functions")))` (collected into
//             ITCM by ports/mimxrt/boards/common.ld).
//
// The default below is empty, so a port that does NOT need RAM placement
// (e.g. a port whose flash backend lets the CPU keep fetching during writes)
// can compile the shared code unchanged.  A port whose flash backend DOES
// suspend XIP but neglects to define MBOOT_RAM_FUNC_ATTR will silently leave
// the annotated shared functions in flash; the resulting hazard is a hard
// fault on the first call path that runs them inside the busy window.
#ifndef MBOOT_RAM_FUNC_ATTR
#define MBOOT_RAM_FUNC_ATTR
#endif

// ---------------------------------------------------------------------------
// Flash backend callbacks
// ---------------------------------------------------------------------------
//
// Common code treats any non-zero return as a failure and reports
// DFU_STATUS_ERR_WRITE or DFU_STATUS_ERR_ERASE to the host as appropriate;
// the specific negative value is not interpreted.  Adapters whose underlying
// HAL cannot signal errors (e.g. rp2 flash_range_erase/flash_range_program
// which return void) may return 0 unconditionally.

// mboot_port_flash_is_writable — region-level allow/deny.
//
// Called by common code before dispatching any erase or write operation.
// The port must reject any range that overlaps with bootloader flash.
//
// Arguments
//   addr   Start of the range to check.
//   len    Byte length of the range.
//
// Call context: not interrupt-safe; may be called repeatedly during a DFU
// download session.
// Return: true if the range is entirely within a writable region, false
// otherwise.  Common code returns DFU_STATUS_ERR_ADDRESS to the host on false.
extern bool mboot_port_flash_is_writable(mboot_addr_t addr, size_t len);

// mboot_port_flash_page_erase — erase one sector.
//
// Erases the sector that contains addr.  The sector need not be aligned on
// addr; the port is responsible for rounding down to the sector boundary.
// On return, *next_addr is set to the address of the first byte past the
// erased sector (addr_of_sector + sector_size).  This allows the caller to
// iterate over a range by repeatedly calling this function with the returned
// *next_addr as the new addr.
//
// Alignment contract: addr must be within a valid, writable region as
// determined by mboot_port_flash_is_writable.
//
// Arguments
//   addr         Address within the sector to erase.
//   next_addr    Out-pointer; receives the start address of the next sector.
//                Must not be NULL.
//
// Call context: not interrupt-safe; may block for the duration of the erase
// (typically 10–100 ms per sector for NOR flash).
// Return: 0 on success, negative errno-style value on error (e.g. -ETIMEDOUT,
// -EIO).
extern int mboot_port_flash_page_erase(mboot_addr_t addr, mboot_addr_t *next_addr);

// mboot_port_flash_write — program previously erased flash.
//
// Writes len bytes from src to flash starting at addr.  Pages must already
// be erased; common code (mboot_region.c, Phase 2) buffers partial
// sector-size transfers and calls mboot_port_flash_page_erase before invoking
// this function.
//
// Alignment requirements:
//   - addr must be 4-byte (word) aligned.
//   - len must be a multiple of 4 bytes.
//   - The addressed range must lie within a single contiguous erased sector,
//     or the port must handle cross-sector writes internally.
//   Partial-word writes (len not a multiple of 4) are NOT supported.  Common
//   code pads the trailing bytes to 0xFF before calling this function.
//
// Note: rp2 flash_range_program() requires 256-byte page alignment and a
// 256-byte-multiple length.  The rp2 adapter must further align or iterate
// internally; the common code only guarantees 4-byte alignment.
//
// Arguments
//   addr   Destination start address (4-byte aligned).
//   src    Source buffer in RAM (must not alias flash).
//   len    Number of bytes to write (multiple of 4).
//
// Call context: not interrupt-safe; may block.
// Return: 0 on success, negative errno-style value on error.
extern int mboot_port_flash_write(mboot_addr_t addr, const uint8_t *src, size_t len);

// mboot_port_flash_read — read from flash (used for DFU_UPLOAD).
//
// Reads len bytes from flash at addr into dst.  The region must be readable
// (MBOOT_REGION_FLAG_READABLE set in the region descriptor).
//
// Arguments
//   addr   Source start address.
//   dst    Destination buffer in RAM.
//   len    Number of bytes to read.
//
// Call context: not interrupt-safe; may use memcpy on XIP-mapped flash.
// Return: 0 on success, negative errno-style value on error.
extern int mboot_port_flash_read(mboot_addr_t addr, uint8_t *dst, size_t len);

// ---------------------------------------------------------------------------
// USB identity callbacks
// ---------------------------------------------------------------------------

// mboot_port_get_serial_number — write the USB serial number string.
//
// The port writes a NUL-terminated ASCII string of up to (buf_len - 1)
// characters into buf.  buf_len includes the NUL terminator, so a buf_len of
// 33 accommodates a 32-character hexadecimal serial number.
//
// Deviation from mp_usbd_port_get_serial_number(char *buf)
// (shared/tinyusb/mp_usbd.h:85): the existing function relies on
// MICROPY_HW_USB_DESC_STR_MAX as an implicit bound and is therefore unsafe to
// call without the MicroPython VM headers.  This bootloader variant takes an
// explicit buf_len so common code can call it with a local buffer without
// depending on py/ or the MICROPY_HW_USB_DESC_STR_MAX constant.
//
// Arguments
//   buf      Buffer to receive the NUL-terminated serial string.
//   buf_len  Size of buf in bytes, including the NUL terminator.
//
// Call context: called once during USB descriptor initialisation; not
// interrupt-safe.
// Return: void.
extern void mboot_port_get_serial_number(char *buf, size_t buf_len);

// mboot_port_get_product_string — return the USB product string.
//
// Returns a pointer to a NUL-terminated ASCII string used as the USB device
// product descriptor.  The string must remain valid for the lifetime of the
// bootloader (string literal or static storage).
//
// Call context: called once during USB descriptor initialisation.
// Return: non-NULL pointer to a NUL-terminated string.
extern const char *mboot_port_get_product_string(void);

// mboot_port_get_vid — return the USB Vendor ID.
//
// Call context: called once during USB descriptor initialisation.
// Return: 16-bit USB VID.
extern uint16_t mboot_port_get_vid(void);

// mboot_port_get_pid — return the USB Product ID.
//
// Call context: called once during USB descriptor initialisation.
// Return: 16-bit USB PID.
extern uint16_t mboot_port_get_pid(void);

// ---------------------------------------------------------------------------
// UI callbacks
// ---------------------------------------------------------------------------

// mboot_port_led_init — initialise LED GPIO(s).
//
// Called once during mboot_port_early_init or shortly after.  Common code
// does not define when exactly; the port should call this from
// mboot_port_early_init if LEDs share GPIO clocks with other peripherals.
//
// Call context: called once before the main bootloader loop.
// Return: void.
extern void mboot_port_led_init(void);

// mboot_port_led_set — set the LED state bitmask.
//
// Each bit in led_mask corresponds to one LED:
//   bit 0 -> LED0, bit 1 -> LED1, etc.
// A set bit means the LED should be on; a clear bit means off.
// Common code uses this for DFU activity indication; port UI code may also
// call it independently.
//
// Arguments
//   led_mask   Bitmask of LEDs to illuminate.
//
// Call context: may be called from the main loop and from systick handlers;
// must be interrupt-safe (GPIO register write is typically atomic).
// Return: void.
extern void mboot_port_led_set(unsigned int led_mask);

// mboot_port_button_pressed — read the user/boot button.
//
// Returns true if the board's primary user or boot button is currently
// pressed.  Common code does not call this in Stage 0 (where entry is
// determined by mboot_port_get_reset_mode), but the contract is locked here
// for Stage 1 enter-on-boot logic.
//
// Call context: may be called repeatedly from the main loop; must not block.
// Not required to be interrupt-safe.
// Return: true if pressed, false otherwise.
extern bool mboot_port_button_pressed(void);

// ---------------------------------------------------------------------------
// Per-port implementation walkthrough
// ---------------------------------------------------------------------------
//
// The following block documents how a hypothetical rp2 and mimxrt adapter
// would implement each callback.  This walkthrough serves as the acceptance
// proof for Stage 0 criterion 10: if any callback would require a contract
// change to implement on either target, the contract is incomplete.
//
// RP2 ADAPTER
// -----------
// Source would live at ports/rp2/mboot/mboot_port.c.
// Required includes: hardware/flash.h, pico/flash.h, hardware/sync.h,
//                    hardware/regs/addressmap.h, pico/unique_id.h.
//
// mboot_port_early_init
//   Call board_init() (or the subset of it relevant to clocks/watchdog).
//   Decode initial_r0 magic for programmatic entry if supported.
//   RP2040/RP2350 reset via watchdog; no NVIC_SystemReset equivalent.
//
// mboot_port_entry_forced
//   Read a board-defined GPIO (e.g. BOOTSEL on RP2040 via
//   PADS_QSPI / GPIO_QSPI registers, or a dedicated MBOOT_BOOTPIN).
//   Return true if the pin is in its active state.
//
// mboot_port_get_reset_mode
//   Sample mboot_port_button_pressed() for a configurable window (e.g.
//   500 ms).  Return MBOOT_RESET_MODE_BOOTLOADER if held, otherwise
//   MBOOT_RESET_MODE_NORMAL.
//
// mboot_port_cleanup
//   Disable USB (tud_disconnect + deinit).
//   No special clock teardown needed on rp2; USB PLL is shared.
//
// mboot_port_leave
//   Disable interrupts, reset watchdog, jump to application vector table
//   using direct VTOR relocation (RP2040 Cortex-M0+ and RP2350 both support
//   VTOR; pico-sdk exposes it as scb_hw->vtor) or the watchdog-reset +
//   scratch-RAM technique used by tinyuf2 on RP2040.  Both approaches are
//   adapter-internal; no contract change is required.  The application base
//   address is expressed as a linker symbol or board macro.
//
// mboot_port_flash_is_writable
//   Reject any range that overlaps [XIP_BASE, XIP_BASE + mboot_end_offset).
//   mboot_end_offset is a linker-symbol or board constant.
//   Return true for ranges within [XIP_BASE + mboot_end_offset, XIP_BASE +
//   PICO_FLASH_SIZE_BYTES).
//
// mboot_port_flash_page_erase
//   Compute flash_offset = addr - XIP_BASE.
//   Round flash_offset down to FLASH_SECTOR_SIZE (4096) boundary.
//   Call flash_range_erase(flash_offset, FLASH_SECTOR_SIZE) from hardware/flash.h.
//   This function must run from RAM with interrupts disabled; the adapter
//   wraps it with save_and_disable_interrupts / restore_interrupts and, if
//   core1 is active, multicore_lockout_start_blocking /
//   multicore_lockout_end_blocking (as in ports/rp2/rp2_flash.c).
//   Set *next_addr = (addr & ~(FLASH_SECTOR_SIZE - 1)) + FLASH_SECTOR_SIZE + XIP_BASE.
//   Return 0 on success (flash_range_erase has no return value; errors manifest
//   as a hang — no negative errno is possible without a timeout).
//   CONTRACT GAP (rp2): flash_range_erase() has no error return.  The
//   adapter cannot distinguish a successful erase from a timed-out erase.
//   Recommend treating void return as 0 and relying on the subsequent
//   mboot_port_flash_read verify step (if implemented) to detect failure.
//
// mboot_port_flash_write
//   Compute flash_offset = addr - XIP_BASE.
//   Call flash_range_program(flash_offset, src, len) from hardware/flash.h.
//   flash_range_program requires len to be a multiple of FLASH_PAGE_SIZE (256)
//   and flash_offset to be FLASH_PAGE_SIZE-aligned.
//   The common code guarantees only 4-byte alignment.  The rp2 adapter must
//   pad src into a 256-byte-aligned staging buffer in RAM before calling
//   flash_range_program, iterating in 256-byte chunks.
//   Wrap with the same interrupt/multicore lockout as erase.
//   CONTRACT NOTE: the staging buffer requirement means the rp2 adapter needs
//   ~256 bytes of RAM scratch.  This is adapter-internal state; no contract
//   change is needed.
//
// mboot_port_flash_read
//   XIP flash is memory-mapped at XIP_BASE.  Use memcpy(dst,
//   (void *)(XIP_BASE + (addr - XIP_BASE)), len) — i.e. a direct pointer
//   dereference.  No HAL call required.
//
// mboot_port_get_serial_number
//   Call pico_get_unique_board_id_string(buf, buf_len) from pico/unique_id.h.
//   This writes a hex string of the 64-bit flash unique ID; it respects the
//   buffer length.  No contract change required.
//
// mboot_port_get_product_string
//   Return a board-defined string literal, e.g. "RP2040 Bootloader".
//
// mboot_port_get_vid / mboot_port_get_pid
//   Return board-configured constants (e.g. MICROPY_HW_USB_VID /
//   MICROPY_HW_USB_PID from mpconfigboard.h compiled into the adapter).
//
// mboot_port_get_regions
//   Define a static const array in the adapter source and return a pointer
//   to it:
//     static const mboot_region_t k_regions[] = {
//         { .addr = XIP_BASE + mboot_end_offset,
//           .size = PICO_FLASH_SIZE_BYTES - mboot_end_offset,
//           .sector_size = FLASH_SECTOR_SIZE,
//           .sector_count = (PICO_FLASH_SIZE_BYTES - mboot_end_offset)
//                          / FLASH_SECTOR_SIZE,
//           .name = "@RP2 Flash",
//           .flags = MBOOT_REGION_FLAG_READABLE |
//                    MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE },
//     };
//     void mboot_port_get_regions(const mboot_region_t **r, size_t *c) {
//         *r = k_regions;
//         *c = sizeof(k_regions) / sizeof(k_regions[0]);
//     }
//
// mboot_port_led_init
//   Configure the board LED GPIO as output using gpio_init() + gpio_set_dir()
//   from hardware/gpio.h.
//
// mboot_port_led_set
//   For each LED bit, call gpio_put(pin, (led_mask >> n) & 1).
//
// mboot_port_button_pressed
//   Read the board's boot/user button via gpio_get(MBOOT_BOOTPIN).
//
// MIMXRT ADAPTER
// --------------
// Source would live at ports/mimxrt/mboot/mboot_port.c.
// Required includes: fsl_common.h, fsl_clock.h, fsl_iomuxc.h,
//                    hal/flexspi_nor_flash.h, hal/flexspi_flash_config.h,
//                    CMSIS core_cm7.h (for SCB->VTOR and __DSB/__ISB).
//
// mboot_port_early_init
//   Configure system clocks via CLOCK_Init (board-specific), enable FLEXSPI
//   peripheral clocks, configure IOMUXC pin mux for FLEXSPI.
//   Initialise the FLEXSPI NOR flash configuration via
//   flexspi_nor_flash_init (board-specific wrapper that loads qspiflash_config).
//   Decode initial_r0 if programmatic entry is supported.
//
// mboot_port_entry_forced
//   Sample MBOOT_BOOTPIN GPIO via GPIO_PinRead (fsl_gpio.h).
//
// mboot_port_get_reset_mode
//   Same button-sampling pattern as rp2.
//
// mboot_port_cleanup
//   Disable USB controller (USB_DeviceStop from usb_device_config.h if
//   TinyUSB is active; otherwise direct register write to disable USB_OTG1).
//   Disable FLEXSPI clocks to return flash to a clean state for the app.
//
// mboot_port_leave
//   Disable interrupts (__disable_irq()).
//   Set SCB->VTOR to the application's vector table address.
//   Load MSP from the application's initial stack pointer (first word at
//   app_base).
//   Branch to the application reset handler (second word at app_base).
//   All three steps in inline asm or via a C trampoline marked __attribute__
//   ((naked)).  No contract gap — VTOR relocation is straightforward on
//   Cortex-M7.
//
// mboot_port_flash_is_writable
//   Reject ranges within [FlexSPI1_AMBA_BASE, FlexSPI1_AMBA_BASE +
//   mboot_size).  Accept ranges within the remainder of the flash mapping.
//
// mboot_port_flash_page_erase
//   Convert the XIP address to a FLEXSPI offset:
//     flash_offset = addr - FlexSPI1_AMBA_BASE (or FlexSPI_AMBA_BASE).
//   Call flexspi_nor_flash_erase_sector(BOARD_FLEX_SPI, flash_offset) from
//   ports/mimxrt/hal/flexspi_nor_flash.h.
//   flexspi_nor_flash_erase_sector returns an NXP status_t (kStatus_Success ==
//   0, other values are errors).  Map non-zero to -EIO.
//   Set *next_addr = (addr & ~(sector_size - 1)) + sector_size, where
//   sector_size comes from qspiflash_config.sectorSize.
//
// mboot_port_flash_write
//   Convert address to FLEXSPI offset.
//   Call flexspi_nor_flash_page_program(BOARD_FLEX_SPI, flash_offset,
//     (const uint32_t *)src, len) from ports/mimxrt/hal/flexspi_nor_flash.h.
//   flexspi_nor_flash_page_program requires src to be a uint32_t pointer and
//   len to be in bytes; the cast from uint8_t* is safe provided addr is
//   4-byte aligned (guaranteed by this contract).
//   The NXP page-program function handles the 256-byte page boundary split
//   internally for the RT1xxx series — no extra staging buffer needed.
//   Map non-kStatus_Success return to -EIO.
//   CONTRACT NOTE: flexspi_nor_flash_page_program takes a uint32_t *src.
//   The contract passes uint8_t *src.  The rp2 adapter is unaffected, but the
//   mimxrt adapter must ensure 4-byte alignment before the cast.  The
//   contract already requires 4-byte-aligned addr, so the cast is valid.
//
// mboot_port_flash_read
//   XIP flash is accessible via AHB at FlexSPI1_AMBA_BASE.
//   Use memcpy(dst, (void *)addr, len) — direct pointer dereference.
//   Invalidate the FLEXSPI AHB buffer after any preceding write:
//     call FLEXSPI_SoftwareReset(BOARD_FLEX_SPI) if reading immediately after
//     a program operation to avoid stale cache data.
//
// mboot_port_get_serial_number
//   Read the 16-byte UID from OCOTP fuse bank via OCOTP_ReadFuseShadowRegister
//   or directly from OCOTP_BASE + offset.  Format as a 32-hex-character string
//   using snprintf(buf, buf_len, "%08lX%08lX%08lX%08lX", uid[0], uid[1],
//   uid[2], uid[3]).  No contract change required.
//
// mboot_port_get_product_string / mboot_port_get_vid / mboot_port_get_pid
//   Same pattern as rp2: return board-defined constants.
//
// mboot_port_get_regions
//   Define a static const array and return it:
//     static const mboot_region_t k_regions[] = {
//         { .addr = FlexSPI1_AMBA_BASE + mboot_size,
//           .size = BOARD_FLASH_SIZE - mboot_size,
//           .sector_size = qspiflash_config.sectorSize,
//           .sector_count = (BOARD_FLASH_SIZE - mboot_size)
//                          / qspiflash_config.sectorSize,
//           .name = "@MIMXRT Flash",
//           .flags = MBOOT_REGION_FLAG_READABLE |
//                    MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE },
//     };
//     void mboot_port_get_regions(const mboot_region_t **r, size_t *c) {
//         *r = k_regions;
//         *c = sizeof(k_regions) / sizeof(k_regions[0]);
//     }
//
// mboot_port_led_init / mboot_port_led_set
//   Use GPIO_PinWrite (fsl_gpio.h) with board-configured pin definitions.
//
// mboot_port_button_pressed
//   GPIO_PinRead on the board's user/boot button GPIO.
//
// CONTRACT GAPS IDENTIFIED DURING WALKTHROUGH
// --------------------------------------------
// 1. rp2 app jump: rp2 may use either direct VTOR relocation (analogous to
//    stm32 mboot's leave_bootloader at ports/stm32/mboot/main.c:1499) or the
//    watchdog-reset + scratch-RAM technique that tinyuf2 uses on RP2040.  Both
//    are adapter-internal and require no contract change.  The application base
//    address is the adapter's concern, expressed as either a linker symbol
//    (stm32's APPLICATION_ADDR pattern) or a board macro.
// 2. rp2 flash erase/program error return: flash_range_erase() and
//    flash_range_program() return void.  The adapter cannot signal -EIO.
//    Recommend treating void as success (0) and adding a post-write verify
//    option in a future contract revision.  Not a Stage 0 blocker.
// 3. mimxrt FLEXSPI AHB cache invalidation: reading flash immediately after
//    a write without FLEXSPI_SoftwareReset may return stale data.  Common
//    code does not call mboot_port_flash_read after a write in normal DFU
//    flow; if DFU_UPLOAD is requested after DNLOAD, the adapter must handle
//    the cache invalidation internally.  No contract change needed.

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_API_H
