#include <string.h>

#include "py/runtime.h"
#include "py/stream.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "extmod/misc.h"
#include "usb.h"
#include "uart.h"
#include "rng.h"

#if MICROPY_HW_TINYUSB_STACK
#include "shared/tinyusb/mp_usbd_cdc.h"

#ifndef MICROPY_HW_STDIN_BUFFER_LEN
#define MICROPY_HW_STDIN_BUFFER_LEN 512
#endif

static uint8_t stdin_ringbuf_array[MICROPY_HW_STDIN_BUFFER_LEN];
ringbuf_t stdin_ringbuf = { stdin_ringbuf_array, sizeof(stdin_ringbuf_array), 0, 0 };
#endif

MP_NORETURN void mp_hal_raise(HAL_StatusTypeDef status) {
    mp_raise_OSError(mp_hal_status_to_errno_table[status]);
}

MP_WEAK uintptr_t mp_hal_stdio_poll(uintptr_t poll_flags) {
    uintptr_t ret = 0;
    #if MICROPY_HW_USB_CDC && MICROPY_HW_TINYUSB_STACK
    ret |= mp_usbd_cdc_poll_interfaces(poll_flags);
    #endif
    if (MP_STATE_PORT(pyb_stdio_uart) != NULL) {
        mp_obj_t pyb_stdio_uart = MP_OBJ_FROM_PTR(MP_STATE_PORT(pyb_stdio_uart));
        int errcode;
        const mp_stream_p_t *stream_p = mp_get_stream(pyb_stdio_uart);
        ret = stream_p->ioctl(pyb_stdio_uart, MP_STREAM_POLL, poll_flags, &errcode);
    }
    return ret | mp_os_dupterm_poll(poll_flags);
}

MP_WEAK int mp_hal_stdin_rx_chr(void) {
    for (;;) {
        #if 0
        #ifdef USE_HOST_MODE
        pyb_usb_host_process();
        int c = pyb_usb_host_get_keyboard();
        if (c != 0) {
            return c;
        }
        #endif
        #endif
        if (MP_STATE_PORT(pyb_stdio_uart) != NULL && uart_rx_any(MP_STATE_PORT(pyb_stdio_uart))) {
            return uart_rx_char(MP_STATE_PORT(pyb_stdio_uart));
        }
        int dupterm_c = mp_os_dupterm_rx_chr();
        if (dupterm_c >= 0) {
            return dupterm_c;
        }
        #if MICROPY_HW_USB_CDC && MICROPY_HW_TINYUSB_STACK
        mp_usbd_cdc_poll_interfaces(0);
        int c = ringbuf_get(&stdin_ringbuf);
        if (c != -1) {
            return c;
        }
        #endif
        mp_event_wait_indefinite();
    }
}

MP_WEAK mp_uint_t mp_hal_stdout_tx_strn(const char *str, size_t len) {
    mp_uint_t ret = len;
    bool did_write = false;
    if (MP_STATE_PORT(pyb_stdio_uart) != NULL) {
        uart_tx_strn(MP_STATE_PORT(pyb_stdio_uart), str, len);
        did_write = true;
    }
    #if MICROPY_HW_USB_CDC && MICROPY_HW_TINYUSB_STACK
    mp_uint_t cdc_res = mp_usbd_cdc_tx_strn(str, len);
    if (cdc_res > 0) {
        did_write = true;
        ret = MIN(cdc_res, ret);
    }
    #endif
    #if 0 && defined(USE_HOST_MODE) && MICROPY_HW_HAS_LCD
    lcd_print_strn(str, len);
    #endif
    int dupterm_res = mp_os_dupterm_tx_strn(str, len);
    if (dupterm_res >= 0) {
        did_write = true;
        ret = MIN((mp_uint_t)dupterm_res, ret);
    }
    return did_write ? ret : 0;
}

#if __CORTEX_M >= 0x03
void mp_hal_ticks_cpu_enable(void) {
    if (!(DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk)) {
        CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
        #if defined(__CORTEX_M) && __CORTEX_M == 7
        // on Cortex-M7 we must unlock the DWT before writing to its registers
        DWT->LAR = 0xc5acce55;
        #endif
        DWT->CYCCNT = 0;
        DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    }
}
#endif

/*******************************************************************************/
// MAC address

// Generate a random locally administered MAC address (LAA)
void mp_hal_generate_laa_mac(int idx, uint8_t buf[6]) {
    uint8_t *id = (uint8_t *)MP_HAL_UNIQUE_ID_ADDRESS;
    buf[0] = 0x02; // LAA range
    buf[1] = (id[11] << 4) | (id[10] & 0xf);
    buf[2] = (id[9] << 4) | (id[8] & 0xf);
    buf[3] = (id[7] << 4) | (id[6] & 0xf);
    buf[4] = id[2];
    buf[5] = (id[0] << 2) | idx;
}

// A board can override this if needed
MP_WEAK void mp_hal_get_mac(int idx, uint8_t buf[6]) {
    mp_hal_generate_laa_mac(idx, buf);
}

void mp_hal_get_mac_ascii(int idx, size_t chr_off, size_t chr_len, char *dest) {
    static const char hexchr[16] = "0123456789ABCDEF";
    uint8_t mac[6];
    mp_hal_get_mac(idx, mac);
    for (; chr_len; ++chr_off, --chr_len) {
        *dest++ = hexchr[mac[chr_off >> 1] >> (4 * (1 - (chr_off & 1))) & 0xf];
    }
}

#if MICROPY_HW_ENABLE_RNG
void mp_hal_get_random(size_t n, uint8_t *buf) {
    uint32_t val = 0;
    for (int i = 0; i < n; i++) {
        if ((i & 3) == 0) {
            val = mp_hal_get_hw_random_u32();
        }
        buf[i] = val;
        val >>= 8;
    }
}
#endif

MP_REGISTER_ROOT_POINTER(struct _machine_uart_obj_t *pyb_stdio_uart);
