/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Startup (z_cstart) for MicroPython
 *
 * This file implements Zephyr's z_cstart() pattern adapted for MicroPython.
 * It follows the initialization sequence from lib/zephyr/kernel/init.c:538-610
 * but tailored for bare-metal MicroPython without full Zephyr infrastructure.
 */

#include <string.h>
#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_ZEPHYR_THREADING

#include "extmod/zephyr_kernel/zephyr_kernel.h"
#include <zephyr/kernel.h>
#include "../../lib/zephyr/kernel/include/kswap.h"  // z_swap_unlocked() and internal swap functions

// External declarations from Zephyr kernel
extern struct z_kernel _kernel;
extern void z_sched_init(void);
extern void z_dummy_thread_init(struct k_thread *dummy_thread);
extern char *z_setup_new_thread(struct k_thread *new_thread,
                                 k_thread_stack_t *stack, size_t stack_size,
                                 k_thread_entry_t entry,
                                 void *p1, void *p2, void *p3,
                                 int prio, uint32_t options, const char *name);
extern void z_mark_thread_as_not_sleeping(struct k_thread *thread);
extern void z_ready_thread(struct k_thread *thread);
// z_swap_unlocked() is static inline in kswap.h, included via kernel_includes.h

// Architecture-specific function for initial switch to main thread
#ifdef CONFIG_ARCH_HAS_CUSTOM_SWAP_TO_MAIN
extern void arch_switch_to_main_thread(struct k_thread *main_thread,
                                        char *stack_ptr,
                                        k_thread_entry_t entry);
#endif

// External architecture init function
extern void mp_zephyr_arch_init(void);

// MicroPython main thread entry point (defined in port's main.c)
extern void micropython_main_thread_entry(void *p1, void *p2, void *p3);

// Dummy thread for boot context (like Zephyr's _thread_dummy)
static struct k_thread dummy_thread;

// Main thread structure and stack (defined here since we don't compile Zephyr's init.c)
struct k_thread z_main_thread;
K_THREAD_STACK_DEFINE(z_main_stack, CONFIG_MAIN_STACK_SIZE);

// Stack definitions moved to zephyr_arch_stm32.c to avoid extern type mismatch issues

// TODO: z_init_cpu() initializes idle thread, IRQ stack, and CPU struct fields.
// Not required for minimal threading: MicroPython main thread never idles (always
// running REPL), IRQ stack already set up by CMSIS/HAL, single-CPU only.
// Would be needed for: power management (idle thread sleep), SMP, per-CPU IRQ stacks.

/**
 * prepare_multithreading - Initialize main thread and ready queue
 *
 * This follows Zephyr's prepare_multithreading() from kernel/init.c:442-473
 * Returns stack pointer for main thread (used by arch-specific switch)
 */
static char *prepare_multithreading(void) {
    char *stack_ptr;

    // Initialize the scheduler and ready queue
    z_sched_init();

    #ifndef CONFIG_SMP
    // Prime the ready queue cache with main thread
    // This is CRITICAL - cache must never be NULL
    _kernel.ready_q.cache = &z_main_thread;
    #endif

    // Setup main thread using z_setup_new_thread()
    // This initializes thread context, stack, priority, and options
    stack_ptr = z_setup_new_thread(&z_main_thread,
                                   (k_thread_stack_t *)z_main_stack,
                                   sizeof(z_main_stack),
                                   micropython_main_thread_entry,  // Entry function
                                   NULL, NULL, NULL,  // Arguments (unused)
                                   CONFIG_MAIN_THREAD_PRIORITY,  // Priority 0 (cooperative)
                                   K_ESSENTIAL,  // Essential thread
                                   "main");

    // Mark main thread as ready to run
    z_mark_thread_as_not_sleeping(&z_main_thread);
    z_ready_thread(&z_main_thread);

    // mp_zephyr_init_cpu(0) - CPU initialization following Zephyr gold standard
    //
    // DISABLED: Not required when using k_yield() in MICROPY_EVENT_POLL_HOOK.
    // k_yield() cooperatively yields CPU without sleep, so no idle thread needed.
    //
    // When using k_msleep(1), idle thread IS required:
    // - Main thread sleeps, scheduler needs idle thread to run
    // - Without idle thread: scheduler has nothing to run → HardFault
    //
    // Current approach uses k_yield() to avoid idle thread complexity while
    // still allowing GIL exit/enter for thread switching.
    //
    // TODO: Debug idle thread initialization issues if k_msleep(1) is needed:
    // - Board resets continuously with idle thread enabled
    // - PC stuck at Reset_Handler (0x080201a0)
    // - May be stack overflow or interrupt configuration conflict
    //
    // extern void mp_zephyr_init_cpu(int id);
    // mp_zephyr_init_cpu(0);

    return stack_ptr;
}

