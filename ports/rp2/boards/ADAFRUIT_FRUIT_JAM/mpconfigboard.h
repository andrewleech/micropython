// Board and hardware specific configuration
#define MICROPY_HW_BOARD_NAME "Adafruit Fruit Jam"

// PERIPH_RESET (GPIO22) is shared between the TLV320DAC3100 audio codec and the
// ESP32-C6 co-processor and is held low (in reset) by default. Release it at
// startup so the codec responds on I2C0, matching the reference firmware.
// Note this pin also doubles as the ESP32-C6 reset line (MICROPY_HW_NINA_RESET
// below), so enabling the WiFi/BLE radio resets the audio codec as well.
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

// ESP32-C6 co-processor bus / STEMMA "SPI" (also used by the NINA-W10 WiFi driver below)
#define MICROPY_HW_SPI1_SCK  (30)
#define MICROPY_HW_SPI1_MOSI (31)
#define MICROPY_HW_SPI1_MISO (28)

// Shared with ESP32-C6 UART (D6/D7 header pins), used by the NINA-W10 Bluetooth HCI below
#define MICROPY_HW_UART1_TX  (8)
#define MICROPY_HW_UART1_RX  (9)
#define MICROPY_HW_UART1_CTS (-1)
#define MICROPY_HW_UART1_RTS (-1)

// Enable networking.
#define MICROPY_PY_NETWORK                  (1)
#define MICROPY_PY_NETWORK_HOSTNAME_DEFAULT "mpy-fruit-jam"

// The NINA-W10 driver's errno values come from the AirLift co-processor's own
// newlib-based errno numbering (e.g. EINPROGRESS=119), not MicroPython's
// internal compact table, so route OSError through the real errno.h values.
#define MICROPY_USE_INTERNAL_ERRNO (0)

// Bluetooth config: ESP32-C6 AirLift (nina-fw) HCI over UART1.
#define MICROPY_HW_BLE_UART_ID       (1)
#define MICROPY_HW_BLE_UART_BAUDRATE (115200)

// WiFi config: ESP32-C6 AirLift (nina-fw) over SPI1.
#define MICROPY_HW_WIFI_SPI_ID       (1)
#define MICROPY_HW_WIFI_SPI_BAUDRATE (8 * 1000 * 1000)

// ESP32-C6 AirLift (nina-fw) control pins.
#define MICROPY_HW_NINA_RESET (22)  // WIFI_RESET / PERIPH_RESET
#define MICROPY_HW_NINA_GPIO0 (23)  // WIFI_IRQ
#define MICROPY_HW_NINA_GPIO1 (46)  // WIFI_CS
#define MICROPY_HW_NINA_ACK   (3)   // WIFI_BUSY
