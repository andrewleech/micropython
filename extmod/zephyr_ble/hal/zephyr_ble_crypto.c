/*
 * Zephyr BLE Secure Connections Crypto Implementation
 * Provides cryptographic functions for LE Secure Connections pairing
 * Uses TinyCrypt library for AES-CMAC and ECC operations
 */

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>

// Platform detection
#if defined(__ARM_ARCH_6M__)
// RP2040/RP2350: TinyCrypt available via NimBLE
#include <tinycrypt/cmac_mode.h>
#include <tinycrypt/aes.h>
#include <tinycrypt/ecc.h>
#include <tinycrypt/ecc_dh.h>
#include <tinycrypt/constants.h>

// MicroPython headers for debug output
#include "py/runtime.h"

// Zephyr BLE address types
#include <zephyr/bluetooth/addr.h>

// Utility functions for byte-order swapping (implemented in zephyr_ble_util.c)
extern void sys_memcpy_swap(void *dst, const void *src, size_t length);
extern void sys_mem_swap(void *buf, size_t length);

// Debug output (matches pattern in modbluetooth_zephyr.c)
#if ZEPHYR_BLE_DEBUG
#define DEBUG_printf(...) mp_printf(&mp_plat_print, "CRYPTO: " __VA_ARGS__)
#else
#define DEBUG_printf(...) do {} while (0)
#endif

// =============================================================================
// AES-CMAC (Foundation for SC crypto functions)
// =============================================================================

// AES-CMAC function (for signing and cryptographic function building blocks)
// Uses TinyCrypt implementation
// Returns 0 on success, negative error code on failure
int bt_crypto_aes_cmac(const uint8_t *key, const uint8_t *in, size_t len,
                       uint8_t *out) {
    struct tc_cmac_struct state;
    struct tc_aes_key_sched_struct sched;

    DEBUG_printf("bt_crypto_aes_cmac: len=%u\n", (unsigned)len);

    // Setup AES-128 key schedule
    if (tc_aes128_set_encrypt_key(&sched, key) != TC_CRYPTO_SUCCESS) {
        DEBUG_printf("bt_crypto_aes_cmac: key setup failed\n");
        return -1;  // -EIO
    }

    // Initialize CMAC context
    if (tc_cmac_setup(&state, key, &sched) != TC_CRYPTO_SUCCESS) {
        DEBUG_printf("bt_crypto_aes_cmac: cmac setup failed\n");
        return -1;
    }

    // Update CMAC with input data
    if (tc_cmac_update(&state, in, len) != TC_CRYPTO_SUCCESS) {
        DEBUG_printf("bt_crypto_aes_cmac: cmac update failed\n");
        return -1;
    }

    // Finalize CMAC and get output
    if (tc_cmac_final(out, &state) != TC_CRYPTO_SUCCESS) {
        DEBUG_printf("bt_crypto_aes_cmac: cmac final failed\n");
        return -1;
    }

    DEBUG_printf("bt_crypto_aes_cmac: SUCCESS\n");
    return 0;
}

// =============================================================================
// SC Cryptographic Functions (defined in BT Core Spec Vol 3 Part H 2.2)
// =============================================================================

// f4 function for LE Secure Connections confirm value generation
// Core Spec 4.2 Vol 3 Part H 2.2.5
// f4(U, V, X, Z) = AES-CMAC_X(U || V || Z)
// where U, V are 256-bit public key coordinates (32 bytes each)
//       X is 128-bit key (16 bytes)
//       Z is 8-bit value (1 byte)
int bt_crypto_f4(const uint8_t *u, const uint8_t *v, const uint8_t *x,
                 uint8_t z, uint8_t *res) {
    uint8_t xs[16];
    uint8_t m[65];  // U (32) + V (32) + Z (1) = 65 bytes
    int err;

    DEBUG_printf("bt_crypto_f4\n");

    // U, V and Z are concatenated and used as input m to AES-CMAC
    // X is used as the key k
    // NOTE: Zephyr/BT stack uses big-endian, but SMP uses little-endian
    // So we swap byte order for key and input, then swap result back
    sys_memcpy_swap(m, u, 32);
    sys_memcpy_swap(m + 32, v, 32);
    m[64] = z;

    sys_memcpy_swap(xs, x, 16);

    err = bt_crypto_aes_cmac(xs, m, sizeof(m), res);
    if (err) {
        return err;
    }

    sys_mem_swap(res, 16);

    DEBUG_printf("bt_crypto_f4: SUCCESS\n");
    return 0;
}

