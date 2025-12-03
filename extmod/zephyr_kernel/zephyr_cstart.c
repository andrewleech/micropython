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
#include <stdio.h>
#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_ZEPHYR_THREADING

#include "extmod/zephyr_kernel/zephyr_kernel.h"
#include <zephyr/kernel.h>
#include "../../lib/zephyr/kernel/include/kswap.h"  // z_swap_unlocked() and internal swap functions
#include "../../lib/zephyr/kernel/include/wait_q.h"  // z_waitq_init() - static inline function

// External declarations from Zephyr kernel
extern struct z_kernel _kernel;
extern void z_sched_init(void);
extern void z_init_thread_base(struct _thread_base *thread_base, int priority,
                               uint32_t initial_state, unsigned int options);

// These are still needed for spawned threads (not main thread)
extern char *z_setup_new_thread(struct k_thread *new_thread,
                                 k_thread_stack_t *stack, size_t stack_size,
                                 k_thread_entry_t entry,
                                 void *p1, void *p2, void *p3,
                                 int prio, uint32_t options, const char *name);
extern void z_mark_thread_as_not_sleeping(struct k_thread *thread);
extern void z_ready_thread(struct k_thread *thread);

// External architecture init function
extern void mp_zephyr_arch_init(void);

// MicroPython main thread entry point (defined in port's main.c)
extern void micropython_main_thread_entry(void *p1, void *p2, void *p3);

// Main thread structure and stack (defined here since we don't compile Zephyr's init.c)
struct k_thread z_main_thread;
K_THREAD_STACK_DEFINE(z_main_stack, CONFIG_MAIN_STACK_SIZE);

// PSP stack top for assembly startup code (zephyr_psp_init in zephyr_psp_switch.S)
// Points to top of z_main_stack where PSP should be initialized.
// This is in .data section so it's initialized from flash BEFORE BSS zeroing,
// which happens before zephyr_psp_init is called from Reset_Handler.
//
// NOTE: We use sizeof(z_main_stack) here (full array size) rather than
// K_THREAD_STACK_SIZEOF() (usable size minus reserved area). This is correct
// because PSP should point to the actual top of physical memory. The reserved
// area at the bottom (if any) is for stack guards/canaries, not usable stack.
// K_THREAD_STACK_BUFFER() and K_THREAD_STACK_SIZEOF() are used in stack_info
// to describe the usable region for GC scanning.
__attribute__((used))
char *const zephyr_psp_stack_top = (char *)z_main_stack + sizeof(z_main_stack);

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
// TODO: Zephyr's idle.c supports automatic low power via CONFIG_PM. When enabled,
// it calls pm_system_suspend() to enter low power states when no threads are ready.
// For MicroPython-specific power management, consider either:
// 1. Enabling CONFIG_PM and implementing pm_system_suspend() for the target
// 2. Providing a custom idle() function that integrates with MicroPython's
//    machine.lightsleep()/deepsleep() infrastructure
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
 * prepare_multithreading - Initialize main thread for direct registration
 *
 * DIRECT REGISTRATION APPROACH:
 * Unlike Zephyr's prepare_multithreading() which creates a new thread context
 * and switches to it via arch_switch_to_main_thread(), we register the CURRENT
 * execution context (already running on z_main_stack via PSP) as the main thread.
 *
 * This avoids the arch_switch_to_main_thread() call which would reset PSP and
 * wipe any stack frames built up since the early PSP switch in Reset_Handler.
 *
 * Prerequisites:
 * - Reset_Handler must have already switched to PSP pointing to z_main_stack
 * - We are currently executing on z_main_stack (via PSP)
 *
 * NOTE on mode_exc_return (z_main_thread.arch.mode_exc_return):
 * We don't explicitly initialize this field. It's populated automatically on the
 * first context switch AWAY from main thread - PendSV saves the current LR (which
 * contains EXC_RETURN) into the thread structure. When switching back to main
 * thread, the saved value is restored. The initial BSS-zero value is never used.
 */
