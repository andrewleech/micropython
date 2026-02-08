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

// Copy memory buffer with byte-swap
// Copies from src to dst while reversing byte order
// NOTE: src and dst buffers must not overlap
void sys_memcpy_swap(void *dst, const void *src, size_t length) {
    uint8_t *pdst = (uint8_t *)dst;
    const uint8_t *psrc = (const uint8_t *)src;

    psrc += length - 1;

    for (size_t i = 0; i < length; i++) {
        *pdst++ = *psrc--;
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

// strtoul() stub required by Zephyr BLE stack (keys.c, gatt.c)
// Even though CONFIG_BT_SETTINGS=0, the code still references this function
// This should never be called with our configuration, but provide a stub to avoid linker errors
unsigned long strtoul(const char *nptr, char **endptr, int base) {
    // Simple implementation: convert hex/decimal string to unsigned long
    unsigned long result = 0;
    const char *p = nptr;

    // Skip whitespace
    while (*p == ' ' || *p == '\t') {
        p++;
    }

    // Auto-detect base if base==0
    if (base == 0) {
        if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
            base = 16;
            p += 2;
        } else if (p[0] == '0') {
            base = 8;
            p++;
        } else {
            base = 10;
        }
    } else if (base == 16 && p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        p += 2;
    }

    // Convert digits
    while (*p) {
        int digit;
        if (*p >= '0' && *p <= '9') {
            digit = *p - '0';
        } else if (*p >= 'a' && *p <= 'f') {
            digit = *p - 'a' + 10;
        } else if (*p >= 'A' && *p <= 'F') {
            digit = *p - 'A' + 10;
        } else {
            break;
        }

        if (digit >= base) {
            break;
        }

        result = result * base + digit;
        p++;
    }

    if (endptr) {
        *endptr = (char *)p;
    }

    return result;
}
