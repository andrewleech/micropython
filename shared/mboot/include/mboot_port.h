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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_PORT_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MBOOT_NORETURN __attribute__((noreturn))

// ---- device description (one per MBOOT_DEVn_*, in mboot_devs[] of flash_map.c) ----

// Runs of equal erase units from the start of a device, one behind the other. A device with one
// erase unit all over has one run.
#define MBOOT_MAX_RUNS 4
typedef struct {
    uint32_t size;         // bytes, a multiple of erase
    uint32_t erase;        // erase unit in bytes
} mboot_flash_run_t;

typedef struct {
    uint8_t id;            // fa_device_id, equals N
    uint8_t erased_val;    // 0xff or 0x00
    uint8_t mapped;        // 1: memcpy from base + off is valid for reads
    uint8_t write_unit;    // bytes; required alignment of offset and length for write
    uint8_t run_count;     // entries of runs[] in use, 1 to MBOOT_MAX_RUNS
    uint32_t base;         // CPU/DFU address of offset 0
    uint32_t size;         // bytes, the sum of the run sizes
    mboot_flash_run_t runs[MBOOT_MAX_RUNS];
    const char *name;
} mboot_flash_dev_t;

extern const mboot_flash_dev_t mboot_devs[];
extern const unsigned mboot_dev_count;

static inline uint32_t mboot_dev_base(uint8_t dev) {
    return mboot_devs[dev].base;
}

// Erase unit of the run that holds device offset off, 0 for an offset outside the device.
static inline uint32_t mboot_dev_erase_at(const mboot_flash_dev_t *d, uint32_t off) {
    uint32_t end = 0;
    for (unsigned i = 0; i < d->run_count; i++) {
        end += d->runs[i].size;
        if (off < end) {
            return d->runs[i].erase;
        }
    }
    return 0;
}

// Offset of the end of the run that holds device offset off, 0 for an offset outside the device.
static inline uint32_t mboot_dev_run_end(const mboot_flash_dev_t *d, uint32_t off) {
    uint32_t end = 0;
    for (unsigned i = 0; i < d->run_count; i++) {
        end += d->runs[i].size;
        if (off < end) {
            return end;
        }
    }
    return 0;
}

// True if [off, off + len) lies in the device and is a whole number of erase units. It may cross
// from one run into the next: each step is a unit of the run it starts in. Runs start at a
// multiple of their unit, so a unit boundary is an offset that is a multiple of the unit.
static inline bool mboot_dev_erase_range_ok(const mboot_flash_dev_t *d, uint32_t off, uint32_t len) {
    if (off > d->size || len > d->size - off) {
        return false;
    }
    for (uint32_t end = off + len; off < end;) {
        uint32_t unit = mboot_dev_erase_at(d, off);
        if (unit == 0 || (off % unit) != 0 || end - off < unit) {
            return false;
        }
        off += unit;
    }
    return true;
}

#ifndef MBOOT_RETENTION_BITS
#define MBOOT_RETENTION_BITS 32   // a port with a narrower retention register defines 8 or 16 in its port header
#endif

// ---- lifecycle (both roles unless noted) ----

// bootloader. First call from main(). Configure VTOR, clocks to a safe boot clock,
// caches, SysTick/timer for mboot_port_ticks_ms(), watchdog policy. No flash access.
void mboot_port_early_init(void);

// bootloader. Undo what the bootloader enabled so the app starts from a reset-like state: disable
// USB with its clock gates and IRQs, stop timers, clear pending IRQs, release flash controller
// mode changes, and clean (write back dirty lines) and disable every cache the bootloader
// enabled. mboot_port_jump() does not touch caches, so the app would otherwise run with stale
// lines of the bootloader's view of memory and flash. A Cortex-M7 data cache must be cleaned
// before the jump so the request region and the flash controller buffers reach memory. Called
// before mboot_port_jump() and before mboot_port_reset().
void mboot_port_deinit(void);

// bootloader. Branch to the image whose vector table is at vt_addr: set VTOR = vt_addr, MSP =
// word[0], branch to word[1]. Called after mboot_port_deinit() with interrupts disabled; the
// image starts with interrupts enabled (PRIMASK = 0 as after a reset). Weak default for
// Cortex-M in shared/mboot/src/arm_jump.c, which changes no cache, SysTick or peripheral state
// (deinit does that); a port overrides it only for MPU or remap handling that deinit cannot do.
MBOOT_NORETURN void mboot_port_jump(uint32_t vt_addr);

// both. System reset. Must flush the request RAM region to memory (clean D-cache, DSB) first.
MBOOT_NORETURN void mboot_port_reset(void);

// bootloader. Reset cause bitmask, then clear the hardware flags.
#define MBOOT_RESET_POR      (1u << 0)   // power-on or brown-out (RAM contents not trusted)
#define MBOOT_RESET_PIN      (1u << 1)
#define MBOOT_RESET_SOFT     (1u << 2)   // NVIC_SystemReset
#define MBOOT_RESET_WDT      (1u << 3)
#define MBOOT_RESET_OTHER    (1u << 4)
uint32_t mboot_port_reset_cause(void);

