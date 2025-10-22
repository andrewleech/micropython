/*
 * Minimal board_irq.h for MicroPython POSIX integration
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef MICROPYTHON_BOARD_IRQ_H
#define MICROPYTHON_BOARD_IRQ_H

#include <stdint.h>
#include <stdbool.h>

/* Note: sw_isr_table.h is NOT included here to avoid header order issues */
/* ISR flag definition (normally from sw_isr_table.h) */
#ifndef ISR_FLAG_DIRECT
#define ISR_FLAG_DIRECT (1 << 0)
#endif

#ifdef __cplusplus
extern "C" {
#endif

void posix_isr_declare(unsigned int irq_p, int flags, void isr_p(const void *),
                       const void *isr_param_p);
void posix_irq_priority_set(unsigned int irq, unsigned int prio,
                            uint32_t flags);

/**
 * Configure a static interrupt.
 */
#define ARCH_IRQ_CONNECT(irq_p, priority_p, isr_p, isr_param_p, flags_p) \
{ \
    posix_isr_declare(irq_p, 0, isr_p, isr_param_p); \
    posix_irq_priority_set(irq_p, priority_p, flags_p); \
}

/**
 * Configure a 'direct' static interrupt.
 */
#define ARCH_IRQ_DIRECT_CONNECT(irq_p, priority_p, isr_p, flags_p) \
{ \
    posix_isr_declare(irq_p, ISR_FLAG_DIRECT, \
                      (void (*)(const void *))isr_p, NULL); \
    posix_irq_priority_set(irq_p, priority_p, flags_p); \
}

/**
 * POSIX Architecture (board) specific ISR_DIRECT_DECLARE()
 */
#define ARCH_ISR_DIRECT_DECLARE(name) \
    static inline int name##_body(void); \
    int name(void) \
    { \
        int check_reschedule; \
        check_reschedule = name##_body(); \
        return check_reschedule; \
    } \
    static inline int name##_body(void)

#define ARCH_ISR_DIRECT_HEADER()   do { } while (false)
#define ARCH_ISR_DIRECT_FOOTER(a)  do { } while (false)

#ifdef CONFIG_PM
extern void posix_irq_check_idle_exit(void);
#define ARCH_ISR_DIRECT_PM() posix_irq_check_idle_exit()
#else
#define ARCH_ISR_DIRECT_PM() do { } while (false)
#endif

#ifdef __cplusplus
}
#endif

#endif /* MICROPYTHON_BOARD_IRQ_H */
