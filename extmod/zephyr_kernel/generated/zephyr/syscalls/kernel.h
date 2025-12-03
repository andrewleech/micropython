/*
 * Auto-generated kernel syscalls for MicroPython Zephyr kernel integration
 *
 * This file provides inline wrappers for kernel functions marked with __syscall.
 * In normal Zephyr builds with CONFIG_USERSPACE=0, __syscall expands to
 * "static inline", so these wrappers forward calls to the z_impl_* implementations.
 */

#ifndef ZEPHYR_SYSCALLS_KERNEL_H
#define ZEPHYR_SYSCALLS_KERNEL_H

#include <zephyr/kernel_includes.h>

#ifndef _ASMLANGUAGE

#ifdef __cplusplus
extern "C" {
#endif

/* Thread management syscalls */

extern k_tid_t z_impl_k_thread_create(struct k_thread *new_thread,
				      k_thread_stack_t *stack,
				      size_t stack_size, k_thread_entry_t entry,
				      void *p1, void *p2, void *p3,
				      int prio, uint32_t options, k_timeout_t delay);

static inline k_tid_t k_thread_create(struct k_thread *new_thread,
				      k_thread_stack_t *stack,
				      size_t stack_size, k_thread_entry_t entry,
				      void *p1, void *p2, void *p3,
				      int prio, uint32_t options, k_timeout_t delay)
{
	return z_impl_k_thread_create(new_thread, stack, stack_size, entry,
				      p1, p2, p3, prio, options, delay);
}

extern void z_impl_k_thread_abort(k_tid_t thread);

static inline void k_thread_abort(k_tid_t thread)
{
	z_impl_k_thread_abort(thread);
}

extern void z_impl_k_thread_suspend(k_tid_t thread);

static inline void k_thread_suspend(k_tid_t thread)
{
	z_impl_k_thread_suspend(thread);
}

extern void z_impl_k_thread_resume(k_tid_t thread);

static inline void k_thread_resume(k_tid_t thread)
{
	z_impl_k_thread_resume(thread);
}

extern int z_impl_k_thread_priority_get(k_tid_t thread);

static inline int k_thread_priority_get(k_tid_t thread)
{
	return z_impl_k_thread_priority_get(thread);
}

extern void z_impl_k_thread_priority_set(k_tid_t thread, int prio);

static inline void k_thread_priority_set(k_tid_t thread, int prio)
{
	z_impl_k_thread_priority_set(thread, prio);
}

/* Sleep and timing syscalls */

extern int32_t z_impl_k_sleep(k_timeout_t timeout);

static inline int32_t k_sleep(k_timeout_t timeout)
{
	return z_impl_k_sleep(timeout);
}

extern int32_t z_impl_k_usleep(int32_t us);

static inline int32_t k_usleep(int32_t us)
{
	return z_impl_k_usleep(us);
}

extern void z_impl_k_busy_wait(uint32_t usec_to_wait);

static inline void k_busy_wait(uint32_t usec_to_wait)
{
	z_impl_k_busy_wait(usec_to_wait);
}

extern void z_impl_k_yield(void);

static inline void k_yield(void)
{
	z_impl_k_yield();
}

extern void z_impl_k_wakeup(k_tid_t thread);

static inline void k_wakeup(k_tid_t thread)
{
	z_impl_k_wakeup(thread);
}

/* Scheduler syscalls */

extern k_tid_t z_impl_k_sched_current_thread_query(void);

static inline k_tid_t k_sched_current_thread_query(void)
{
	return z_impl_k_sched_current_thread_query();
}

/* Thread stack syscalls (if CONFIG_DYNAMIC_THREAD) */

#if CONFIG_DYNAMIC_THREAD
extern k_thread_stack_t *z_impl_k_thread_stack_alloc(size_t size, int flags);

static inline k_thread_stack_t *k_thread_stack_alloc(size_t size, int flags)
{
	return z_impl_k_thread_stack_alloc(size, flags);
}

extern int z_impl_k_thread_stack_free(k_thread_stack_t *stack);

static inline int k_thread_stack_free(k_thread_stack_t *stack)
{
	return z_impl_k_thread_stack_free(stack);
}
#endif

/* Thread join */

extern int z_impl_k_thread_join(struct k_thread *thread, k_timeout_t timeout);

static inline int k_thread_join(struct k_thread *thread, k_timeout_t timeout)
{
	return z_impl_k_thread_join(thread, timeout);
}

/* Thread deadline scheduling (if enabled) */

#if CONFIG_SCHED_DEADLINE
extern void z_impl_k_thread_deadline_set(k_tid_t thread, int deadline);

static inline void k_thread_deadline_set(k_tid_t thread, int deadline)
{
	z_impl_k_thread_deadline_set(thread, deadline);
}
#endif

/* Rescheduling */

extern void z_impl_k_reschedule(void);

static inline void k_reschedule(void)
{
	z_impl_k_reschedule();
}

/* Thread property syscalls */

extern k_ticks_t z_impl_k_thread_timeout_expires_ticks(const struct k_thread *thread);

static inline k_ticks_t k_thread_timeout_expires_ticks(const struct k_thread *thread)
{
	return z_impl_k_thread_timeout_expires_ticks(thread);
}

extern k_ticks_t z_impl_k_thread_timeout_remaining_ticks(const struct k_thread *thread);

static inline k_ticks_t k_thread_timeout_remaining_ticks(const struct k_thread *thread)
{
	return z_impl_k_thread_timeout_remaining_ticks(thread);
}

extern int z_impl_k_is_preempt_thread(void);

static inline int k_is_preempt_thread(void)
{
	return z_impl_k_is_preempt_thread();
}

extern void z_impl_k_thread_custom_data_set(void *value);

static inline void k_thread_custom_data_set(void *value)
{
	z_impl_k_thread_custom_data_set(value);
}

extern void *z_impl_k_thread_custom_data_get(void);

static inline void *k_thread_custom_data_get(void)
{
	return z_impl_k_thread_custom_data_get();
}

extern int z_impl_k_thread_name_set(k_tid_t thread, const char *str);

static inline int k_thread_name_set(k_tid_t thread, const char *str)
{
	return z_impl_k_thread_name_set(thread, str);
}

extern int z_impl_k_thread_name_copy(k_tid_t thread, char *buf, size_t size);

static inline int k_thread_name_copy(k_tid_t thread, char *buf, size_t size)
{
	return z_impl_k_thread_name_copy(thread, buf, size);
}

/* Timer syscalls */

extern void z_impl_k_timer_start(struct k_timer *timer, k_timeout_t duration,
				 k_timeout_t period);

static inline void k_timer_start(struct k_timer *timer, k_timeout_t duration,
				 k_timeout_t period)
{
	z_impl_k_timer_start(timer, duration, period);
}

extern void z_impl_k_timer_stop(struct k_timer *timer);

static inline void k_timer_stop(struct k_timer *timer)
{
	z_impl_k_timer_stop(timer);
}

extern uint32_t z_impl_k_timer_status_get(struct k_timer *timer);

static inline uint32_t k_timer_status_get(struct k_timer *timer)
{
	return z_impl_k_timer_status_get(timer);
}

extern uint32_t z_impl_k_timer_status_sync(struct k_timer *timer);

static inline uint32_t k_timer_status_sync(struct k_timer *timer)
{
	return z_impl_k_timer_status_sync(timer);
}

extern k_ticks_t z_impl_k_timer_expires_ticks(const struct k_timer *timer);

static inline k_ticks_t k_timer_expires_ticks(const struct k_timer *timer)
{
	return z_impl_k_timer_expires_ticks(timer);
}

extern k_ticks_t z_impl_k_timer_remaining_ticks(const struct k_timer *timer);

static inline k_ticks_t k_timer_remaining_ticks(const struct k_timer *timer)
{
	return z_impl_k_timer_remaining_ticks(timer);
}

extern void z_impl_k_timer_user_data_set(struct k_timer *timer, void *user_data);

static inline void k_timer_user_data_set(struct k_timer *timer, void *user_data)
{
	z_impl_k_timer_user_data_set(timer, user_data);
}

extern void *z_impl_k_timer_user_data_get(const struct k_timer *timer);

static inline void *k_timer_user_data_get(const struct k_timer *timer)
{
	return z_impl_k_timer_user_data_get(timer);
}

/* Uptime syscalls */

extern int64_t z_impl_k_uptime_ticks(void);

static inline int64_t k_uptime_ticks(void)
{
	return z_impl_k_uptime_ticks();
}

/* Queue syscalls */

extern void z_impl_k_queue_init(struct k_queue *queue);

static inline void k_queue_init(struct k_queue *queue)
{
	z_impl_k_queue_init(queue);
}

extern void z_impl_k_queue_cancel_wait(struct k_queue *queue);

static inline void k_queue_cancel_wait(struct k_queue *queue)
{
	z_impl_k_queue_cancel_wait(queue);
}

extern int32_t z_impl_k_queue_alloc_append(struct k_queue *queue, void *data);

static inline int32_t k_queue_alloc_append(struct k_queue *queue, void *data)
{
	return z_impl_k_queue_alloc_append(queue, data);
}

extern int32_t z_impl_k_queue_alloc_prepend(struct k_queue *queue, void *data);

static inline int32_t k_queue_alloc_prepend(struct k_queue *queue, void *data)
{
	return z_impl_k_queue_alloc_prepend(queue, data);
}

extern void *z_impl_k_queue_get(struct k_queue *queue, k_timeout_t timeout);

static inline void *k_queue_get(struct k_queue *queue, k_timeout_t timeout)
{
	return z_impl_k_queue_get(queue, timeout);
}

extern int z_impl_k_queue_is_empty(struct k_queue *queue);

static inline int k_queue_is_empty(struct k_queue *queue)
{
	return z_impl_k_queue_is_empty(queue);
}

extern void *z_impl_k_queue_peek_head(struct k_queue *queue);

static inline void *k_queue_peek_head(struct k_queue *queue)
{
	return z_impl_k_queue_peek_head(queue);
}

extern void *z_impl_k_queue_peek_tail(struct k_queue *queue);

static inline void *k_queue_peek_tail(struct k_queue *queue)
{
	return z_impl_k_queue_peek_tail(queue);
}

/* Futex syscalls */

extern int z_impl_k_futex_wait(struct k_futex *futex, int expected, k_timeout_t timeout);

static inline int k_futex_wait(struct k_futex *futex, int expected, k_timeout_t timeout)
{
	return z_impl_k_futex_wait(futex, expected, timeout);
}

extern int z_impl_k_futex_wake(struct k_futex *futex, bool wake_all);

static inline int k_futex_wake(struct k_futex *futex, bool wake_all)
{
	return z_impl_k_futex_wake(futex, wake_all);
}

/* Event syscalls */

extern void z_impl_k_event_init(struct k_event *event);

static inline void k_event_init(struct k_event *event)
{
	z_impl_k_event_init(event);
}

extern uint32_t z_impl_k_event_post(struct k_event *event, uint32_t events);

static inline uint32_t k_event_post(struct k_event *event, uint32_t events)
{
	return z_impl_k_event_post(event, events);
}

extern uint32_t z_impl_k_event_set(struct k_event *event, uint32_t events);

static inline uint32_t k_event_set(struct k_event *event, uint32_t events)
{
	return z_impl_k_event_set(event, events);
}

extern uint32_t z_impl_k_event_set_masked(struct k_event *event, uint32_t events, uint32_t events_mask);

static inline uint32_t k_event_set_masked(struct k_event *event, uint32_t events, uint32_t events_mask)
{
	return z_impl_k_event_set_masked(event, events, events_mask);
}

extern uint32_t z_impl_k_event_clear(struct k_event *event, uint32_t events);

static inline uint32_t k_event_clear(struct k_event *event, uint32_t events)
{
	return z_impl_k_event_clear(event, events);
}

extern uint32_t z_impl_k_event_wait(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout);

static inline uint32_t k_event_wait(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout)
{
	return z_impl_k_event_wait(event, events, reset, timeout);
}

extern uint32_t z_impl_k_event_wait_all(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout);

static inline uint32_t k_event_wait_all(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout)
{
	return z_impl_k_event_wait_all(event, events, reset, timeout);
}

extern uint32_t z_impl_k_event_wait_safe(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout);

static inline uint32_t k_event_wait_safe(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout)
{
	return z_impl_k_event_wait_safe(event, events, reset, timeout);
}

extern uint32_t z_impl_k_event_wait_all_safe(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout);

static inline uint32_t k_event_wait_all_safe(struct k_event *event, uint32_t events, bool reset, k_timeout_t timeout)
{
	return z_impl_k_event_wait_all_safe(event, events, reset, timeout);
}

/* Stack syscalls */

extern int32_t z_impl_k_stack_alloc_init(struct k_stack *stack, uint32_t num_entries);

static inline int32_t k_stack_alloc_init(struct k_stack *stack, uint32_t num_entries)
{
	return z_impl_k_stack_alloc_init(stack, num_entries);
}

extern int z_impl_k_stack_push(struct k_stack *stack, stack_data_t data);

static inline int k_stack_push(struct k_stack *stack, stack_data_t data)
{
	return z_impl_k_stack_push(stack, data);
}

extern int z_impl_k_stack_pop(struct k_stack *stack, stack_data_t *data, k_timeout_t timeout);

static inline int k_stack_pop(struct k_stack *stack, stack_data_t *data, k_timeout_t timeout)
{
	return z_impl_k_stack_pop(stack, data, timeout);
}

/* Mutex syscalls */

extern int z_impl_k_mutex_init(struct k_mutex *mutex);

static inline int k_mutex_init(struct k_mutex *mutex)
{
	return z_impl_k_mutex_init(mutex);
}

extern int z_impl_k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout);

