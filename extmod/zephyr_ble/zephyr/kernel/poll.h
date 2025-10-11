/*
 * Zephyr kernel/poll.h wrapper for MicroPython
 * Polling API stubs (not used in MicroPython)
 */

#ifndef ZEPHYR_KERNEL_POLL_H_
#define ZEPHYR_KERNEL_POLL_H_

#include <stdint.h>
#include <stdbool.h>

// Poll signal structure (used for connection change notifications)
struct k_poll_signal {
    int signaled;
    int result;
};

// Poll event structure
struct k_poll_event {
    int dummy;
};

// Poll signal static initializer macro
#define K_POLL_SIGNAL_INITIALIZER(name) { \
    .signaled = 0, \
    .result = 0 \
}

// Poll signal initialization
static inline void k_poll_signal_init(struct k_poll_signal *sig) {
    sig->signaled = 0;
    sig->result = 0;
}

// Raise a poll signal
static inline void k_poll_signal_raise(struct k_poll_signal *sig, int result) {
    sig->signaled = 1;
    sig->result = result;
}

// Check poll signal
static inline void k_poll_signal_check(struct k_poll_signal *sig, unsigned int *signaled, int *result) {
    if (signaled) {
        *signaled = sig->signaled;
    }
    if (result) {
        *result = sig->result;
    }
}

// Reset poll signal
static inline void k_poll_signal_reset(struct k_poll_signal *sig) {
    sig->signaled = 0;
    sig->result = 0;
}

// Poll function (no-op)
static inline int k_poll(struct k_poll_event *events, int num_events, int timeout) {
    (void)events;
    (void)num_events;
    (void)timeout;
    return -1;  // Timeout
}

#endif /* ZEPHYR_KERNEL_POLL_H_ */
