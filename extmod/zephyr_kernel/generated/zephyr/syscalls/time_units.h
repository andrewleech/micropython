/*
 * Auto-generated time_units syscalls stub for MicroPython Zephyr kernel integration
 *
 * This file is only needed when CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=1.
 * Since we set it to 0, we provide a minimal stub.
 */

#ifndef ZEPHYR_SYSCALLS_TIME_UNITS_H
#define ZEPHYR_SYSCALLS_TIME_UNITS_H

#ifndef _ASMLANGUAGE

#ifdef __cplusplus
extern "C" {
#endif

/* Runtime frequency syscall (not used with CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=0) */

extern unsigned int z_impl_sys_clock_hw_cycles_per_sec_runtime_get(void);

static inline unsigned int sys_clock_hw_cycles_per_sec_runtime_get(void)
{
	return z_impl_sys_clock_hw_cycles_per_sec_runtime_get();
}

#ifdef __cplusplus
}
#endif

#endif /* _ASMLANGUAGE */

#endif /* ZEPHYR_SYSCALLS_TIME_UNITS_H */