// bootloader. Busy wait / millisecond tick (monotonic, wraps at 2^32).
void mboot_port_delay_ms(uint32_t ms);
uint32_t mboot_port_ticks_ms(void);

// both. Feed the watchdog. Empty if the port has none enabled. Called from MCUBOOT_WATCHDOG_FEED().
void mboot_port_wdt_feed(void);

// ---- recovery request inputs (bootloader unless noted) ----

// Hardware forced entry (button held, boot pin). Must not block more than 50 ms.
bool mboot_port_entry_forced(void);

// bootloader, optional. Number of consecutive resets that the fault handlers of the port
// (HardFault, an NMI that is not a flash ECC event, ...) caused since the bootloader last handed
// over to the application. The port clears it in mboot_port_deinit(). At 3 the main flow stops
// running boot_go() and enters recovery with cause REC_FAULT: the bootloader stays in DFU instead of
// resetting again. A weak default in boot_main.c returns 0.
uint32_t mboot_port_fault_resets(void);

// both. Word that survives every reset except power loss (STM32 TAMP BKPxR). A register narrower
// than 32 bits keeps only the low MBOOT_RETENTION_BITS of the value, see mboot_request.h.
// Also the no-init RAM request region below.
uint32_t mboot_port_retention_read(void);
void     mboot_port_retention_write(uint32_t v);
void *mboot_port_request_ram(size_t *size_out);      // returns (void *)MBOOT_REQ_START and 0x400

// ---- flash device access (device-relative offsets; role specific implementation) ----
//
// All return 0 on success and a negative errno-style value on failure. Offsets and lengths obey:
//   read : any offset/length inside the device
//   write: offset % write_unit == 0 and len % write_unit == 0, target previously erased
//          (or never programmed since erase); the port may split into controller pages
//   erase: offset and length are whole erase units: the run of the device that holds the offset
//          gives the unit, and a range that crosses into the next run goes on with that run's
//          unit (the length is never a part of a unit)
// Functions that run while the flash controller is busy, with the code in XIP flash, must be
// placed in RAM and mask interrupts for the duration of each controller command. They must
// invalidate caches and AHB buffers so a following read sees the new data.
int mboot_port_flash_dev_init(void);                                       // bootloader only; app flash is already up
int mboot_port_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len);
int mboot_port_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len);
int mboot_port_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len);

// ---- USB (bootloader, only if MBOOT_DFU) ----
int mboot_port_usb_init(void);         // clocks, PHY, pins, NVIC for the USB IRQ; 0, or a negative errno if the clock or PHY does not start (logged, error LED shown). The port file also defines the ISR that calls tusb_int_handler()
void mboot_port_usb_deinit(void);
void mboot_port_usb_serial_number(char *buf, size_t len);   // NUL terminated, [0-9A-F] preferred, from the chip unique id

// ---- UI (bootloader) ----
void mboot_port_led(uint32_t mask);    // bit0 alive, bit1 USB active, bit2 error; may be empty

// ---- log sink (both; bootloader mandatory when MBOOT_LOG_LEVEL > 0) ----
void mboot_port_log_write(const char *s, size_t n);   // UART, SWO, RTT or empty

// ---- security counter backend (only if MBOOT_SECCNT_PORT) ----
int mboot_port_seccnt_read(uint32_t image_id, uint32_t *value);     // 0 ok
int mboot_port_seccnt_write(uint32_t image_id, uint32_t value);     // monotonic: value >= current; idempotent
bool mboot_port_seccnt_can_update(uint32_t image_id, uint32_t value);
int mboot_port_seccnt_lock(uint32_t image_id);                      // optional; return 0 if unsupported

// ---- entropy (only if MCUBOOT_FIH_PROFILE_HIGH) ----
uint8_t mboot_port_entropy_u8(void);

// ---- ECC flash (only if a device has MBOOT_DEVn_ECC = 1) ----
uint32_t mboot_port_ecc_events(void);   // count of ECC double-error events, incremented by the NMI handler
void     mboot_port_ecc_clear(void);    // clear the controller's ECC error flag
// A word that a power cut left partly programmed can also read as "corrected" (a single-bit ECC
// correction, possibly with wrong data) without a double-error event. This counts every such read.
// A port whose controller has no correction flag need not define it (the weak default in
// flash_map_backend.c returns 0).
uint32_t mboot_port_ecc_corrected(void);

// ---- test hook (only if MBOOT_TEST_FI=1, never in release builds) ----
// Called by flash_map_backend.c around every flash write and erase while the fault-injection
// state in the last 16 bytes of the request region (MBOOT_REQ_START + MBOOT_FI_STATE_OFFSET,
// see mboot_request.h) is armed. phase 0 = before the operation, phase 1 = after it completed.
// The port reads its trigger from that state (the words magic, target, counter and mode) and may
// abort via mboot_port_reset() or, for "during" cuts, start the operation and reset while the
// controller is busy.
void mboot_port_fi_hook(int op /*0 write,1 erase*/, uint8_t dev, uint32_t off, uint32_t len, int phase);

#endif