static inline int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout)
{
	return z_impl_k_mutex_lock(mutex, timeout);
}

extern int z_impl_k_mutex_unlock(struct k_mutex *mutex);

static inline int k_mutex_unlock(struct k_mutex *mutex)
{
	return z_impl_k_mutex_unlock(mutex);
}

/* Condvar syscalls */

extern int z_impl_k_condvar_init(struct k_condvar *condvar);

static inline int k_condvar_init(struct k_condvar *condvar)
{
	return z_impl_k_condvar_init(condvar);
}

extern int z_impl_k_condvar_signal(struct k_condvar *condvar);

static inline int k_condvar_signal(struct k_condvar *condvar)
{
	return z_impl_k_condvar_signal(condvar);
}

extern int z_impl_k_condvar_broadcast(struct k_condvar *condvar);

static inline int k_condvar_broadcast(struct k_condvar *condvar)
{
	return z_impl_k_condvar_broadcast(condvar);
}

extern int z_impl_k_condvar_wait(struct k_condvar *condvar, struct k_mutex *mutex, k_timeout_t timeout);

static inline int k_condvar_wait(struct k_condvar *condvar, struct k_mutex *mutex, k_timeout_t timeout)
{
	return z_impl_k_condvar_wait(condvar, mutex, timeout);
}

