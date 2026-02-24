// Board config for Waveshare RP2350-USB-A

// Board and hardware
#define MICROPY_HW_BOARD_NAME          "Waveshare RP2350-USB-A"
#define MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - 1024 * 1024)

// USB Host via PIO USB on USB-A connector (GPIO12=D+, GPIO13=D-)
// Native USB controller remains in device mode for CDC REPL on USB-C
#define MICROPY_HW_USB_HOST            (1)

// PIO USB D+ pin (D- is implicitly D+ + 1 = GPIO13)
#define MICROPY_HW_USB_HOST_DP_PIN     (12)

// Dual-mode USB: native device on rhport 0, PIO USB host on rhport 1
#define BOARD_TUD_RHPORT               0
#define BOARD_TUH_RHPORT               1
#define CFG_TUH_RPI_PIO_USB            1

// Tell TinyUSB which rhport is device vs host mode.
// Using raw values (0x0001=DEVICE, 0x0002=HOST, 0x0200=FULL_SPEED) because
// OPT_MODE_* may not be defined when mpconfigboard.h is included outside
// TinyUSB context. tusb_option.h checks these before selecting HCD/DCD.
#define CFG_TUSB_RHPORT0_MODE          (0x0201)  // OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED
#define CFG_TUSB_RHPORT1_MODE          (0x0202)  // OPT_MODE_HOST | OPT_MODE_FULL_SPEED

// PIO USB requires sys clock to be a multiple of 12MHz.
// RP2350 default is 150MHz which doesn't divide evenly.
// SYS_CLK_KHZ=120000 is set in mpconfigboard.cmake so pico-sdk
// sees it early enough during clock init.

// UART config
#define MICROPY_HW_UART0_TX            (0)
#define MICROPY_HW_UART0_RX            (1)
#define MICROPY_HW_UART0_CTS           (2)
#define MICROPY_HW_UART0_RTS           (3)

#define MICROPY_HW_UART1_TX            (4)
#define MICROPY_HW_UART1_RX            (5)
#define MICROPY_HW_UART1_CTS           (6)
#define MICROPY_HW_UART1_RTS           (7)

// I2C config
#define MICROPY_HW_I2C0_SCL            (9)
#define MICROPY_HW_I2C0_SDA            (8)

#define MICROPY_HW_I2C1_SCL            (11)
#define MICROPY_HW_I2C1_SDA            (10)

// SPI config
#define MICROPY_HW_SPI0_SCK            (18)
#define MICROPY_HW_SPI0_MOSI           (19)
#define MICROPY_HW_SPI0_MISO           (16)

#define MICROPY_HW_SPI1_SCK            (10)
#define MICROPY_HW_SPI1_MOSI           (11)
// Note: GPIO12 is shared with PIO USB host D+ pin. Do not use SPI1 MISO
// when USB host is active.
#define MICROPY_HW_SPI1_MISO           (12)

// LEDs
#define MICROPY_HW_LED1                (25)

// Boot button
#define MICROPY_HW_BOOTSEL_BUTTON_PIN  (22)
