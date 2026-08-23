/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2015 Damien P. George
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
#ifndef MICROPY_INCLUDED_PY_MPHAL_H
#define MICROPY_INCLUDED_PY_MPHAL_H

#include <stdint.h>
#include <stddef.h>
#include "py/mpconfig.h"

#ifdef MICROPY_MPHALPORT_H
#include MICROPY_MPHALPORT_H
#else
#include <mphalport.h>
#endif

// On embedded platforms, these will typically enable/disable irqs.
#ifndef MICROPY_BEGIN_ATOMIC_SECTION
#define MICROPY_BEGIN_ATOMIC_SECTION() (0)
#endif
#ifndef MICROPY_END_ATOMIC_SECTION
#define MICROPY_END_ATOMIC_SECTION(state) (void)(state)
#endif

#ifndef mp_hal_stdio_poll
uintptr_t mp_hal_stdio_poll(uintptr_t poll_flags);
#endif

#ifndef mp_hal_stdin_rx_chr
int mp_hal_stdin_rx_chr(void);
#endif

#ifndef mp_hal_stdout_tx_str
void mp_hal_stdout_tx_str(const char *str);
#endif

#ifndef mp_hal_stdout_tx_strn
mp_uint_t mp_hal_stdout_tx_strn(const char *str, size_t len);
#endif

#ifndef mp_hal_stdout_tx_strn_cooked
void mp_hal_stdout_tx_strn_cooked(const char *str, size_t len);
#endif

#ifndef mp_hal_delay_ms
void mp_hal_delay_ms(mp_uint_t ms);
#endif

#ifndef mp_hal_delay_us
void mp_hal_delay_us(mp_uint_t us);
#endif

#ifndef mp_hal_ticks_ms
mp_uint_t mp_hal_ticks_ms(void);
#endif

#ifndef mp_hal_ticks_us
mp_uint_t mp_hal_ticks_us(void);
#endif

#ifndef mp_hal_ticks_cpu
mp_uint_t mp_hal_ticks_cpu(void);
#endif

#ifndef mp_hal_time_ns
// Nanoseconds since the Epoch.
uint64_t mp_hal_time_ns(void);
#endif

// If port HAL didn't define its own pin API, use generic
// "virtual pin" API from the core.
#ifndef mp_hal_pin_obj_t
#define mp_hal_pin_obj_t mp_obj_t
#define mp_hal_get_pin_obj(pin) (pin)
#define mp_hal_pin_read(pin) mp_virtual_pin_read(pin)
#define mp_hal_pin_write(pin, v) mp_virtual_pin_write(pin, v)
#include "extmod/virtpin.h"
#endif

// Event handling and wait-for-event functions.

#ifndef MICROPY_INTERNAL_WFE
// Fallback definition for ports that don't need to suspend the CPU.
#define MICROPY_INTERNAL_WFE(TIMEOUT_MS) (void)0
#endif

#ifndef MICROPY_INTERNAL_EVENT_HOOK
// Fallback definition for ports that don't need any port-specific
// non-blocking event processing.
#define MICROPY_INTERNAL_EVENT_HOOK (void)0
#endif

// Ends a blocking MICROPY_INTERNAL_WFE(), so the waiter re-evaluates its wait
// conditions.  May be called from a signal handler or interrupt, and not
// necessarily from within an atomic section.
#ifndef mp_hal_signal_event
// Fallback definition for ports whose wait cannot be ended early.
#define mp_hal_signal_event() (void)0
#endif

// Per-thread wake objects. A polling thread that has claimed one waits on that object
// instead of on a single process-wide primitive, so a raise reaches it directly and no
// other thread can consume the same token first. Distinct from mp_hal_signal_event(),
// which pokes one primitive shared by every waiter: that one has exactly one legitimate
// consumer, whichever waiter reads it first, and is not reliable for a thread that is not
// entitled to it.
#ifndef MICROPY_HAL_HAS_WAKE_OBJ
#define MICROPY_HAL_HAS_WAKE_OBJ (0)
#endif

#if MICROPY_HAL_HAS_WAKE_OBJ

// Opaque; the port defines the struct in its own mphalport.h. Storage belongs to the port
// and outlives every caller, so nothing outside the port ever holds a pointer into caller
// memory.
typedef struct _mp_hal_wake_obj_t mp_hal_wake_obj_t;

// This thread's wake object, claimed on the first call and held until the thread ends.
// NULL if the port has none left to give, which is a supported answer, not a failure: the
// caller falls back to whatever bounded-wait cadence it used without one. Claiming once
// per thread rather than once per wait means nothing has to be released when a wait
// returns, including when an exception unwinds out of it, and a nested wait on the same
// thread shares the one object rather than needing a second.
mp_hal_wake_obj_t *mp_hal_wake_obj_this_thread(void);

// Consumes the object's raised state. Call only after a wait on it has reported it
// raised: the backing latches, so draining ahead of a wait discards the raise and leaves
// that wait with nothing to end it.
void mp_hal_wake_obj_drain(mp_hal_wake_obj_t *w);

