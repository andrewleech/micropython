#include "hardware/gpio.h"

#define PERIPH_RESET_PIN (22)

// Release PERIPH_RESET (shared by the TLV320DAC3100 codec and the ESP32-C6
// co-processor). Set the pin high before enabling the output driver so it
// can't glitch low, mirroring the reference (CircuitPython) board_init().
void ADAFRUIT_FRUIT_JAM_board_startup(void) {
    gpio_put(PERIPH_RESET_PIN, 1);
    gpio_set_dir(PERIPH_RESET_PIN, GPIO_OUT);
    gpio_set_function(PERIPH_RESET_PIN, GPIO_FUNC_SIO);
}