/* Semaphore syscalls */

extern int z_impl_k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit);

static inline int k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit)
{
	return z_impl_k_sem_init(sem, initial_count, limit);
}

extern int z_impl_k_sem_take(struct k_sem *sem, k_timeout_t timeout);

static inline int k_sem_take(struct k_sem *sem, k_timeout_t timeout)
{
	return z_impl_k_sem_take(sem, timeout);
}

extern void z_impl_k_sem_give(struct k_sem *sem);

static inline void k_sem_give(struct k_sem *sem)
{
	z_impl_k_sem_give(sem);
}

extern void z_impl_k_sem_reset(struct k_sem *sem);

static inline void k_sem_reset(struct k_sem *sem)
{
	z_impl_k_sem_reset(sem);
}

extern unsigned int z_impl_k_sem_count_get(struct k_sem *sem);

static inline unsigned int k_sem_count_get(struct k_sem *sem)
{
	return z_impl_k_sem_count_get(sem);
}

/* Message queue syscalls */

extern int z_impl_k_msgq_alloc_init(struct k_msgq *msgq, size_t msg_size, uint32_t max_msgs);

static inline int k_msgq_alloc_init(struct k_msgq *msgq, size_t msg_size, uint32_t max_msgs)
{
	return z_impl_k_msgq_alloc_init(msgq, msg_size, max_msgs);
}

