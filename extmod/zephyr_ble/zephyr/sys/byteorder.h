/*
 * Zephyr byteorder.h wrapper for MicroPython
 */

#ifndef ZEPHYR_SYS_BYTEORDER_H_
#define ZEPHYR_SYS_BYTEORDER_H_

#include <stdint.h>

// Zephyr uses these byte swap macros extensively

// Byte swap for 16-bit values
static inline uint16_t sys_bswap_16(uint16_t val) {
    return ((val & 0x00FFU) << 8) | ((val & 0xFF00U) >> 8);
}

// Byte swap for 32-bit values
static inline uint32_t sys_bswap_32(uint32_t val) {
    return ((val & 0x000000FFU) << 24) |
           ((val & 0x0000FF00U) << 8) |
           ((val & 0x00FF0000U) >> 8) |
           ((val & 0xFF000000U) >> 24);
}

// Byte swap for 64-bit values
static inline uint64_t sys_bswap_64(uint64_t val) {
    return ((val & 0x00000000000000FFULL) << 56) |
           ((val & 0x000000000000FF00ULL) << 40) |
           ((val & 0x0000000000FF0000ULL) << 24) |
           ((val & 0x00000000FF000000ULL) << 8) |
           ((val & 0x000000FF00000000ULL) >> 8) |
           ((val & 0x0000FF0000000000ULL) >> 24) |
           ((val & 0x00FF000000000000ULL) >> 40) |
           ((val & 0xFF00000000000000ULL) >> 56);
}

// Zephyr byte order conversion macros
// Assumes little-endian host (CONFIG_LITTLE_ENDIAN=1)

// Verify host is little-endian at compile time
#if defined(__BYTE_ORDER__)
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
#error "Big-endian hosts not supported - byte order conversion needs updating"
#elif __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "Unknown byte order"
#endif
#endif

#define sys_le16_to_cpu(val) (val)
#define sys_cpu_to_le16(val) (val)
#define sys_be16_to_cpu(val) sys_bswap_16(val)
#define sys_cpu_to_be16(val) sys_bswap_16(val)

#define sys_le32_to_cpu(val) (val)
#define sys_cpu_to_le32(val) (val)
#define sys_be32_to_cpu(val) sys_bswap_32(val)
#define sys_cpu_to_be32(val) sys_bswap_32(val)

#define sys_le64_to_cpu(val) (val)
#define sys_cpu_to_le64(val) (val)
#define sys_be64_to_cpu(val) sys_bswap_64(val)
#define sys_cpu_to_be64(val) sys_bswap_64(val)

// Put/get unaligned little-endian values
static inline void sys_put_le16(uint16_t val, uint8_t dst[2]) {
    dst[0] = val & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
}

static inline void sys_put_le32(uint32_t val, uint8_t dst[4]) {
    dst[0] = val & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
    dst[2] = (val >> 16) & 0xFF;
    dst[3] = (val >> 24) & 0xFF;
}

static inline uint16_t sys_get_le16(const uint8_t src[2]) {
    return ((uint16_t)src[0]) | (((uint16_t)src[1]) << 8);
}

static inline uint32_t sys_get_le32(const uint8_t src[4]) {
    return ((uint32_t)src[0]) |
           (((uint32_t)src[1]) << 8) |
           (((uint32_t)src[2]) << 16) |
           (((uint32_t)src[3]) << 24);
}

// Put/get unaligned big-endian values
static inline void sys_put_be16(uint16_t val, uint8_t dst[2]) {
    dst[0] = (val >> 8) & 0xFF;
    dst[1] = val & 0xFF;
}

static inline void sys_put_be32(uint32_t val, uint8_t dst[4]) {
    dst[0] = (val >> 24) & 0xFF;
    dst[1] = (val >> 16) & 0xFF;
    dst[2] = (val >> 8) & 0xFF;
    dst[3] = val & 0xFF;
}

static inline uint16_t sys_get_be16(const uint8_t src[2]) {
    return (((uint16_t)src[0]) << 8) | ((uint16_t)src[1]);
}

static inline uint32_t sys_get_be32(const uint8_t src[4]) {
    return (((uint32_t)src[0]) << 24) |
           (((uint32_t)src[1]) << 16) |
           (((uint32_t)src[2]) << 8) |
           ((uint32_t)src[3]);
}

static inline uint64_t sys_get_le64(const uint8_t src[8]) {
    return ((uint64_t)src[0]) |
           (((uint64_t)src[1]) << 8) |
           (((uint64_t)src[2]) << 16) |
           (((uint64_t)src[3]) << 24) |
           (((uint64_t)src[4]) << 32) |
           (((uint64_t)src[5]) << 40) |
           (((uint64_t)src[6]) << 48) |
           (((uint64_t)src[7]) << 56);
}

