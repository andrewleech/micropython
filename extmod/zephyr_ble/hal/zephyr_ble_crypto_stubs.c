/*
 * Zephyr Crypto Stubs
 * Provides stub implementations for unimplemented crypto functions
 *
 * Implemented functions are in zephyr_ble_crypto.c (TinyCrypt-based):
 * - bt_crypto_aes_cmac, bt_crypto_f4/f5/f6/g2 (SC crypto)
 * - bt_pub_key_gen, bt_dh_key_gen (ECC)
 * - bt_encrypt_le (Legacy pairing)
 * - bt_crypto_init
 */

#include <stdint.h>
#include <stddef.h>
#include <string.h>

// MicroPython headers for debug output
#include "py/runtime.h"

// Include STM32 HAL header to get platform defines (STM32WB, etc.)
// This is needed for platform-specific RNG selection in bt_rand()
#ifdef STM32_HAL_H
#include STM32_HAL_H
#endif

#include <errno.h>

// pico-sdk hardware RNG for RP2040/RP2350
#if PICO_RP2040 || PICO_RP2350
#include "pico/rand.h"
#endif

// Linux getrandom() for Unix port
#if defined(__linux__)
#include <sys/random.h>
#endif

// =============================================================================
// Platform-Specific Hardware RNG (used by all crypto functions)
// =============================================================================

// Random number generation
// Used by BLE stack for generating keys, nonces, etc.
// Uses platform hardware RNG or controller entropy when available
int bt_rand(void *buf, size_t len) {
    if (!buf) {
        return -EINVAL;
    }

    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
    // When controller is running on-core, use controller's entropy path.
    // lll_csrand_get() uses the hardware RNG peripheral via the controller's
    // entropy driver (if CONFIG_ENTROPY_HAS_DRIVER) or returns uninitialized
    // buffer contents as a fallback (see lll.c FIXME).
    extern int lll_csrand_get(void *buf, size_t len);
    return lll_csrand_get(buf, len);

    #elif PICO_RP2040 || PICO_RP2350
    // RP2040/RP2350: Use pico-sdk hardware RNG (pico/rand.h)
    uint8_t *p = (uint8_t *)buf;
    for (size_t i = 0; i < len;) {
        uint32_t r = get_rand_32();
        size_t chunk = len - i;
        if (chunk > 4) {
            chunk = 4;
        }
        memcpy(p + i, &r, chunk);
        i += chunk;
    }
    return 0;

    #elif defined(STM32_HAL_H)
    // STM32: Use hardware RNG peripheral (rng_get() available on all STM32 ports)
    extern uint32_t rng_get(void);
    uint8_t *p = (uint8_t *)buf;
    for (size_t i = 0; i < len; i++) {
        if (i % 4 == 0) {
            // Get new 32-bit random value every 4 bytes
            uint32_t r = rng_get();
            p[i] = r & 0xFF;
            if (i + 1 < len) {
                p[i + 1] = (r >> 8) & 0xFF;
            }
            if (i + 2 < len) {
                p[i + 2] = (r >> 16) & 0xFF;
            }
            if (i + 3 < len) {
                p[i + 3] = (r >> 24) & 0xFF;
            }
        }
    }
    return 0;

    #elif defined(__linux__)
    // Linux: Use getrandom() syscall for cryptographically secure random bytes
    ssize_t ret = getrandom(buf, len, 0);
    if (ret < 0 || (size_t)ret != len) {
        return -5; // -EIO
    }
    return 0;

    #else
    // Platform must provide hardware RNG implementation
    return -ENOSYS;
    #endif
}

// =============================================================================
// Unimplemented Crypto Functions (Stubs)
// =============================================================================

// AES ECB encryption stub
// Used by rpa.c for generating resolvable private addresses
// When controller is enabled, ecb.c provides the real ecb_encrypt via hardware ECB.
#if !MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
int ecb_encrypt(const uint8_t *key, const uint8_t *clear_text,
    uint8_t *cipher_text, uint8_t length) {
    (void)key;
    (void)clear_text;
    (void)cipher_text;
    (void)length;
    // Return error - RPA not implemented yet
    return -EIO;
}
#endif

// Controller random number generator stub
// When controller is enabled, lll.c provides the real lll_csrand_get.
#if !MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
int lll_csrand_get(void *buf, size_t len) {
    (void)buf;
    (void)len;
    // Return error - controller crypto not available
    return -ENOSYS;
}
#endif