/**
 * z_cstart - Zephyr kernel initialization and startup
 *
 * This is the C entry point after CMSIS assembly startup (Reset_Handler).
 * Implements a minimal subset of Zephyr's z_cstart() pattern (inspired by
 * kernel/init.c:538-610) adapted for bare-metal MicroPython. Unlike full
 * Zephyr, this omits device initialization, PRE_KERNEL hooks, and other
 * infrastructure not needed for threading-only operation.
 *
 * Flow:
 * 1. Initialize architecture (SysTick, PendSV, etc.)
 * 2. Initialize dummy thread for boot context
 * 3. Create and setup z_main_thread via prepare_multithreading()
 * 4. Context switch TO z_main_thread (never returns)
 *
 * After this function, execution continues in micropython_main_thread_entry()
 * running in z_main_thread context, where MicroPython initialization happens.
 */
FUNC_NORETURN void z_cstart(void) {
    // Initialize architecture-specific components
    // This sets up SysTick, PendSV, etc. but does NOT enable interrupts yet
    mp_zephyr_arch_init();

    // Zero out the kernel structure (defensive programming - _kernel is in BSS
    // which startup.c already zeroed, but this ensures clean state)
    memset(&_kernel, 0, sizeof(_kernel));

    #if defined(CONFIG_MULTITHREADING)
    // Initialize dummy thread for boot context
    // This represents the "current thread" during kernel initialization
    // It will NEVER be scheduled again after we switch to z_main_thread
    z_dummy_thread_init(&dummy_thread);

    // Set dummy thread as current (we're running in it now)
    _kernel.cpus[0].current = &dummy_thread;
    #endif

    // Create z_main_thread and initialize scheduler/ready queue
    char *stack_ptr = prepare_multithreading();

    // Switch from boot/dummy context to z_main_thread using arch_switch_to_main_thread()
    // This is the correct Zephyr approach for the initial boot→main transition.
    // Unlike z_swap_unlocked() (which is designed for context switching between
    // already-running threads via the scheduler), arch_switch_to_main_thread()
    // handles the one-time initialization switch from the dummy boot context
    // to the newly created main thread without involving the scheduler.
    //
    // NOTE: After this call, we're running in z_main_thread context.
    //       The dummy thread is never scheduled again and its stack is abandoned.

    #ifdef CONFIG_MULTITHREADING
    // Use arch_switch_to_main_thread() for the initial boot→main transition
    // This is the proper Zephyr way for the first context switch
    // It sets up stack, enables interrupts, and calls z_thread_entry() which
    // in turn calls our micropython_main_thread_entry()
    arch_switch_to_main_thread(&z_main_thread, stack_ptr, micropython_main_thread_entry);

    // Should never return
    CODE_UNREACHABLE;
    #else
    // No multithreading - just call main entry directly
    micropython_main_thread_entry(NULL, NULL, NULL);
    #endif

    // Should never reach here
    CODE_UNREACHABLE;
}

#endif // MICROPY_ZEPHYR_THREADING
