// Board config for Waveshare RP2350-USB-A

// Board and hardware
#define MICROPY_HW_BOARD_NAME          "Waveshare RP2350-USB-A"
#define MICROPY_HW_FLASH_STORAGE_BYTES (PICO_FLASH_SIZE_BYTES - 1024 * 1024)

// Enable USB Host functionality
#define MICROPY_HW_USB_HOST            (1)

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
#define MICROPY_HW_SPI1_MISO           (12)

// LEDs
#define MICROPY_HW_LED1                (25)

// Boot button
#define MICROPY_HW_BOOTSEL_BUTTON_PIN  (22)