static inline uint64_t sys_get_be64(const uint8_t src[8]) {
    return (((uint64_t)src[0]) << 56) |
           (((uint64_t)src[1]) << 48) |
           (((uint64_t)src[2]) << 40) |
           (((uint64_t)src[3]) << 32) |
           (((uint64_t)src[4]) << 24) |
           (((uint64_t)src[5]) << 16) |
           (((uint64_t)src[6]) << 8) |
           ((uint64_t)src[7]);
}

// Memory copy with byte swap
static inline void sys_memcpy_swap(void *dst, const void *src, size_t length) {
    uint8_t *dst_u8 = (uint8_t *)dst;
    const uint8_t *src_u8 = (const uint8_t *)src;
    for (size_t i = 0; i < length; i++) {
        dst_u8[i] = src_u8[length - 1 - i];
    }
}

// 24-bit operations
static inline void sys_put_le24(uint32_t val, uint8_t dst[3]) {
    dst[0] = val & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
    dst[2] = (val >> 16) & 0xFF;
}

static inline void sys_put_be24(uint32_t val, uint8_t dst[3]) {
    dst[0] = (val >> 16) & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
    dst[2] = val & 0xFF;
}

static inline uint32_t sys_le24_to_cpu(uint32_t val) {
    return val & 0xFFFFFF;
}

static inline uint32_t sys_be24_to_cpu(uint32_t val) {
    return ((val & 0xFF) << 16) | (val & 0xFF00) | ((val & 0xFF0000) >> 16);
}

// 40-bit operations
static inline void sys_put_le40(uint64_t val, uint8_t dst[5]) {
    dst[0] = val & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
    dst[2] = (val >> 16) & 0xFF;
    dst[3] = (val >> 24) & 0xFF;
    dst[4] = (val >> 32) & 0xFF;
}

static inline void sys_put_be40(uint64_t val, uint8_t dst[5]) {
    dst[0] = (val >> 32) & 0xFF;
    dst[1] = (val >> 24) & 0xFF;
    dst[2] = (val >> 16) & 0xFF;
    dst[3] = (val >> 8) & 0xFF;
    dst[4] = val & 0xFF;
}

static inline uint64_t sys_le40_to_cpu(uint64_t val) {
    return val & 0xFFFFFFFFFFULL;
}

static inline uint64_t sys_be40_to_cpu(uint64_t val) {
    return ((val & 0xFF) << 32) | ((val & 0xFF00) << 16) | (val & 0xFF0000) |
           ((val & 0xFF000000) >> 16) | ((val & 0xFF00000000ULL) >> 32);
}

// 48-bit operations
static inline void sys_put_le48(uint64_t val, uint8_t dst[6]) {
    dst[0] = val & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
    dst[2] = (val >> 16) & 0xFF;
    dst[3] = (val >> 24) & 0xFF;
    dst[4] = (val >> 32) & 0xFF;
    dst[5] = (val >> 40) & 0xFF;
}

static inline void sys_put_be48(uint64_t val, uint8_t dst[6]) {
    dst[0] = (val >> 40) & 0xFF;
    dst[1] = (val >> 32) & 0xFF;
    dst[2] = (val >> 24) & 0xFF;
    dst[3] = (val >> 16) & 0xFF;
    dst[4] = (val >> 8) & 0xFF;
    dst[5] = val & 0xFF;
}

static inline uint64_t sys_le48_to_cpu(uint64_t val) {
    return val & 0xFFFFFFFFFFFFULL;
}

static inline uint64_t sys_be48_to_cpu(uint64_t val) {
    return ((val & 0xFF) << 40) | ((val & 0xFF00) << 24) | ((val & 0xFF0000) << 8) |
           ((val & 0xFF000000) >> 8) | ((val & 0xFF00000000ULL) >> 24) |
           ((val & 0xFF0000000000ULL) >> 40);
}

// 64-bit operations
static inline void sys_put_le64(uint64_t val, uint8_t dst[8]) {
    dst[0] = val & 0xFF;
    dst[1] = (val >> 8) & 0xFF;
    dst[2] = (val >> 16) & 0xFF;
    dst[3] = (val >> 24) & 0xFF;
    dst[4] = (val >> 32) & 0xFF;
    dst[5] = (val >> 40) & 0xFF;
    dst[6] = (val >> 48) & 0xFF;
    dst[7] = (val >> 56) & 0xFF;
}

static inline void sys_put_be64(uint64_t val, uint8_t dst[8]) {
    dst[0] = (val >> 56) & 0xFF;
    dst[1] = (val >> 48) & 0xFF;
    dst[2] = (val >> 40) & 0xFF;
    dst[3] = (val >> 32) & 0xFF;
    dst[4] = (val >> 24) & 0xFF;
    dst[5] = (val >> 16) & 0xFF;
    dst[6] = (val >> 8) & 0xFF;
    dst[7] = val & 0xFF;
}

// Unaligned access macro
#define UNALIGNED_GET(ptr) (*((const typeof(*(ptr)) __attribute__((__packed__)) *)(ptr)))

#endif /* ZEPHYR_SYS_BYTEORDER_H_ */
