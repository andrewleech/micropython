/*
 * Minimal posix_board_if.h for MicroPython POSIX integration
 *
 * This provides the board interface functions that the POSIX architecture expects.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef MICROPYTHON_POSIX_BOARD_IF_H
#define MICROPYTHON_POSIX_BOARD_IF_H

#include <stdint.h>
#include <stdarg.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Board initialization and cleanup */
void posix_arch_init(void);
void posix_arch_clean_up(void);

/* Thread context switching */
void posix_swap(int next_allowed_thread_nbr, int this_th_nbr);
void posix_main_thread_start(int next_allowed_thread_nbr);

/* Get hardware cycle/time for timing */
uint64_t posix_get_hw_cycle(void);

/* Exit and printing functions */
void posix_exit(int exit_code);
void posix_print_error_and_exit(const char *format, ...);
void posix_print_warning(const char *format, ...);
void posix_print_trace(const char *format, ...);
void posix_vprint_error_and_exit(const char *format, va_list vargs);
void posix_vprint_warning(const char *format, va_list vargs);
void posix_vprint_trace(const char *format, va_list vargs);

/* Trace output control */
int posix_trace_over_tty(int file_number);

#ifdef __cplusplus
}
#endif

#endif /* MICROPYTHON_POSIX_BOARD_IF_H */
