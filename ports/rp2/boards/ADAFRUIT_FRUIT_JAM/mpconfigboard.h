// Board and hardware specific configuration
#define MICROPY_HW_BOARD_NAME "Adafruit Fruit Jam"

// PERIPH_RESET (GPIO22) is shared between the TLV320DAC3100 audio codec and the
// ESP32-C6 co-processor and is held low (in reset) by default. Release it at
// startup so the codec responds on I2C0, matching the reference firmware.
#define MICROPY_BOARD_STARTUP ADAFRUIT_FRUIT_JAM_board_startup
void ADAFRUIT_FRUIT_JAM_board_startup(void);

#define MICROPY_HW_USB_VID (0x239A)
#define MICROPY_HW_USB_PID (0x8110)  // TODO: placeholder -- confirm official PID with Adafruit

// STEMMA QT / default I2C0
#define MICROPY_HW_I2C0_SDA (20)
#define MICROPY_HW_I2C0_SCL (21)

// microSD card (SPI-mode, over the same lines as the SDIO interface)
#define MICROPY_HW_SPI0_SCK  (34)
#define MICROPY_HW_SPI0_MOSI (35)
#define MICROPY_HW_SPI0_MISO (36)

// ESP32-C6 co-processor bus / STEMMA "SPI" -- general purpose only, no driver
#define MICROPY_HW_SPI1_SCK  (30)
#define MICROPY_HW_SPI1_MOSI (31)
#define MICROPY_HW_SPI1_MISO (28)

// Shared with ESP32-C6 UART (D6/D7 header pins)
#define MICROPY_HW_UART1_TX  (8)
#define MICROPY_HW_UART1_RX  (9)
#define MICROPY_HW_UART1_CTS (-1)
#define MICROPY_HW_UART1_RTS (-1)
