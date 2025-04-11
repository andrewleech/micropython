/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Your Name
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

#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_PY_USBIP

#include "extmod/usbip.h"
// #include "extmod/usbip_tusb.c" // Separated into build system
// #include "extmod/usbip_server.c"
// #include "extmod/usbip_glue.c"

// Ensure required components are included via build flags
#if !MICROPY_PY_LWIP
#error USBIP requires MICROPY_PY_LWIP
#endif
#if !CFG_TUH_ENABLED
#error USBIP requires CFG_TUH_ENABLED
#endif
#if !CFG_TUH_APPLICATION_DRIVER
#error USBIP requires CFG_TUH_APPLICATION_DRIVER
#endif


// --- Module Functions ---

STATIC mp_obj_t usbip_start(void) {
    #if MICROPY_PY_LWIP
    // Call initialization functions
    usbip_glue_init(); // Initialize global state
    usbip_server_init();
    // usbip_register_driver(); // Registration now happens via usbh_app_driver_get_cb hook
    mp_printf(MP_PYTHON_PRINTER, "USBIP Server Started\n");
    #else
    mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("USBIP requires LWIP"));
    #endif
    return mp_const_none;
}
STATIC MP_DEFINE_CONST_FUN_OBJ_0(usbip_start_obj, usbip_start);

STATIC mp_obj_t usbip_stop(void) {
    #if MICROPY_PY_LWIP
    // Call deinitialization functions
    usbip_server_deinit();
    // usbip_unregister_driver(); // Unregistration might not be needed if handled by TinyUSB
    mp_printf(MP_PYTHON_PRINTER, "USBIP Server Stopped\n");
    #else
    // No-op if LWIP wasn't enabled, start would have failed.
    #endif
    return mp_const_none;
}
STATIC MP_DEFINE_CONST_FUN_OBJ_0(usbip_stop_obj, usbip_stop);

STATIC const mp_rom_map_elem_t usbip_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_usbip) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&usbip_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&usbip_stop_obj) },
    // Add other functions (e.g., status) here
};
STATIC MP_DEFINE_CONST_DICT(usbip_module_globals, usbip_module_globals_table);

const mp_obj_module_t mp_module_usbip = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&usbip_module_globals,
};

// We need to ensure this module is only registered when the feature is enabled
#if MICROPY_PY_USBIP
// Register the module to make it available in Python
MP_REGISTER_MODULE(MP_QSTR_usbip, mp_module_usbip);
#endif

#endif // MICROPY_PY_USBIP
