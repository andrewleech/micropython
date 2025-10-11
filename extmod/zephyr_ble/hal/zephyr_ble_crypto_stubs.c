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
