/*
 * Zephyr Utility Functions
 * Implementations of utility functions used by BLE stack
 */

#include <stdint.h>
#include <stddef.h>

// Byte-swap memory buffer in place
// Reverses byte order of a buffer (e.g., for endianness conversion)
void sys_mem_swap(void *buf, size_t length) {
    uint8_t *p = (uint8_t *)buf;
    size_t i;

    for (i = 0; i < length / 2; i++) {
        uint8_t tmp = p[i];
        p[i] = p[length - 1 - i];
        p[length - 1 - i] = tmp;
    }
}

// Convert uint8_t to decimal string
// Returns the number of characters written (excluding null terminator)
uint8_t u8_to_dec(char *buf, uint8_t buflen, uint8_t value) {
    if (buflen < 4) {  // Need at least 3 chars + null (for "255")
        return 0;
    }

    uint8_t digits[3];
    uint8_t count = 0;

    // Extract digits (least significant first)
    if (value == 0) {
        buf[0] = '0';
        buf[1] = '\0';
        return 1;
    }

    uint8_t temp = value;
    while (temp > 0 && count < 3) {
        digits[count++] = '0' + (temp % 10);
        temp /= 10;
    }

    // Write digits in reverse order (most significant first)
    for (uint8_t i = 0; i < count; i++) {
        buf[i] = digits[count - 1 - i];
    }
    buf[count] = '\0';

    return count;
}