// f5 function for LE Secure Connections LTK and MacKey generation
// Core Spec 4.2 Vol 3 Part H 2.2.7
// f5(W, N1, N2, A1, A2) = (MacKey, LTK)
// where W is DHKey (256 bits / 32 bytes)
//       N1, N2 are nonces (128 bits / 16 bytes each)
//       A1, A2 are bt_addr_le_t structures (type + 6 bytes addr)
int bt_crypto_f5(const uint8_t *w, const uint8_t *n1, const uint8_t *n2,
                 const bt_addr_le_t *a1, const bt_addr_le_t *a2,
                 uint8_t *mackey, uint8_t *ltk) {
    // Salt for f5 (defined in spec)
    static const uint8_t salt[16] = {
        0x6c, 0x88, 0x83, 0x91, 0xaa, 0xf5, 0xa5, 0x38,
        0x60, 0x37, 0x0b, 0xdb, 0x5a, 0x60, 0x83, 0xbe
    };

    // Message for AES-CMAC:
    // counter (1) || keyID (4) || N1 (16) || N2 (16) || A1 (7) || A2 (7) || length (2)
    uint8_t m[53] = {
        0x00,                           // counter
        0x62, 0x74, 0x6c, 0x65,        // keyID "btle"
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  // N1 placeholder
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  // N2 placeholder
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,        // A1 placeholder
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,        // A2 placeholder
        0x01, 0x00                                        // length = 256 bits
    };
    uint8_t t[16], ws[32];
    int err;

    DEBUG_printf("bt_crypto_f5\n");

    // Step 1: Compute T = AES-CMAC_salt(W)
    sys_memcpy_swap(ws, w, 32);
    err = bt_crypto_aes_cmac(salt, ws, 32, t);
    if (err) {
        return err;
    }

    // Step 2: Fill message with N1, N2, A1, A2
    sys_memcpy_swap(m + 5, n1, 16);
    sys_memcpy_swap(m + 21, n2, 16);
    m[37] = a1->type;  // address type
    sys_memcpy_swap(m + 38, a1->a.val, 6);  // address bytes
    m[44] = a2->type;  // address type
    sys_memcpy_swap(m + 45, a2->a.val, 6);  // address bytes

    // Step 3: Compute MacKey = AES-CMAC_T(Counter || keyID || N1 || N2 || A1 || A2 || Length)
    // Counter = 0x00 for MacKey
    err = bt_crypto_aes_cmac(t, m, sizeof(m), mackey);
    if (err) {
        return err;
    }
    sys_mem_swap(mackey, 16);

    // Step 4: Compute LTK = AES-CMAC_T(Counter || keyID || N1 || N2 || A1 || A2 || Length)
    // Counter = 0x01 for LTK
    m[0] = 0x01;
    err = bt_crypto_aes_cmac(t, m, sizeof(m), ltk);
    if (err) {
        return err;
    }
    sys_mem_swap(ltk, 16);

    DEBUG_printf("bt_crypto_f5: SUCCESS\n");
    return 0;
}

