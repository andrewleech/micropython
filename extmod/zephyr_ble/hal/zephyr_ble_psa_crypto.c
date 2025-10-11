/*
 * PSA Crypto API Stubs
 * Stub implementations for PSA crypto functions used by GATT database hashing
 * Phase 1: GATT caching disabled, these functions won't be called
 */

#include <stdint.h>
#include <stddef.h>

// PSA status type (standard across PSA APIs)
typedef int32_t psa_status_t;

// PSA key handle type
typedef uint32_t psa_key_id_t;

// PSA success code
#define PSA_SUCCESS 0

// Stub: Update MAC operation (incremental hashing)
psa_status_t psa_mac_update(void *operation, const uint8_t *input, size_t input_length) {
    (void)operation;
    (void)input;
    (void)input_length;
    // Phase 1: GATT caching disabled (CONFIG_BT_GATT_CACHING=0)
    // This function won't be called
    return PSA_SUCCESS;
}

// Stub: Finish MAC operation and get signature
psa_status_t psa_mac_sign_finish(void *operation, uint8_t *mac, size_t mac_size, size_t *mac_length) {
    (void)operation;
    (void)mac;
    (void)mac_size;
    (void)mac_length;
    // Phase 1: GATT caching disabled
    return PSA_SUCCESS;
}

// Stub: Destroy a key
psa_status_t psa_destroy_key(psa_key_id_t key) {
    (void)key;
    // Phase 1: GATT caching disabled
    return PSA_SUCCESS;
}

// Stub: Import a key
psa_status_t psa_import_key(const void *attributes, const uint8_t *data,
                             size_t data_length, psa_key_id_t *key) {
    (void)attributes;
    (void)data;
    (void)data_length;
    (void)key;
    // Phase 1: GATT caching disabled
    return PSA_SUCCESS;
}

// Stub: Set up a MAC signing operation
psa_status_t psa_mac_sign_setup(void *operation, psa_key_id_t key, uint32_t alg) {
    (void)operation;
    (void)key;
    (void)alg;
    // Phase 1: GATT caching disabled
    return PSA_SUCCESS;
}