extern int z_impl_k_msgq_put(struct k_msgq *msgq, const void *data, k_timeout_t timeout);

static inline int k_msgq_put(struct k_msgq *msgq, const void *data, k_timeout_t timeout)
{
	return z_impl_k_msgq_put(msgq, data, timeout);
}

extern int z_impl_k_msgq_put_front(struct k_msgq *msgq, const void *data);

static inline int k_msgq_put_front(struct k_msgq *msgq, const void *data)
{
	return z_impl_k_msgq_put_front(msgq, data);
}

extern int z_impl_k_msgq_get(struct k_msgq *msgq, void *data, k_timeout_t timeout);

static inline int k_msgq_get(struct k_msgq *msgq, void *data, k_timeout_t timeout)
{
	return z_impl_k_msgq_get(msgq, data, timeout);
}

extern int z_impl_k_msgq_peek(struct k_msgq *msgq, void *data);

static inline int k_msgq_peek(struct k_msgq *msgq, void *data)
{
	return z_impl_k_msgq_peek(msgq, data);
}

extern int z_impl_k_msgq_peek_at(struct k_msgq *msgq, void *data, uint32_t idx);

static inline int k_msgq_peek_at(struct k_msgq *msgq, void *data, uint32_t idx)
{
	return z_impl_k_msgq_peek_at(msgq, data, idx);
}