static void prepare_multithreading(void) {
    // Clear FPU state for clean thread context (matches z_arm_prepare_switch_to_main)
    // If CONFIG_FPU_SHARING is enabled, this ensures no stale FPU context from
    // startup code affects thread scheduling decisions.
    #if defined(CONFIG_FPU) && defined(CONFIG_FPU_SHARING)
    __set_FPSCR(0);
    __set_CONTROL(__get_CONTROL() & (~CONTROL_FPCA_Msk));
    __ISB();
    #endif

    // Initialize the scheduler and ready queue
    z_sched_init();

    // Initialize z_main_thread base - state 0 means "runnable" (not sleeping/pending)
    z_init_thread_base(&z_main_thread.base, CONFIG_MAIN_THREAD_PRIORITY, 0, K_ESSENTIAL);

    // Initialize join queue
    z_waitq_init(&z_main_thread.join_queue);

    #ifdef CONFIG_THREAD_STACK_INFO
    // Set stack info to z_main_stack - this is the stack we're ALREADY running on
    z_main_thread.stack_info.start = (uintptr_t)K_THREAD_STACK_BUFFER(z_main_stack);
    z_main_thread.stack_info.size = K_THREAD_STACK_SIZEOF(z_main_stack);
    z_main_thread.stack_info.delta = 0;
    #endif

    // Initialize callee_saved.psp - CRITICAL for PendSV context switch
    // When PendSV runs, it loads the next thread's PSP from callee_saved.psp.
    // If this is uninitialized (0), the context switch will fail.
    uint32_t current_psp;
    __asm__ volatile("mrs %0, PSP" : "=r"(current_psp));
    z_main_thread.callee_saved.psp = current_psp;

    // Initialize arch fields
    z_main_thread.arch.basepri = 0;  // 0 = interrupts enabled
    #ifdef CONFIG_ARM_STORE_EXC_RETURN
    // EXC_RETURN for thread mode, PSP, no FP context (0xFFFFFFFD)
    z_main_thread.arch.mode_exc_return = 0xFD;
    #endif

    // Debug: verify initialization (disabled for testing)
    // printf("DBG: z_main_thread.callee_saved.psp = 0x%08x\n", z_main_thread.callee_saved.psp);
    // printf("DBG: z_main_thread.arch.mode_exc_return = 0x%02x\n", z_main_thread.arch.mode_exc_return);

    #ifdef CONFIG_THREAD_NAME
    strncpy(z_main_thread.name, "main", sizeof(z_main_thread.name) - 1);
    z_main_thread.name[sizeof(z_main_thread.name) - 1] = '\0';
    #endif

    // Set main thread as current - we ARE this thread already
    _kernel.cpus[0].current = &z_main_thread;

    #ifndef CONFIG_SMP
    // Prime the ready queue cache with main thread
    // This is CRITICAL - cache must never be NULL
    _kernel.ready_q.cache = &z_main_thread;
    #endif

    // CRITICAL: Add main thread to the run queue via z_ready_thread().
    // This matches Zephyr's init.c:468 behavior. Without this, when another
    // thread blocks (e.g., on GIL), update_cache() calls next_up() which
    // returns runq_best(), and if run queue is empty, returns idle_thread.
    // Since we have no idle thread, this would return NULL, causing hang.
    z_mark_thread_as_not_sleeping(&z_main_thread);
    z_ready_thread(&z_main_thread);

    // Initialize timeslice for main thread
    // Without this, the main thread runs forever without ever triggering
    // slice_timeout(), so equal-priority threads never get scheduled via timeslicing.
    #ifdef CONFIG_TIMESLICING
    extern void z_reset_time_slice(struct k_thread *thread);
    z_reset_time_slice(&z_main_thread);
    #endif

    // Initialize CPU (idle thread, IRQ stack, CPU struct) if enabled
    // Required for k_msleep() support - disabled by default, use k_yield() instead
    #if defined(MICROPY_ZEPHYR_USE_IDLE_THREAD) && MICROPY_ZEPHYR_USE_IDLE_THREAD
    z_init_cpu(0);
    #endif
}

/**
 * z_cstart - Zephyr kernel initialization and startup
 *
 * This is the C entry point after CMSIS assembly startup (Reset_Handler).
 * Implements a minimal subset of Zephyr's z_cstart() pattern adapted for
 * bare-metal MicroPython with DIRECT REGISTRATION of the main thread.
 *
 * DIRECT REGISTRATION APPROACH:
 * Unlike standard Zephyr which creates a fresh stack and switches to it via
 * arch_switch_to_main_thread(), we register the CURRENT execution context
 * (already running on z_main_stack via PSP) as the main thread. This avoids
 * the stack switch that would wipe our existing stack frames.
 *
 * Prerequisites (handled by Reset_Handler with zephyr_psp_init):
 * - PSP points to z_main_stack (set by zephyr_psp_init)
 * - CONTROL.SPSEL = 1 (thread mode uses PSP)
 * - MSP reserved for exception handlers
 *
 * Flow:
 * 1. Initialize architecture (SysTick, PendSV, etc.)
 * 2. Zero kernel structure
 * 3. Direct registration of current context as z_main_thread
 * 4. Call micropython_main_thread_entry() directly (no context switch needed)
 *
 * After this, execution continues in micropython_main_thread_entry()
 * already running in z_main_thread context on z_main_stack.
 */
FUNC_NORETURN void z_cstart(void) {
    // Initialize architecture-specific components
    // This sets up SysTick, PendSV, etc. but does NOT enable interrupts yet
    mp_zephyr_arch_init();

    // Zero out the kernel structure (defensive programming - _kernel is in BSS
    // which startup.c already zeroed, but this ensures clean state)
    memset(&_kernel, 0, sizeof(_kernel));

    #if defined(CONFIG_MULTITHREADING)
    // DIRECT REGISTRATION: Register current context as z_main_thread.
    // We're already running on z_main_stack (PSP), so we just need to
    // initialize the thread structure and set it as current.
    // No dummy thread or arch_switch_to_main_thread() needed.
    prepare_multithreading();

    // Call main entry directly - we're already in main thread context
    micropython_main_thread_entry(NULL, NULL, NULL);
    #else
    // No multithreading - just call main entry directly
    micropython_main_thread_entry(NULL, NULL, NULL);
    #endif

    // Should never reach here
    CODE_UNREACHABLE;
}

#endif // MICROPY_ZEPHYR_THREADING