// f6 function for LE Secure Connections check value generation
// Core Spec 4.2 Vol 3 Part H 2.2.8
// f6(W, N1, N2, R, IOcap, A1, A2) = AES-CMAC_W(N1 || N2 || R || IOcap || A1 || A2)
int bt_crypto_f6(const uint8_t *w, const uint8_t *n1, const uint8_t *n2,
                 const uint8_t *r, const uint8_t *io_cap,
                 const bt_addr_le_t *a1, const bt_addr_le_t *a2,
                 uint8_t *res) {
    // Message: N1 (16) || N2 (16) || R (16) || IOcap (3) || A1 (7) || A2 (7) = 65 bytes
    uint8_t m[65];
    uint8_t ws[16];
    int err;

    DEBUG_printf("bt_crypto_f6\n");

    // Build message
    sys_memcpy_swap(m, n1, 16);
    sys_memcpy_swap(m + 16, n2, 16);
    sys_memcpy_swap(m + 32, r, 16);
    sys_memcpy_swap(m + 48, io_cap, 3);
    m[51] = a1->type;  // address type
    sys_memcpy_swap(m + 52, a1->a.val, 6);  // address bytes
    m[58] = a2->type;  // address type
    sys_memcpy_swap(m + 59, a2->a.val, 6);  // address bytes

    // Compute f6 = AES-CMAC_W(message)
    sys_memcpy_swap(ws, w, 16);
    err = bt_crypto_aes_cmac(ws, m, sizeof(m), res);
    if (err) {
        return err;
    }
    sys_mem_swap(res, 16);

    DEBUG_printf("bt_crypto_f6: SUCCESS\n");
    return 0;
}

// g2 function for LE Secure Connections numeric comparison value generation
// Core Spec 4.2 Vol 3 Part H 2.2.8
// g2(U, V, X, Y) = AES-CMAC_X(U || V || Y) mod 2^32
// Returns a 6-digit passkey value (0-999999)
int bt_crypto_g2(const uint8_t u[32], const uint8_t v[32],
                 const uint8_t x[16], const uint8_t y[16], uint32_t *passkey) {
    uint8_t m[80];  // U (32) + V (32) + Y (16) = 80 bytes
    uint8_t xs[16];
    uint8_t cmac[16];
    int err;

    DEBUG_printf("bt_crypto_g2\n");

    // Build message: U || V || Y
    sys_memcpy_swap(m, u, 32);
    sys_memcpy_swap(m + 32, v, 32);
    sys_memcpy_swap(m + 64, y, 16);

    // Compute AES-CMAC
    sys_memcpy_swap(xs, x, 16);
    err = bt_crypto_aes_cmac(xs, m, sizeof(m), cmac);
    if (err) {
        return err;
    }

    // Extract 32-bit value from CMAC output (little-endian)
    // Take least significant 32 bits, then mod 1000000 to get 6-digit passkey
    uint32_t val = cmac[0] | (cmac[1] << 8) | (cmac[2] << 16) | (cmac[3] << 24);
    *passkey = val % 1000000;

    DEBUG_printf("bt_crypto_g2: passkey=%u\n", (unsigned)*passkey);
    return 0;
}

// =============================================================================
// ECC Functions (P-256 curve, required for SC pairing)
// =============================================================================

// Static storage for our ECC keypair (persists across pairing sessions)
static uint8_t ecc_priv_key[NUM_ECC_BYTES];        // 32 bytes
static uint8_t ecc_pub_key[NUM_ECC_BYTES * 2];     // 64 bytes (X || Y coordinates)
static bool ecc_key_valid = false;

// ECC public key generation callback structure
typedef void (*bt_pub_key_cb_func_t)(const uint8_t *key);
struct bt_pub_key_cb {
    void *node;  // sys_snode_t (unused in our implementation)
    bt_pub_key_cb_func_t func;
};

// Generate ECC P-256 public/private keypair
// Callback is invoked with public key (64 bytes) on success, NULL on failure
int bt_pub_key_gen(struct bt_pub_key_cb *cb) {
    uint8_t pub_key_be[NUM_ECC_BYTES * 2];  // Temp buffer for big-endian key

    DEBUG_printf("bt_pub_key_gen called\n");

    // Generate P-256 keypair using TinyCrypt
    // TinyCrypt produces keys in big-endian format
    // Public key: 64 bytes (X || Y coordinates, 32 bytes each)
    // Private key: 32 bytes
    int ret = uECC_make_key(pub_key_be, ecc_priv_key, uECC_secp256r1());

    if (ret == 0) {
        DEBUG_printf("bt_pub_key_gen: FAILED\n");
        ecc_key_valid = false;

        // Invoke callback with NULL to indicate failure
        if (cb && cb->func) {
            cb->func(NULL);
        }
        return -1;
    }

    // Convert public key from big-endian to little-endian
    // BLE uses little-endian format for keys in SMP
    // Each coordinate (X and Y) is swapped separately
    sys_memcpy_swap(ecc_pub_key, pub_key_be, 32);  // X coordinate
    sys_memcpy_swap(ecc_pub_key + 32, pub_key_be + 32, 32);  // Y coordinate

    ecc_key_valid = true;
    DEBUG_printf("bt_pub_key_gen: SUCCESS\n");

    // Invoke callback with public key (now in little-endian)
    if (cb && cb->func) {
        cb->func(ecc_pub_key);
    }

    return 0;
}

