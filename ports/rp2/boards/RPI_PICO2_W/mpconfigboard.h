// Board and hardware specific configuration
#define MICROPY_HW_BOARD_NAME                   "Raspberry Pi Pico 2 W"

// Enable networking.
#define MICROPY_PY_NETWORK 1
#define MICROPY_PY_NETWORK_HOSTNAME_DEFAULT     "Pico2W"

// CYW43 driver configuration.
#define CYW43_USE_SPI (1)
#define CYW43_LWIP (1)
#define CYW43_GPIO (1)
#define CYW43_SPI_PIO (1)

// For debugging mbedtls - also set
// Debug level (0-4) 1=warning, 2=info, 3=debug, 4=verbose
// #define MODUSSL_MBEDTLS_DEBUG_LEVEL 1

#define MICROPY_HW_PIN_EXT_COUNT    CYW43_WL_GPIO_COUNT

#ifndef __ASSEMBLER__
int mp_hal_is_pin_reserved(int n);
#define MICROPY_HW_PIN_RESERVED(i) mp_hal_is_pin_reserved(i)
#endif

#if defined(USE_MBOOT)
// Per-board mboot configuration (DFU USB identity + UI).
#define MBOOT_USB_PID         (0xDFA3u)
#define MBOOT_LED_PIN         (-1)  // LED is on the CYW43; mboot does not drive it.
#define MBOOT_PRODUCT_STRING  "RPI_PICO2_W Bootloader"
#endif
