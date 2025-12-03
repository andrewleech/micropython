/*
 * Auto-generated clock syscalls for MicroPython Zephyr kernel integration
 *
 * This file provides inline wrappers for clock functions marked with __syscall.
 * In normal Zephyr builds with CONFIG_USERSPACE=0, __syscall expands to
 * "static inline", so these wrappers forward calls to the z_impl_* implementations.
 */

#ifndef ZEPHYR_SYSCALLS_CLOCK_H
#define ZEPHYR_SYSCALLS_CLOCK_H

#include <time.h>

#ifndef _ASMLANGUAGE

#ifdef __cplusplus
extern "C" {
#endif

/* Clock syscalls */

extern void z_impl_sys_clock_getrtoffset(struct timespec *tp);

static inline void sys_clock_getrtoffset(struct timespec *tp)
{
	z_impl_sys_clock_getrtoffset(tp);
}

extern int z_impl_sys_clock_settime(int clock_id, const struct timespec *tp);

static inline int sys_clock_settime(int clock_id, const struct timespec *tp)
{
	return z_impl_sys_clock_settime(clock_id, tp);
}

extern int z_impl_sys_clock_nanosleep(int clock_id, int flags, const struct timespec *rqtp,
				      struct timespec *rmtp);

static inline int sys_clock_nanosleep(int clock_id, int flags, const struct timespec *rqtp,
				      struct timespec *rmtp)
{
	return z_impl_sys_clock_nanosleep(clock_id, flags, rqtp, rmtp);
}

#ifdef __cplusplus
}
#endif

#endif /* _ASMLANGUAGE */

#endif /* ZEPHYR_SYSCALLS_CLOCK_H */
