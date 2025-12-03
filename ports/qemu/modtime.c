/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Time module implementation for QEMU bare-metal port.
 * Provides relative time since boot (no RTC/wall-clock).
 */

#include "py/obj.h"

// External functions from ticks.c
extern uintptr_t ticks_ms(void);
extern uintptr_t ticks_us(void);

// Return the number of seconds since boot (not epoch - no RTC available).
static mp_obj_t mp_time_time_get(void) {
    // Return seconds since boot as a float
    mp_float_t seconds = (mp_float_t)ticks_ms() / 1000.0f;
    return mp_obj_new_float(seconds);
}
