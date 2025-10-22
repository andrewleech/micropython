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
#include <zephyr/kernel.h>

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

    // Exit the entire process - we can't properly clean up the bootstrap thread
    exit(exit_code);
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

    // The bootstrap thread just sleeps forever
    // The MicroPython thread will call exit() when it's done
    while (1) {
        k_sleep(K_FOREVER);
    }
}

#endif // MICROPY_ZEPHYR_THREADING
