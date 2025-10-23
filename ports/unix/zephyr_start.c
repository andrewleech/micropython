/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Thread Bootstrap for Unix Port
 *
 * This file provides the glue to run MicroPython as a Zephyr thread,
 * following the pattern used in the Zephyr port.
 */

#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <zephyr/kernel.h>
#include "posix_core.h"

// Forward declaration of POSIX board helper
extern pthread_t posix_get_pthread_handle(int thread_idx);

#if MICROPY_ZEPHYR_THREADING

extern int real_main(int argc, char **argv);

// Structure to pass argc/argv to thread
typedef struct {
    int argc;
    char **argv;
} main_args_t;

// Allocate stack for MicroPython main thread at file scope
// K_THREAD_STACK_DEFINE requires static storage with section attributes
K_THREAD_STACK_DEFINE(mp_main_stack, CONFIG_MAIN_STACK_SIZE);
static struct k_thread mp_main_thread;
static main_args_t main_args;
static int exit_code = 0;

// Thread entry point for MicroPython - runs real_main()
static void micropython_thread_main(void *p1, void *p2, void *p3) {
    fprintf(stderr, "[3] thread started\n");
    (void)p2;
    (void)p3;

    main_args_t *args = (main_args_t *)p1;
    fprintf(stderr, "[4] calling real_main\n");

    // Call the real MicroPython main function
    // This will initialize threading from within the Zephyr thread
    exit_code = real_main(args->argc, args->argv);

    // Don't call exit() - let the thread return naturally
    // exit(exit_code);
    fprintf(stderr, "[micropython_thread_main] real_main returned with code %d\n", exit_code);
}

// Start MicroPython as a Zephyr thread
// This function never returns - it creates the thread and waits forever
void mp_zephyr_start(int argc, char **argv) {
    fprintf(stderr, "[1] mp_zephyr_start\n");
    // Store arguments
    main_args.argc = argc;
    main_args.argv = argv;

    fprintf(stderr, "[2] calling k_thread_create\n");
    // Create MicroPython thread with main thread priority
    k_tid_t thread_id = k_thread_create(
        &mp_main_thread,
        mp_main_stack,
        K_THREAD_STACK_SIZEOF(mp_main_stack),
        micropython_thread_main,
        &main_args, NULL, NULL,
        CONFIG_MAIN_THREAD_PRIORITY,
        0,  // No options
        K_NO_WAIT  // Start immediately
        );

    if (thread_id == NULL) {
        fprintf(stderr, "Failed to create MicroPython main thread\n");
        exit(1);
    }

    k_thread_name_set(thread_id, "mp_main");

    // Wait for the MicroPython thread to complete using pthread_join
    // The thread index is stored in the thread's arch-specific data
    posix_thread_status_t *thread_status = (posix_thread_status_t *)mp_main_thread.callee_saved.thread_status;
    if (thread_status != NULL) {
        int thread_idx = thread_status->thread_idx;
        pthread_t pthread_handle = posix_get_pthread_handle(thread_idx);

        fprintf(stderr, "[bootstrap] Waiting for MicroPython thread %d (pthread=%p) to complete\n",
            thread_idx, (void *)pthread_handle);

        // Wait for the thread to finish
        pthread_join(pthread_handle, NULL);

        fprintf(stderr, "[bootstrap] MicroPython thread completed\n");
    }

    // Exit with the exit code from real_main
    exit(exit_code);
}

#endif // MICROPY_ZEPHYR_THREADING