// Raises every currently claimed object. Must be safe to call from anywhere
// mp_hal_signal_event() itself is safe to call: a hard ISR, a second core, a
// non-MicroPython RTOS task, or a POSIX signal handler.
void mp_hal_wake_obj_signal_all(void);

#else

#define mp_hal_wake_obj_signal_all() (void)0

#endif

// How a waiter composes its own object with everything else it is waiting on has no
// port-neutral spelling, so it is deliberately not part of the contract above. Each port
// that adopts wake objects exports whatever composition primitive it has behind its own
// capability macro, and core code that uses one is guarded to match. The descriptor below
// is the only such primitive today.
#ifndef MICROPY_HAL_WAKE_OBJ_HAS_POSIX_FD
#define MICROPY_HAL_WAKE_OBJ_HAS_POSIX_FD (0)
#endif

#if MICROPY_HAL_WAKE_OBJ_HAS_POSIX_FD
// A descriptor that polls readable while the object is raised, for a caller running its
// own poll()/select() set. Negative once the HAL has been torn down. Deliberately not
// part of the generic contract above: a port whose wake object is not descriptor-backed
// leaves MICROPY_HAL_WAKE_OBJ_HAS_POSIX_FD at 0 and never declares this.
int mp_hal_wake_obj_posix_fd(mp_hal_wake_obj_t *w);
#endif

#if MICROPY_PY_SELECT_EVENT_SOURCE

// Monotonic count of mp_event_signal() calls. A waiter compares a snapshot of this
// against the current value to learn whether a SOURCE_SIGNAL entry may have readied
// since the snapshot was taken; it is never read-and-cleared, so a raise is visible to
// every waiter rather than only the first to notice it. Wrap is harmless because every
// use is an inequality test against a snapshot taken before the raises being detected.
//
// The count is bumped before mp_event_signal() invokes the port's raise primitive
// (mp_hal_signal_event()), and that primitive is the ordering point a waiter's own wait
// call pairs with: on unix, the write() to the wake descriptor, ordered against the
// waiter's read() of it. A driver calling mp_event_signal() must complete its own state
// write before the call, which the SOURCE_SIGNAL contract already requires (py/stream.h).
// A port whose raise primitive carries no release semantics of its own must supply one,
// since this variable's declaration alone does not provide it.
//
// Movement here is the only thing that tells a waiter to re-ask a SOURCE_SIGNAL entry
// whose own descriptor did not fire, so a lost increment is a missed wake rather than a
// missed optimisation. MICROPY_EVENT_SIGNAL_COUNT_INC() below is what makes it lossless.
extern volatile uint32_t mp_event_signal_count;

// PORT OBLIGATION, two parts. The default of each is enough only where raises cannot
// preempt one another; a port whose raises can (threads, or an ISR interrupting a raise)
// must override both.
//
// Lossless. Two raisers must not coalesce into one bump: were the second to land between
// the first's load and store, a waiter snapshotting the intermediate value would then see
// the first's store leave the count where that snapshot already sits, hiding the second
// raise from every later pass.
//
// Ordered. The increment must be a release and the read an acquire, so that a waiter
// observing movement also observes the driver's state write that preceded it. Not because
// the count carries data, but because it is the sole trigger for re-asking an entry, and
// the ioctl that ask performs reads exactly that state. Release/acquire puts the guarantee
// on the object the reader actually consults. A port could instead lean on its raise
// primitive, whose syscall or barrier happens to order these anyway, but that guarantee
// lives in an unrelated object and disappears the day two apparently independent lines are
// reordered; the pairing here has to keep being satisfied to compile as written.
// Deliberately no default. The obvious one, a plain read-modify-write and a plain read,
// satisfies neither obligation above: on a single-core MCU `++count` is load/add/store, so a
// raise from an ISR landing between the load and the store drops an increment, which is a
// missed wake. A default that silently fails the contract stated directly above it is worse
// than a compile error naming it, and enabling this feature is exactly the moment a port
// should be made to decide.
//
// A port whose raises genuinely cannot preempt one another - no ISR raises, no second core,
// no threads - may define these as the plain operations, but must say so rather than
// inherit it by absence.
#if !defined(MICROPY_EVENT_SIGNAL_COUNT_INC) || !defined(MICROPY_EVENT_SIGNAL_COUNT_GET)
#error "MICROPY_PY_SELECT_EVENT_SOURCE requires the port to define both MICROPY_EVENT_SIGNAL_COUNT_INC() and MICROPY_EVENT_SIGNAL_COUNT_GET(); see py/mphal.h for the lossless and ordered obligations they carry"
#endif

// The external wake-source entry point: bumps mp_event_signal_count and
// then calls mp_hal_signal_event() to end a blocking wait. Distinct from
// calling mp_hal_signal_event() directly, which the scheduler's own
// queued-work hooks do without touching the count; routing a plain
// "runnable work exists" wake through here would make every scheduled
// callback trigger a sweep of every registered SOURCE_SIGNAL entry.
void mp_event_signal(void);

#else

// The count does not exist when the feature is off, so a driver's call site
// stays portable: this collapses to exactly mp_hal_signal_event().
#define mp_event_signal() mp_hal_signal_event()

#endif

#endif // MICROPY_INCLUDED_PY_MPHAL_H