extern void z_impl_k_msgq_purge(struct k_msgq *msgq);

static inline void k_msgq_purge(struct k_msgq *msgq)
{
	z_impl_k_msgq_purge(msgq);
}

extern uint32_t z_impl_k_msgq_num_free_get(struct k_msgq *msgq);

static inline uint32_t k_msgq_num_free_get(struct k_msgq *msgq)
{
	return z_impl_k_msgq_num_free_get(msgq);
}

extern void z_impl_k_msgq_get_attrs(struct k_msgq *msgq, struct k_msgq_attrs *attrs);

static inline void k_msgq_get_attrs(struct k_msgq *msgq, struct k_msgq_attrs *attrs)
{
	z_impl_k_msgq_get_attrs(msgq, attrs);
}

extern uint32_t z_impl_k_msgq_num_used_get(struct k_msgq *msgq);

static inline uint32_t k_msgq_num_used_get(struct k_msgq *msgq)
{
	return z_impl_k_msgq_num_used_get(msgq);
}

/* Pipe syscalls */

extern void z_impl_k_pipe_init(struct k_pipe *pipe, uint8_t *buffer, size_t buffer_size);

static inline void k_pipe_init(struct k_pipe *pipe, uint8_t *buffer, size_t buffer_size)
{
	z_impl_k_pipe_init(pipe, buffer, buffer_size);
}

extern int z_impl_k_pipe_write(struct k_pipe *pipe, const uint8_t *data, size_t len, k_timeout_t timeout);

static inline int k_pipe_write(struct k_pipe *pipe, const uint8_t *data, size_t len, k_timeout_t timeout)
{
	return z_impl_k_pipe_write(pipe, data, len, timeout);
}

extern int z_impl_k_pipe_read(struct k_pipe *pipe, uint8_t *data, size_t len, k_timeout_t timeout);

static inline int k_pipe_read(struct k_pipe *pipe, uint8_t *data, size_t len, k_timeout_t timeout)
{
	return z_impl_k_pipe_read(pipe, data, len, timeout);
}

extern void z_impl_k_pipe_reset(struct k_pipe *pipe);

static inline void k_pipe_reset(struct k_pipe *pipe)
{
	z_impl_k_pipe_reset(pipe);
}

extern void z_impl_k_pipe_close(struct k_pipe *pipe);

static inline void k_pipe_close(struct k_pipe *pipe)
{
	z_impl_k_pipe_close(pipe);
}

/* Poll syscalls */

extern int z_impl_k_poll(struct k_poll_event *events, int num_events, k_timeout_t timeout);

static inline int k_poll(struct k_poll_event *events, int num_events, k_timeout_t timeout)
{
	return z_impl_k_poll(events, num_events, timeout);
}

extern void z_impl_k_poll_signal_init(struct k_poll_signal *sig);

static inline void k_poll_signal_init(struct k_poll_signal *sig)
{
	z_impl_k_poll_signal_init(sig);
}

extern void z_impl_k_poll_signal_reset(struct k_poll_signal *sig);

static inline void k_poll_signal_reset(struct k_poll_signal *sig)
{
	z_impl_k_poll_signal_reset(sig);
}

extern void z_impl_k_poll_signal_check(struct k_poll_signal *sig, unsigned int *signaled, int *result);

static inline void k_poll_signal_check(struct k_poll_signal *sig, unsigned int *signaled, int *result)
{
	z_impl_k_poll_signal_check(sig, signaled, result);
}

extern int z_impl_k_poll_signal_raise(struct k_poll_signal *sig, int result);

static inline int k_poll_signal_raise(struct k_poll_signal *sig, int result)
{
	return z_impl_k_poll_signal_raise(sig, result);
}

/* Float enable/disable syscalls */

extern int z_impl_k_float_disable(struct k_thread *thread);

static inline int k_float_disable(struct k_thread *thread)
{
	return z_impl_k_float_disable(thread);
}

extern int z_impl_k_float_enable(struct k_thread *thread, unsigned int options);

static inline int k_float_enable(struct k_thread *thread, unsigned int options)
{
	return z_impl_k_float_enable(thread, options);
}

#ifdef __cplusplus
}
#endif

#endif /* _ASMLANGUAGE */

#endif /* ZEPHYR_SYSCALLS_KERNEL_H */