// Get current ECC P-256 public key
// Returns pointer to 64-byte public key if available, NULL otherwise
// Called by SMP to get our public key during SC pairing
const uint8_t *bt_pub_key_get(void) {
    DEBUG_printf("bt_pub_key_get: valid=%d\n", ecc_key_valid);
    if (ecc_key_valid) {
        return ecc_pub_key;
    }
    return NULL;
}

// BT Core Spec debug public key (Vol 3 Part H 2.3.5.6.1)
// Used for testing SC pairing without real key generation
static const uint8_t debug_public_key[64] = {
    // X coordinate
    0xe6, 0x9d, 0x35, 0x0e, 0x48, 0x01, 0x03, 0xcc,
    0xdb, 0xfd, 0xf4, 0xac, 0x11, 0x91, 0xf4, 0xef,
    0xb9, 0xa5, 0xf9, 0xe9, 0xa7, 0x83, 0x2c, 0x5e,
    0x2c, 0xbe, 0x97, 0xf2, 0xd2, 0x03, 0xb0, 0x20,
    // Y coordinate
    0x8b, 0xd2, 0x89, 0x15, 0xd0, 0x8e, 0x1c, 0x74,
    0x24, 0x30, 0xed, 0x8f, 0xc2, 0x45, 0x63, 0x76,
    0x5c, 0x15, 0x52, 0x5a, 0xbf, 0x9a, 0x32, 0x63,
    0x6d, 0xeb, 0x2a, 0x65, 0x49, 0x9c, 0x80, 0xdc
};

// Check if public key is the BT debug public key
// Returns true if the key matches the spec-defined debug key
bool bt_pub_key_is_debug(uint8_t *cmp_pub_key) {
    DEBUG_printf("bt_pub_key_is_debug\n");
    return memcmp(cmp_pub_key, debug_public_key, 64) == 0;
}

// Validate that a public key lies on the P-256 curve
// Returns true if the key is valid
bool bt_pub_key_is_valid(const uint8_t key[64]) {
    DEBUG_printf("bt_pub_key_is_valid\n");
    // uECC_valid_public_key returns 1 if valid, 0 if invalid
    return uECC_valid_public_key(key, uECC_secp256r1()) != 0;
}

// ECC Diffie-Hellman shared secret computation
// Callback is invoked with DH key (32 bytes) on success, NULL on failure
typedef void (*bt_dh_key_cb_t)(const uint8_t *key);

int bt_dh_key_gen(const uint8_t remote_pk[64], bt_dh_key_cb_t cb) {
    uint8_t remote_pk_be[NUM_ECC_BYTES * 2];  // Remote public key in big-endian
    uint8_t dh_key_be[NUM_ECC_BYTES];         // DH key in big-endian
    uint8_t dh_key[NUM_ECC_BYTES];            // DH key in little-endian

    DEBUG_printf("bt_dh_key_gen called\n");

    // Verify we have a valid private key
    if (!ecc_key_valid) {
        DEBUG_printf("bt_dh_key_gen: no valid private key\n");
        if (cb) {
            cb(NULL);
        }
        return -1;
    }

    // Convert remote public key from little-endian to big-endian for TinyCrypt
    sys_memcpy_swap(remote_pk_be, remote_pk, 32);  // X coordinate
    sys_memcpy_swap(remote_pk_be + 32, remote_pk + 32, 32);  // Y coordinate

    // Compute ECDH shared secret: DH = remote_public_key * our_private_key
    // TinyCrypt expects big-endian inputs and produces big-endian output
    // Result is the X coordinate of the shared point (32 bytes)
    int ret = uECC_shared_secret(remote_pk_be, ecc_priv_key, dh_key_be, uECC_secp256r1());

    if (ret == 0) {
        DEBUG_printf("bt_dh_key_gen: FAILED\n");
        if (cb) {
            cb(NULL);
        }
        return -1;
    }

    // Convert DH key from big-endian to little-endian for BLE
    sys_memcpy_swap(dh_key, dh_key_be, 32);

    DEBUG_printf("bt_dh_key_gen: SUCCESS\n");

    // Invoke callback with DH key (now in little-endian)
    if (cb) {
        cb(dh_key);
    }

    return 0;
}

