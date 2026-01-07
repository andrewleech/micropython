/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2020-2021 Damien P. George
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

#include "py/mphal.h"
#include "py/runtime.h"
#include "py/mpprint.h"
#include "drivers/dht/dht.h"
#include "modrp2.h"
#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "hardware/structs/ioqspi.h"
#include "hardware/structs/sio.h"

#if MICROPY_PY_NETWORK_CYW43
#include "extmod/modnetwork.h"
#include "lib/cyw43-driver/src/cyw43_stats.h"
#endif

#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS
#include "extmod/freertos/mp_freertos_service.h"
#endif

#if MICROPY_PY_NETWORK_CYW43
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(mod_network_country_obj);
#endif

#define CS_PIN_INDEX 1

#if PICO_RP2040
#define CS_BIT (1u << CS_PIN_INDEX)
#else
#define CS_BIT SIO_GPIO_HI_IN_QSPI_CSN_BITS
#endif

// Improved version of
// https://github.com/raspberrypi/pico-examples/blob/master/picoboard/button/button.c
static bool __no_inline_not_in_flash_func(bootsel_button)(void) {
    // Disable interrupts and the other core since they might be
    // executing code from flash and we are about to temporarily
    // disable flash access.
    mp_uint_t atomic_state = MICROPY_BEGIN_ATOMIC_SECTION();

    // Set the CS pin to high impedance.
    hw_write_masked(&ioqspi_hw->io[CS_PIN_INDEX].ctrl,
        (GPIO_OVERRIDE_LOW << IO_QSPI_GPIO_QSPI_SS_CTRL_OEOVER_LSB),
        IO_QSPI_GPIO_QSPI_SS_CTRL_OEOVER_BITS);

    // Delay without calling any functions in flash.
    uint32_t start = timer_hw->timerawl;
    while ((uint32_t)(timer_hw->timerawl - start) <= MICROPY_HW_BOOTSEL_DELAY_US) {
        ;
    }

    // The HI GPIO registers in SIO can observe and control the 6 QSPI pins.
    // The button pulls the QSPI_SS pin *low* when pressed.
    bool button_state = !(sio_hw->gpio_hi_in & CS_BIT);

    // Restore the QSPI_SS pin so we can use flash again.
    hw_write_masked(&ioqspi_hw->io[CS_PIN_INDEX].ctrl,
        (GPIO_OVERRIDE_NORMAL << IO_QSPI_GPIO_QSPI_SS_CTRL_OEOVER_LSB),
        IO_QSPI_GPIO_QSPI_SS_CTRL_OEOVER_BITS);

    MICROPY_END_ATOMIC_SECTION(atomic_state);

    return button_state;
}

static mp_obj_t rp2_bootsel_button(void) {
    return MP_OBJ_NEW_SMALL_INT(bootsel_button());
}
MP_DEFINE_CONST_FUN_OBJ_0(rp2_bootsel_button_obj, rp2_bootsel_button);

// Debug functions for WiFi/service task investigation
#if MICROPY_PY_NETWORK_CYW43 && CYW43_USE_STATS
static mp_obj_t rp2_cyw43_stats(void) {
    cyw43_dump_stats();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(rp2_cyw43_stats_obj, rp2_cyw43_stats);
#endif

#if MICROPY_PY_NETWORK_CYW43
// Debug functions for CYW43 GPIO tracking
extern uint32_t cyw43_debug_get_post_poll_count(void);
extern void cyw43_debug_reset_post_poll_count(void);
extern bool cyw43_debug_get_gpio_state(void);
extern uint32_t cyw43_debug_get_yield_count(void);
extern void cyw43_debug_reset_yield_count(void);
extern uint32_t cyw43_debug_get_gpio_irq_count(void);
extern void cyw43_debug_reset_gpio_irq_count(void);

static mp_obj_t rp2_cyw43_gpio_debug(void) {
    mp_printf(&mp_plat_print, "=== CYW43 GPIO Debug ===\n");
    mp_printf(&mp_plat_print, "GPIO IRQ triggers: %lu\n", (unsigned long)cyw43_debug_get_gpio_irq_count());
    mp_printf(&mp_plat_print, "post_poll_hook calls: %lu\n", (unsigned long)cyw43_debug_get_post_poll_count());
    mp_printf(&mp_plat_print, "HOST_WAKE GPIO state: %d\n", cyw43_debug_get_gpio_state());
    mp_printf(&mp_plat_print, "cyw43_yield() calls: %lu\n", (unsigned long)cyw43_debug_get_yield_count());
    mp_printf(&mp_plat_print, "========================\n");
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(rp2_cyw43_gpio_debug_obj, rp2_cyw43_gpio_debug);

static mp_obj_t rp2_cyw43_gpio_reset(void) {
    cyw43_debug_reset_post_poll_count();
    cyw43_debug_reset_yield_count();
    cyw43_debug_reset_gpio_irq_count();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(rp2_cyw43_gpio_reset_obj, rp2_cyw43_gpio_reset);
#endif

#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS && MP_FREERTOS_SERVICE_DEBUG
static mp_obj_t rp2_service_stats(void) {
    mp_freertos_service_debug_print();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(rp2_service_stats_obj, rp2_service_stats);

static mp_obj_t rp2_service_stats_reset(void) {
    mp_freertos_service_debug_reset();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(rp2_service_stats_reset_obj, rp2_service_stats_reset);
#endif

static const mp_rom_map_elem_t rp2_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),            MP_ROM_QSTR(MP_QSTR_rp2) },
    { MP_ROM_QSTR(MP_QSTR_Flash),               MP_ROM_PTR(&rp2_flash_type) },
    { MP_ROM_QSTR(MP_QSTR_PIO),                 MP_ROM_PTR(&rp2_pio_type) },
    { MP_ROM_QSTR(MP_QSTR_StateMachine),        MP_ROM_PTR(&rp2_state_machine_type) },
    { MP_ROM_QSTR(MP_QSTR_DMA),                 MP_ROM_PTR(&rp2_dma_type) },
    { MP_ROM_QSTR(MP_QSTR_bootsel_button),      MP_ROM_PTR(&rp2_bootsel_button_obj) },

    #if MICROPY_PY_NETWORK_CYW43
    // Deprecated (use network.country instead).
    { MP_ROM_QSTR(MP_QSTR_country),             MP_ROM_PTR(&mod_network_country_obj) },
    #endif

    // Debug functions (temporary - for debugging WiFi/service task issues)
    #if MICROPY_PY_NETWORK_CYW43 && CYW43_USE_STATS
    { MP_ROM_QSTR(MP_QSTR_cyw43_stats),         MP_ROM_PTR(&rp2_cyw43_stats_obj) },
    #endif
    #if MICROPY_PY_NETWORK_CYW43
    { MP_ROM_QSTR(MP_QSTR_cyw43_gpio_debug),    MP_ROM_PTR(&rp2_cyw43_gpio_debug_obj) },
    { MP_ROM_QSTR(MP_QSTR_cyw43_gpio_reset),    MP_ROM_PTR(&rp2_cyw43_gpio_reset_obj) },
    #endif
    #if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS && MP_FREERTOS_SERVICE_DEBUG
    { MP_ROM_QSTR(MP_QSTR_service_stats),       MP_ROM_PTR(&rp2_service_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_service_stats_reset), MP_ROM_PTR(&rp2_service_stats_reset_obj) },
    #endif
};
static MP_DEFINE_CONST_DICT(rp2_module_globals, rp2_module_globals_table);

const mp_obj_module_t mp_module_rp2 = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&rp2_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR__rp2, mp_module_rp2);
