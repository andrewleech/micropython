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

// Host test of the two optional port hooks that the core harness boards do not use: the
// security counter backend of the port (MCUBOOT_SECCNT_PORT, security_cnt.c over
// mcuboot_port_seccnt_*) and the entropy source of the high FIH profile (fih_delay_rng.c over
// mcuboot_port_entropy_u8). The port side is a counter variable with a limited number of
// increments, like fuses, and a fixed byte sequence.

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "mcuboot_config/mcuboot_config.h"
#include "bootutil/fault_injection_hardening.h"
#include "bootutil/security_cnt.h"
#include "mcuboot_port.h"

#if !defined(MCUBOOT_SECCNT_PORT) || !defined(MCUBOOT_HW_ROLLBACK_PROT_LOCK) || !defined(MCUBOOT_HW_ROLLBACK_PROT_COUNTER_LIMITED) \
    || !defined(MCUBOOT_FIH_PROFILE_HIGH)
#error "this test is built for a board with the port security counter and the high FIH profile"
#endif

static int n_checks;
static int n_failures;

#define CHECK(cond) do { \
        n_checks++; \
        if (!(cond)) { \
            n_failures++; \
            printf("  CHECK FAILED %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        } \
} while (0)

// ---- the port: a counter of 8 increments (a fuse bank) ----

#define FUSE_STEPS 8
static uint32_t cnt_value;
static uint32_t cnt_steps_left;
static bool cnt_locked;
static int cnt_read_rc;
static int cnt_write_rc;
static unsigned cnt_lock_calls;
static unsigned cnt_write_calls;
static uint32_t cnt_image_id_seen;

static void port_reset(void) {
    cnt_value = 0;
    cnt_steps_left = FUSE_STEPS;
    cnt_locked = false;
    cnt_read_rc = 0;
    cnt_write_rc = 0;
    cnt_lock_calls = 0;
    cnt_write_calls = 0;
}

int mcuboot_port_seccnt_read(uint32_t image_id, uint32_t *value) {
    cnt_image_id_seen = image_id;
    if (cnt_read_rc != 0) {
        return cnt_read_rc;
    }
    *value = cnt_value;
    return 0;
}

int mcuboot_port_seccnt_write(uint32_t image_id, uint32_t value) {
    cnt_write_calls++;
    cnt_image_id_seen = image_id;
    if (cnt_write_rc != 0) {
        return cnt_write_rc;
    }
    if (cnt_locked || value < cnt_value) {
        return -1;
    }
    if (value == cnt_value) {
        return 0;       // idempotent
    }
    if (value - cnt_value > cnt_steps_left) {
        return -1;
    }
    cnt_steps_left -= value - cnt_value;
    cnt_value = value;
    return 0;
}

bool mcuboot_port_seccnt_can_update(uint32_t image_id, uint32_t value) {
    return !cnt_locked && value >= cnt_value && value - cnt_value <= cnt_steps_left;
}

int mcuboot_port_seccnt_lock(uint32_t image_id) {
    cnt_lock_calls++;
    cnt_locked = true;
    return 0;
}

// ---- entropy: a fixed sequence, counted ----

static const uint8_t entropy_seq[] = {7, 0, 3, 250};
static unsigned entropy_calls;

uint8_t mcuboot_port_entropy_u8(void) {
    return entropy_seq[entropy_calls++ % sizeof(entropy_seq)];
}

// bootutil and the glue call these; nothing here reaches them.
void mcuboot_port_wdt_feed(void) {
}

// ---- tests ----

static uint32_t decode(fih_int v) {
    return (uint32_t)fih_int_decode(v);
}

static void test_counter(void) {
    printf("security counter over the port backend\n");
    port_reset();
    CHECK(FIH_EQ(boot_nv_security_counter_init(), FIH_SUCCESS));

    fih_int c;
    CHECK(FIH_EQ(boot_nv_security_counter_get(0, &c), FIH_SUCCESS) && decode(c) == 0);
    CHECK(boot_nv_security_counter_update(0, 3) == 0);
    CHECK(FIH_EQ(boot_nv_security_counter_get(0, &c), FIH_SUCCESS) && decode(c) == 3);
    CHECK(cnt_image_id_seen == 0);

    // The same value again is not an error and not an increment.
    CHECK(boot_nv_security_counter_update(0, 3) == 0);
    CHECK(cnt_steps_left == FUSE_STEPS - 3);

    // A counter that cannot be read must not look like a low one.
    cnt_read_rc = -5;
    CHECK(FIH_NOT_EQ(boot_nv_security_counter_get(0, &c), FIH_SUCCESS));
    CHECK(decode(c) == UINT32_MAX);
    cnt_read_rc = 0;

    // A failing write is reported as it is.
    cnt_write_rc = -7;
    CHECK(boot_nv_security_counter_update(0, 4) == -7);
    cnt_write_rc = 0;
    CHECK(FIH_EQ(boot_nv_security_counter_get(0, &c), FIH_SUCCESS) && decode(c) == 3);
}

static void test_limited(void) {
    printf("counter that runs out of increments\n");
    port_reset();
    CHECK(FIH_EQ(boot_nv_security_counter_is_update_possible(0, FUSE_STEPS), FIH_SUCCESS));
    CHECK(FIH_NOT_EQ(boot_nv_security_counter_is_update_possible(0, FUSE_STEPS + 1), FIH_SUCCESS));
    CHECK(boot_nv_security_counter_update(0, FUSE_STEPS) == 0);
    CHECK(FIH_EQ(boot_nv_security_counter_is_update_possible(0, FUSE_STEPS), FIH_SUCCESS));
    CHECK(FIH_NOT_EQ(boot_nv_security_counter_is_update_possible(0, FUSE_STEPS + 1), FIH_SUCCESS));
    CHECK(boot_nv_security_counter_update(0, FUSE_STEPS + 1) != 0);
}

static void test_lock(void) {
    printf("lock of the counter\n");
    port_reset();
    CHECK(boot_nv_security_counter_update(0, 2) == 0);
    CHECK(boot_nv_security_counter_lock(0) == 0);
    CHECK(cnt_lock_calls == 1);
    // Updates after the lock are refused by the backend.
    CHECK(FIH_NOT_EQ(boot_nv_security_counter_is_update_possible(0, 3), FIH_SUCCESS));
    CHECK(boot_nv_security_counter_update(0, 3) != 0);
}

static void test_entropy(void) {
    printf("entropy of the high FIH profile\n");
    entropy_calls = 0;
    CHECK(fih_delay_init() == 0);
    for (unsigned i = 0; i < 2 * sizeof(entropy_seq); i++) {
        CHECK(fih_delay_random_uchar() == entropy_seq[i % sizeof(entropy_seq)]);
    }
    // The delay in the comparison macros draws from it.
    entropy_calls = 0;
    fih_ret ok = FIH_SUCCESS;
    CHECK(FIH_EQ(ok, FIH_SUCCESS));
    CHECK(entropy_calls == 1);
    // FIH_NOT_EQ only delays when the values are equal (the check that must not be skipped).
    CHECK(!FIH_NOT_EQ(ok, FIH_SUCCESS));
    CHECK(entropy_calls == 2);
}

int main(void) {
    test_counter();
    test_limited();
    test_lock();
    test_entropy();
    printf("%d checks, %d failed\n", n_checks, n_failures);
    printf("result: %s\n", n_failures == 0 ? "PASS" : "FAIL");
    return n_failures == 0 ? 0 : 1;
}
