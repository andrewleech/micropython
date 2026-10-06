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

#include "mcuboot_config/mcuboot_config.h"

// Entry points bootutil calls for hardware rollback protection. The counter itself is stored
// either by seccnt_flash.c or by the port (OTP, fuses, battery backed registers).

#if defined(MCUBOOT_HW_ROLLBACK_PROT)

#include <stdbool.h>

#include "bootutil/fault_injection_hardening.h"
#include "bootutil/security_cnt.h"
#include "mcuboot_port.h"
#include "mcuboot_seccnt.h"

#if defined(MCUBOOT_SECCNT_FLASH)
#define BACKEND_INIT() mcuboot_seccnt_init()
#define BACKEND_READ(id, v) mcuboot_seccnt_flash_read(id, v)
#define BACKEND_WRITE(id, v) mcuboot_seccnt_flash_write(id, v)
#define BACKEND_CAN_UPDATE(id, v) mcuboot_seccnt_flash_can_update(id, v)
#define BACKEND_LOCK(id) 0
#else
#define BACKEND_INIT() 0
#define BACKEND_READ(id, v) mcuboot_port_seccnt_read(id, v)
#define BACKEND_WRITE(id, v) mcuboot_port_seccnt_write(id, v)
#define BACKEND_CAN_UPDATE(id, v) mcuboot_port_seccnt_can_update(id, v)
#define BACKEND_LOCK(id) mcuboot_port_seccnt_lock(id)
#endif

fih_ret boot_nv_security_counter_init(void) {
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    fih_rc = fih_ret_encode_zero_equality(BACKEND_INIT());
    FIH_RET(fih_rc);
}

fih_ret boot_nv_security_counter_get(uint32_t image_id, fih_int *security_cnt) {
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    uint32_t value = 0;
    int rc = BACKEND_READ(image_id, &value);
    // A counter that cannot be read must not look like a low one.
    *security_cnt = fih_int_encode(rc == 0 ? value : UINT32_MAX);
    fih_rc = fih_ret_encode_zero_equality(rc);
    FIH_RET(fih_rc);
}

int32_t boot_nv_security_counter_update(uint32_t image_id, uint32_t img_security_cnt) {
    return BACKEND_WRITE(image_id, img_security_cnt);
}

fih_ret boot_nv_security_counter_is_update_possible(uint32_t image_id, uint32_t img_security_cnt) {
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    fih_rc = fih_ret_encode_zero_equality(!BACKEND_CAN_UPDATE(image_id, img_security_cnt));
    FIH_RET(fih_rc);
}

#if defined(MCUBOOT_HW_ROLLBACK_PROT_LOCK)
int32_t boot_nv_security_counter_lock(uint32_t image_id) {
    return BACKEND_LOCK(image_id);
}
#endif

#endif // MCUBOOT_HW_ROLLBACK_PROT
