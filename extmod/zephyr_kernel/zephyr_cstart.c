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

// ============================================================================
// Optional Idle Thread Infrastructure (disabled by default)
// ============================================================================
//
// This infrastructure enables k_msleep() support in MICROPY_EVENT_POLL_HOOK.
// Currently disabled because threading works fine with k_yield() approach.
//
// To enable: #define MICROPY_ZEPHYR_USE_IDLE_THREAD 1 in mpconfigport.h
//
// Requirements when enabled:
// - Add idle.c to build (zephyr_kernel.mk)
// - Uncomment z_init_cpu() call in prepare_multithreading()
// - Debug continuous reset issue (PC stuck at Reset_Handler)
//
#if defined(MICROPY_ZEPHYR_USE_IDLE_THREAD) && MICROPY_ZEPHYR_USE_IDLE_THREAD

// Idle thread infrastructure (required for k_msleep in EVENT_POLL_HOOK)
// Idle thread runs when no other thread is ready (e.g., all threads sleeping)
struct k_thread z_idle_threads[1];
K_KERNEL_STACK_ARRAY_DEFINE(z_idle_stacks, 1, CONFIG_IDLE_STACK_SIZE);

// Interrupt stack array (required for z_init_cpu irq_stack initialization)
K_KERNEL_STACK_ARRAY_DEFINE(z_interrupt_stacks, 1, CONFIG_ISR_STACK_SIZE);

// Forward declaration for idle thread entry point (defined in lib/zephyr/kernel/idle.c)
extern void idle(void *unused1, void *unused2, void *unused3);

/**
 * init_idle_thread - Initialize idle thread for CPU
 * @param i: CPU id (always 0 for single-CPU)
 *
 * Sets up the idle thread which runs when no other threads are ready.
 * Essential for k_msleep() - when main thread sleeps, scheduler needs
 * idle thread to execute.
 */
static void init_idle_thread(int i) {
    struct k_thread *thread = &z_idle_threads[i];
    k_thread_stack_t *stack = z_idle_stacks[i];
    size_t stack_size = K_KERNEL_STACK_SIZEOF(z_idle_stacks[i]);

    #ifdef CONFIG_THREAD_NAME
    char *tname = "idle";
    #else
    char *tname = NULL;
    #endif

    // Setup idle thread using z_setup_new_thread()
    // Priority K_IDLE_PRIO (lowest) ensures it only runs when nothing else ready
    z_setup_new_thread(thread, stack, stack_size,
                      idle, &_kernel.cpus[i], NULL, NULL,
                      K_IDLE_PRIO, K_ESSENTIAL, tname);

    // Mark as not sleeping (ready to run)
    z_mark_thread_as_not_sleeping(thread);
}

/**
 * z_init_cpu - Initialize CPU-specific kernel structures
 * @param id: CPU id (always 0 for single-CPU)
 *
 * Following Zephyr's z_init_cpu() pattern from lib/zephyr/kernel/init.c:393-413
 * Initializes:
 * - Idle thread (runs when no other thread ready)
 * - CPU struct fields (idle_thread, id, irq_stack)
 *
 * Required for k_msleep() in MICROPY_EVENT_POLL_HOOK - when main thread
 * calls k_msleep(1), scheduler switches to idle thread until timeout.
 */
void z_init_cpu(int id) {
    // Initialize idle thread
    init_idle_thread(id);

    // Set CPU struct fields
    _kernel.cpus[id].idle_thread = &z_idle_threads[id];
    _kernel.cpus[id].id = id;

    // Set IRQ stack pointer to end of stack (ARM stacks grow downward)
    _kernel.cpus[id].irq_stack = (K_KERNEL_STACK_BUFFER(z_interrupt_stacks[id]) +
                                   K_KERNEL_STACK_SIZEOF(z_interrupt_stacks[id]));
}

#endif // MICROPY_ZEPHYR_USE_IDLE_THREAD

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

    // Initialize CPU (idle thread, IRQ stack, CPU struct) if enabled
    // Required for k_msleep() support - disabled by default, use k_yield() instead
    #if defined(MICROPY_ZEPHYR_USE_IDLE_THREAD) && MICROPY_ZEPHYR_USE_IDLE_THREAD
    z_init_cpu(0);
    #endif

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
