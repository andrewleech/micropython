// Board and hardware specific configuration
#define MICROPY_HW_BOARD_NAME                   "Raspberry Pi Pico 2 W"
#define MICROPY_HW_FLASH_STORAGE_BYTES          (PICO_FLASH_SIZE_BYTES - 1536 * 1024)

// Enable networking.
#define MICROPY_PY_NETWORK 1
#define MICROPY_PY_NETWORK_HOSTNAME_DEFAULT     "Pico2W"

// CYW43 driver configuration.
// Use #ifndef guards as pico-SDK also defines these
#ifndef CYW43_USE_SPI
#define CYW43_USE_SPI (1)
#endif
#ifndef CYW43_LWIP
#define CYW43_LWIP (1)
#endif
#ifndef CYW43_GPIO
#define CYW43_GPIO (1)
#endif
#ifndef CYW43_SPI_PIO
#define CYW43_SPI_PIO (1)
#endif

// For debugging mbedtls - also set
// Debug level (0-4) 1=warning, 2=info, 3=debug, 4=verbose
// #define MODUSSL_MBEDTLS_DEBUG_LEVEL 1

#define MICROPY_HW_PIN_EXT_COUNT    CYW43_WL_GPIO_COUNT

int mp_hal_is_pin_reserved(int n);
#define MICROPY_HW_PIN_RESERVED(i) mp_hal_is_pin_reserved(i)
