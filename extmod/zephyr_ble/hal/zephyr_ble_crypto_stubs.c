/*
 * Zephyr Crypto Stubs
 * Provides stub implementations for crypto functions used by BLE stack
 */

#include <stdint.h>
#include <string.h>

// AES ECB encryption stub
// Used by rpa.c for generating resolvable private addresses
// In Phase 1, we return an error to indicate crypto not available
// TODO: Implement using TinyCrypt or MicroPython's crypto module
int ecb_encrypt(const uint8_t *key, const uint8_t *clear_text,
                uint8_t *cipher_text, uint8_t length) {
    (void)key;
    (void)clear_text;
    (void)cipher_text;
    (void)length;
    // Return error - crypto not implemented yet
    return -1;
}

// BT Crypto functions - used for SMP secure connections
// These implement cryptographic functions defined in Bluetooth spec

// AES-CMAC function (for signing)
int bt_crypto_aes_cmac(const uint8_t *key, const uint8_t *in, size_t len,
                       uint8_t *out) {
    (void)key;
    (void)in;
    (void)len;
    (void)out;
    // Return error - not implemented
    return -1;
}

// f4 function for LE Secure Connections confirm value generation
int bt_crypto_f4(const uint8_t *u, const uint8_t *v, const uint8_t *x,
                 uint8_t z, uint8_t *res) {
    (void)u;
    (void)v;
    (void)x;
    (void)z;
    (void)res;
    // Return error - not implemented
    return -1;
}

// f5 function for LE Secure Connections key generation
int bt_crypto_f5(const uint8_t *w, const uint8_t *n1, const uint8_t *n2,
                 const uint8_t *a1, const uint8_t *a2, uint8_t *mackey,
                 uint8_t *ltk) {
    (void)w;
    (void)n1;
    (void)n2;
    (void)a1;
    (void)a2;
    (void)mackey;
    (void)ltk;
    // Return error - not implemented
    return -1;
}

// f6 function for LE Secure Connections check value generation
int bt_crypto_f6(const uint8_t *w, const uint8_t *n1, const uint8_t *n2,
                 const uint8_t *r, const uint8_t *io_cap, const uint8_t *a1,
                 const uint8_t *a2, uint8_t *res) {
    (void)w;
    (void)n1;
    (void)n2;
    (void)r;
    (void)io_cap;
    (void)a1;
    (void)a2;
    (void)res;
    // Return error - not implemented
    return -1;
}

// Controller random number generator stub
// This is referenced in rpa.c but should never be called since CONFIG_BT_CTLR_CRYPTO=0
int lll_csrand_get(void *buf, size_t len) {
    (void)buf;
    (void)len;
    // Return error - controller crypto not available
    return -1;
}

// Crypto module initialization
// Called by bt_init() in hci_core.c
int bt_crypto_init(void) {
    // No-op in Phase 1 (crypto not available)
    return 0;
}

// Random number generation
// Used by BLE stack for generating keys, nonces, etc.
// TODO: Implement using MicroPython's random module or hardware RNG
int bt_rand(void *buf, size_t len) {
    // For now, fill with zeros (INSECURE - only for build testing)
    // Real implementation must use secure random source
    memset(buf, 0, len);
    return 0;
}

// LE encryption function (used for legacy pairing)
// e(key, plaintext) -> returns ciphertext
int bt_encrypt_le(const uint8_t key[16], const uint8_t plaintext[16],
                  uint8_t enc_data[16]) {
    (void)key;
    (void)plaintext;
    (void)enc_data;
    // Return error - not implemented
    return -1;
}

// ECC public key generation (used for LE Secure Connections)
// Takes a callback that's called when key is ready
typedef void (*bt_pub_key_cb_func_t)(const uint8_t *key);
struct bt_pub_key_cb {
    void *node;  // sys_snode_t
    bt_pub_key_cb_func_t func;
};
int bt_pub_key_gen(struct bt_pub_key_cb *cb) {
    // Call callback with NULL to indicate failure
    if (cb && cb->func) {
        cb->func(NULL);
    }
    return -1;
}

// ECC Diffie-Hellman key generation (used for LE Secure Connections)
// remote_pk[64] = remote public key (X,Y coordinates)
// cb = callback when DH key is ready
typedef void (*bt_dh_key_cb_t)(const uint8_t *key);
int bt_dh_key_gen(const uint8_t remote_pk[64], bt_dh_key_cb_t cb) {
    (void)remote_pk;
    // Call callback with NULL to indicate failure
    if (cb) {
        cb(NULL);
    }
    return -1;
}

// g2 function for LE Secure Connections numeric comparison
int bt_crypto_g2(const uint8_t u[32], const uint8_t v[32],
                 const uint8_t x[16], const uint8_t y[16], uint32_t *passkey) {
    (void)u;
    (void)v;
    (void)x;
    (void)y;
    (void)passkey;
    // Return error - not implemented
    return -1;
}

// Standard C library function strtoul
// Convert string to unsigned long
// This is needed by settings code but not available in all embedded libcs
unsigned long strtoul(const char *nptr, char **endptr, int base) {
    unsigned long result = 0;
    int digit;

    (void)base;  // Simple implementation ignores base

    if (!nptr) {
        if (endptr) *endptr = (char*)nptr;
        return 0;
    }

    // Skip whitespace
    while (*nptr == ' ' || *nptr == '\t') nptr++;

    // Parse digits
    while (*nptr) {
        if (*nptr >= '0' && *nptr <= '9') {
            digit = *nptr - '0';
        } else if (*nptr >= 'a' && *nptr <= 'f') {
            digit = *nptr - 'a' + 10;
        } else if (*nptr >= 'A' && *nptr <= 'F') {
            digit = *nptr - 'A' + 10;
        } else {
            break;
        }
        result = result * 16 + digit;  // Assuming hex
        nptr++;
    }

    if (endptr) *endptr = (char*)nptr;
    return result;
}