// Called when HCI connection is disrupted during ECC key generation
// Invalidates any in-progress public key generation
void bt_pub_key_hci_disrupted(void) {
    DEBUG_printf("bt_pub_key_hci_disrupted\n");
    // Invalidate current keypair (forces regeneration on next pairing)
    ecc_key_valid = false;
}

// Zephyr BLE random number generator
// Declared in zephyr/bluetooth/crypto.h
extern int bt_rand(void *buf, size_t len);

// TinyCrypt RNG wrapper using Zephyr's bt_rand()
// Returns 1 on success, 0 on failure (TinyCrypt convention)
static int zephyr_rng_wrapper(uint8_t *dest, unsigned int size) {
    return bt_rand(dest, size) == 0 ? 1 : 0;
}

// Crypto module initialization
// Called by bt_init() in hci_core.c
int bt_crypto_init(void) {
    DEBUG_printf("bt_crypto_init\n");

    // Initialize TinyCrypt RNG with Zephyr's bt_rand()
    // This is required for uECC_make_key() to work
    uECC_set_rng(zephyr_rng_wrapper);

    DEBUG_printf("bt_crypto_init: RNG initialized\n");
    return 0;
}

// =============================================================================
// Legacy Pairing Support (AES-128-ECB for Just Works)
// =============================================================================

// LE encryption function (used for legacy pairing)
// e(key, plaintext) -> returns ciphertext
// Implements AES-128-ECB encryption using TinyCrypt
int bt_encrypt_le(const uint8_t key[16], const uint8_t plaintext[16],
                  uint8_t enc_data[16]) {
    struct tc_aes_key_sched_struct sched;
    uint8_t tmp[16];
    int ret;

    DEBUG_printf("bt_encrypt_le\n");

    // Validate parameters
    if (!key || !plaintext || !enc_data) {
        DEBUG_printf("bt_encrypt_le: NULL parameter\n");
        return -22; // -EINVAL
    }

    // BLE uses little-endian byte order, but AES uses big-endian
    // Swap key bytes for AES
    sys_memcpy_swap(tmp, key, 16);

    // Set AES encryption key
    if (tc_aes128_set_encrypt_key(&sched, tmp) != TC_CRYPTO_SUCCESS) {
        DEBUG_printf("bt_encrypt_le: set_key FAILED\n");
        ret = -1;
        goto cleanup;
    }

    // Swap plaintext bytes
    sys_memcpy_swap(tmp, plaintext, 16);

    // Encrypt using AES-128-ECB
    if (tc_aes_encrypt(enc_data, tmp, &sched) != TC_CRYPTO_SUCCESS) {
        DEBUG_printf("bt_encrypt_le: encrypt FAILED\n");
        ret = -1;
        goto cleanup;
    }

    // Swap result back to little-endian
    sys_mem_swap(enc_data, 16);

    ret = 0;
    DEBUG_printf("bt_encrypt_le: SUCCESS\n");

cleanup:
    // Zero sensitive key material
    memset(&sched, 0, sizeof(sched));
    memset(tmp, 0, sizeof(tmp));
    return ret;
}

#else
// Other platforms: TinyCrypt not available
#error "TinyCrypt SC crypto implementation requires RP2 platform (__ARM_ARCH_6M__)"
#endif
