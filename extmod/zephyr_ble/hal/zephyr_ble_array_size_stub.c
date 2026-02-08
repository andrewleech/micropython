/*
 * ARRAY_SIZE Stub
 * Workaround for link-time ARRAY_SIZE resolution issues
 *
 * This provides a fallback function when ARRAY_SIZE macro doesn't expand properly.
 * Note: This should NOT be called at runtime - it's only here to satisfy the linker.
 */

#include <stddef.h>

// Dummy ARRAY_SIZE function
// The compiler should use the ARRAY_SIZE macro instead, but if it generates
// a function call, this stub will satisfy the linker.
// Parameters are unnamed to avoid warnings about unused parameters.
size_t ARRAY_SIZE(void *array, ...) {
    (void)array;
    // This should never actually be called
    // Return a safe value that might prevent crashes if it is called
    return 0;
}
