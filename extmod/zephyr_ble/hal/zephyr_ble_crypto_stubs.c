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

// =============================================================================
// Platform-Specific Hardware RNG (used by all crypto functions)
// =============================================================================

// Random number generation
// Used by BLE stack for generating keys, nonces, etc.
// Uses platform hardware RNG or controller entropy when available
int bt_rand(void *buf, size_t len) {
    if (!buf) {
        return -22; // -EINVAL
    }

    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
    // When controller is running on-core, use controller's entropy path.
    // lll_csrand_get() uses the hardware RNG peripheral via the controller's
    // entropy driver (if CONFIG_ENTROPY_HAS_DRIVER) or returns uninitialized
    // buffer contents as a fallback (see lll.c FIXME).
    extern int lll_csrand_get(void *buf, size_t len);
    return lll_csrand_get(buf, len);

    #elif defined(__ARM_ARCH_6M__)
    // RP2040/RP2350: Use ROSC hardware RNG
    extern uint8_t rosc_random_u8(size_t cycles);
    uint8_t *p = (uint8_t *)buf;
    for (size_t i = 0; i < len; i++) {
        p[i] = rosc_random_u8(8);
    }
    return 0;

    #elif defined(STM32WB)
    // STM32WB: Use hardware RNG peripheral
    extern uint32_t rng_get(void);
    uint8_t *p = (uint8_t *)buf;
    for (size_t i = 0; i < len; i++) {
        if (i % 4 == 0) {
            // Get new 32-bit random value every 4 bytes
            uint32_t r = rng_get();
            p[i] = r & 0xFF;
            if (i + 1 < len) p[i + 1] = (r >> 8) & 0xFF;
            if (i + 2 < len) p[i + 2] = (r >> 16) & 0xFF;
            if (i + 3 < len) p[i + 3] = (r >> 24) & 0xFF;
        }
    }
    return 0;

    #else
    // Platform must provide hardware RNG implementation
    return -38; // -ENOSYS
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
    return -1;
}
#endif

// Controller random number generator stub
// When controller is enabled, lll.c provides the real lll_csrand_get.
#if !MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
int lll_csrand_get(void *buf, size_t len) {
    (void)buf;
    (void)len;
    // Return error - controller crypto not available
    return -1;
}
#endif
