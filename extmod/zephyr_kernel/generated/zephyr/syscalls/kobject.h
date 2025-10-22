/*
 * Auto-generated kobject syscalls for MicroPython Zephyr kernel integration
 *
 * This file provides inline wrappers for kobject functions marked with __syscall.
 * In normal Zephyr builds with CONFIG_USERSPACE=0, __syscall expands to
 * "static inline", so these wrappers forward calls to the z_impl_* implementations.
 */

#ifndef ZEPHYR_SYSCALLS_KOBJECT_H
#define ZEPHYR_SYSCALLS_KOBJECT_H

#include <zephyr/kernel.h>

#ifndef _ASMLANGUAGE

#ifdef __cplusplus
extern "C" {
#endif

/* Kernel object syscalls */

extern void z_impl_k_object_access_grant(const void *object,
					 struct k_thread *thread);

static inline void k_object_access_grant(const void *object,
					 struct k_thread *thread)
{
	z_impl_k_object_access_grant(object, thread);
}

extern void z_impl_k_object_release(const void *object);

static inline void k_object_release(const void *object)
{
	z_impl_k_object_release(object);
}

#if defined(CONFIG_DYNAMIC_OBJECTS)
extern void *z_impl_k_object_alloc(enum k_objects otype);

static inline void *k_object_alloc(enum k_objects otype)
{
	return z_impl_k_object_alloc(otype);
}

extern void *z_impl_k_object_alloc_size(enum k_objects otype, size_t size);

static inline void *k_object_alloc_size(enum k_objects otype, size_t size)
{
	return z_impl_k_object_alloc_size(otype, size);
}
#endif /* CONFIG_DYNAMIC_OBJECTS */

#ifdef __cplusplus
}
#endif

#endif /* _ASMLANGUAGE */

#endif /* ZEPHYR_SYSCALLS_KOBJECT_H */
