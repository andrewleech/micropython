/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2014-2023 Damien P. George
 * Copyright (c) 2015-2017 Paul Sokolovsky
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

#include "py/mpconfig.h"
#include "py/runtime.h"
#include "py/obj.h"
#include "py/objlist.h"
#include "py/stream.h"
#include "py/mperrno.h"
#include "py/mphal.h"

#if MICROPY_PY_SELECT

#if MICROPY_PY_SELECT_SELECT && MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
#error "select.select is not supported with MICROPY_PY_SELECT_POSIX_OPTIMISATIONS"
#endif

#if MICROPY_PY_SELECT_EVENT_SOURCE && !MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
#error "MICROPY_PY_SELECT_EVENT_SOURCE requires MICROPY_PY_SELECT_POSIX_OPTIMISATIONS"
#endif

#if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS

#include <string.h>
#include <poll.h>

#if !((MP_STREAM_POLL_RD) == (POLLIN) && \
    (MP_STREAM_POLL_WR) == (POLLOUT) && \
    (MP_STREAM_POLL_ERR) == (POLLERR) && \
    (MP_STREAM_POLL_HUP) == (POLLHUP) && \
    (MP_STREAM_POLL_NVAL) == (POLLNVAL))
#error "With MICROPY_PY_SELECT_POSIX_OPTIMISATIONS enabled, POLL constants must match"
#endif

// When non-file-descriptor objects are on the list to be polled (the polling of
// which involves repeatedly calling ioctl(MP_STREAM_POLL)), this variable sets
// the period between polling these objects.
#define MICROPY_PY_SELECT_IOCTL_CALL_PERIOD_MS (1)

#endif

// Flags for ipoll()
#define FLAG_ONESHOT (1)

// A single pollable object.
typedef struct _poll_obj_t {
    mp_obj_t obj;
    mp_uint_t (*ioctl)(mp_obj_t obj, mp_uint_t request, uintptr_t arg, int *errcode);

    // The mask the caller requested via register()/modify(), persistent for the life of the
    // registration, and the readiness resolved from exactly one authoritative source:
    // poll_obj->ioctl(MP_STREAM_POLL, ...) for a non-fd or ioctl-authoritative object, or
    // pollfd->revents when the fd itself is authoritative for readiness (see pollfd below).
    // Storage distinct from any fd-level mask, so neither can clobber the other.
    mp_uint_t events;
    mp_uint_t revents;

    #if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
    // If non-NULL, points into poll_set_t::pollfds and gives this object something the
    // kernel can sleep on. pollfd->events carries the fd-level direction to watch; today
    // that always mirrors `events` above (see poll_obj_set_events), because every fd-backed
    // object is currently also authoritative for its own readiness. pollfd->revents is raw
    // kernel output: read directly by poll_obj_get_revents() for such an object, and never
    // written by anything but poll() itself.
    struct pollfd *pollfd;

    #if MICROPY_PY_SELECT_EVENT_SOURCE
    // Wake-source properties declared via MP_STREAM_SET_EVENT_SOURCE, probed once at
    // registration alongside the MP_STREAM_GET_FILENO probe (the Register cadence; see
    // py/stream.h). All zero if the object does not implement the ioctl, which is the
    // compatibility path: such an object is handled exactly as it would be without this
    // config, keyed only off GET_FILENO. event_flags is read by poll_obj_fd_is_readiness()
    // and poll_set_all_are_fds(); event_fd_events by poll_set_refresh_fd_events().
    // The cb/ctx passed to the stream at Register are not stored here: every SOURCE_SIGNAL
    // entry is armed with the same (poll_signal_cb, NULL) pair (see poll_set_add_obj), so
    // there is no per-entry value to remember, and the stream itself is what holds onto
    // them for the life of the registration.
    uint16_t event_fd_events;
    uint8_t event_flags;
    #endif
    #endif
} poll_obj_t;

// A set of pollable objects.
typedef struct _poll_set_t {
    // Map containing a dict with key=object to poll, value=its corresponding poll_obj_t.
    mp_map_t map;

    #if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
    // Array of pollfd entries for objects that have a file descriptor.
    unsigned short alloc; // memory allocated for pollfds
    unsigned short max_used; // maximum number of used entries in pollfds
    unsigned short used; // actual number of used entries in pollfds
    struct pollfd *pollfds;

    #if MICROPY_PY_SELECT_EVENT_SOURCE
    // This set's own snapshot of mp_event_signal_count, compared and refreshed by
    // poll_set_take_signal(). Per-set rather than a single shared snapshot, so every
    // poll set sees every raise since its own last check regardless of what any other
    // poll set has already consumed.
    uint32_t signal_seen;
    #endif
    #endif
} poll_set_t;

static void poll_set_init(poll_set_t *poll_set, size_t n) {
    mp_map_init(&poll_set->map, n);
    #if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
    #if MICROPY_PY_SELECT_EVENT_SOURCE
    // pollfds[0] is reserved for ports/unix's wake event, not a registered object, so the
    // set always has one fd-level entry more than poll_set->map.used. Reserved rather than
    // added on demand so the blocking-poll gate below can rely on it being present from the
    // first call, before any object has been registered.
    poll_set->alloc = 1;
    poll_set->max_used = 1;
    poll_set->used = 1;
    poll_set->pollfds = m_new(struct pollfd, 1);
    poll_set->pollfds[0].fd = -1;
    poll_set->pollfds[0].events = POLLIN;
    poll_set->pollfds[0].revents = 0;
    // Snapshot rather than 0, so a raise that happened before this set existed is not
    // mistaken for one that happened after.
    poll_set->signal_seen = mp_event_signal_count;
    #else
    poll_set->alloc = 0;
    poll_set->max_used = 0;
    poll_set->used = 0;
    poll_set->pollfds = NULL;
    #endif
    #endif
}

#if MICROPY_PY_SELECT_SELECT
static void poll_set_deinit(poll_set_t *poll_set) {
    mp_map_deinit(&poll_set->map);
}
#endif

#if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS

static mp_uint_t poll_obj_get_events(poll_obj_t *poll_obj) {
    return poll_obj->events;
}

static void poll_obj_set_events(poll_obj_t *poll_obj, mp_uint_t events) {
    poll_obj->events = events;
    if (poll_obj->pollfd != NULL) {
        // Legacy fd-authoritative path: the fd-level direction to watch is exactly the
        // requested mask. A wake-source-only fd (not authoritative for readiness) would
        // resolve fd_events independently instead of mirroring this write.
        poll_obj->pollfd->events = events;
    }
}

// Whether this entry's readiness is exactly its fd's kernel revents, as opposed to being
// resolved by its own MP_STREAM_POLL ioctl. An entry with no fd at all can never be
// fd-authoritative.
static bool poll_obj_fd_is_readiness(poll_obj_t *poll_obj) {
    if (poll_obj->pollfd == NULL) {
        return false;
    }
    #if MICROPY_PY_SELECT_EVENT_SOURCE
    if (poll_obj->event_flags != 0) {
        return (poll_obj->event_flags & MP_STREAM_EVENT_FD_IS_READINESS) != 0;
    }
    #endif
    // Compatibility default (MICROPY_PY_SELECT_EVENT_SOURCE off, or the stream does not
    // implement MP_STREAM_SET_EVENT_SOURCE): every fd-backed object is authoritative for
    // its own readiness, exactly as every fd-backed object was before this ioctl existed.
    return true;
}

// Whether poll_set_resolve_readiness() must invoke this entry's MP_STREAM_POLL ioctl on
// the current pass, for an entry that is not fd-authoritative (poll_obj_fd_is_readiness()
// false). An entry with a wake-source-only fd (SOURCE_FD without FD_IS_READINESS, e.g.
// SSL) is asked when its own fd just fired, since that is the only other-than-signal
// event observable to the loop that its readiness may have changed; a SOURCE_SIGNAL
// entry (with or without a pollfd) is additionally asked whenever `signalled` is true,
// since its readiness can change from an ISR or another thread with no fd activity at
// all. An entry with no fd at all, the compatibility path for a stream that does not
// implement MP_STREAM_SET_EVENT_SOURCE and has no file descriptor either, has no such
// signal to filter on and must be asked every pass, matching poll_set_poll_once()'s
// unconditional sweep on the non-POSIX path.
static bool poll_obj_should_be_asked(poll_obj_t *poll_obj, bool signalled) {
    if (poll_obj->pollfd == NULL) {
        return true;
    }
    if (poll_obj->pollfd->revents != 0) {
        return true;
    }
    #if MICROPY_PY_SELECT_EVENT_SOURCE
    if (signalled && (poll_obj->event_flags & MP_STREAM_EVENT_SOURCE_SIGNAL) != 0) {
        return true;
    }
    #else
    (void)signalled;
    #endif
    return false;
}

#if MICROPY_PY_SELECT_EVENT_SOURCE
// Whether any SOURCE_SIGNAL entry's readiness may have changed since this poll set last
// asked. Reads mp_event_signal_count once and refreshes this set's own snapshot, so the
// answer reflects every raise since this set's own last check, not since any other poll
// set's; two sets calling this concurrently each get a true answer, never only one of them.
static bool poll_set_take_signal(poll_set_t *poll_set) {
    uint32_t seen = mp_event_signal_count;
    bool moved = seen != poll_set->signal_seen;
    poll_set->signal_seen = seen;
    return moved;
}

// Installed as every SOURCE_SIGNAL entry's cb at Register (see poll_set_add_obj). ctx is
// always NULL: the callback carries no per-poll-set identity, so mp_event_signal() wakes
// and is checked by every poll set that has a SOURCE_SIGNAL entry registered, not only the
// one the raise was meant for.
static void poll_signal_cb(void *ctx) {
    (void)ctx;
    mp_event_signal();
}
#endif

static mp_uint_t poll_obj_get_revents(poll_obj_t *poll_obj) {
    if (poll_obj_fd_is_readiness(poll_obj)) {
        return poll_obj->pollfd->revents;
    }
    return poll_obj->revents;
}

static void poll_obj_set_revents(poll_obj_t *poll_obj, mp_uint_t revents) {
    // Always the resolved-readiness field, never pollfd->revents: that storage belongs to
    // poll() alone, so a subsequent poll() call can never overwrite a value set here.
    poll_obj->revents = revents;
}

// How much (in pollfds) to grow the allocation for poll_set->pollfds by.
#define POLL_SET_ALLOC_INCREMENT (4)

static struct pollfd *poll_set_add_fd(poll_set_t *poll_set, int fd) {
    struct pollfd *free_slot = NULL;

    if (poll_set->used == poll_set->max_used) {
        // No free slots below max_used, so expand max_used (and possibly allocate).
        if (poll_set->max_used >= poll_set->alloc) {
            size_t new_alloc = poll_set->alloc + POLL_SET_ALLOC_INCREMENT;
            // Try to grow in-place.
            struct pollfd *new_fds = m_renew_maybe(struct pollfd, poll_set->pollfds, poll_set->alloc, new_alloc, false);
            if (!new_fds) {
                // Failed to grow in-place. Do a new allocation and copy over the pollfd values.
                new_fds = m_new(struct pollfd, new_alloc);
                memcpy(new_fds, poll_set->pollfds, sizeof(struct pollfd) * poll_set->alloc);

                // Update existing poll_obj_t to update their pollfd field to
                // point to the same offset inside the new allocation.
                for (mp_uint_t i = 0; i < poll_set->map.alloc; ++i) {
                    if (!mp_map_slot_is_filled(&poll_set->map, i)) {
                        continue;
                    }

                    poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set->map.table[i].value);
                    if (!poll_obj) {
                        // This is the one we're currently adding,
                        // poll_set_add_obj doesn't assign elem->value until
                        // afterwards.
                        continue;
                    }

                    // An entry with no fd at all has poll_obj->pollfd == NULL; rebasing
                    // that against the old allocation's base would compute a wild pointer
                    // and hand it to an object that is not supposed to have a pollfd.
                    if (poll_obj->pollfd != NULL) {
                        poll_obj->pollfd = new_fds + (poll_obj->pollfd - poll_set->pollfds);
                    }
                }

                // Delete the old allocation.
                m_del(struct pollfd, poll_set->pollfds, poll_set->alloc);
            }

            poll_set->pollfds = new_fds;
            poll_set->alloc = new_alloc;
        }
        free_slot = &poll_set->pollfds[poll_set->max_used++];
    } else {
        // There should be a free slot below max_used.
        #if MICROPY_PY_SELECT_EVENT_SOURCE
        // Start at 1: pollfds[0] is the reserved wake-event slot (see poll_set_init), which
        // sits at fd == -1 whenever the wake event is momentarily unavailable, e.g. before
        // the first poll() call in this set or while gated off between iterations. Scanning
        // from 0 would read that as a free slot and hand the reservation to this object.
        unsigned int start = 1;
        #else
        unsigned int start = 0;
        #endif
        for (unsigned int i = start; i < poll_set->max_used; ++i) {
            struct pollfd *slot = &poll_set->pollfds[i];
            if (slot->fd == -1) {
                free_slot = slot;
                break;
            }
        }
        assert(free_slot != NULL);
    }

    free_slot->fd = fd;
    ++poll_set->used;

    return free_slot;
}

// Whether the wait loop may block on poll() for the full remaining deadline instead of the
// period-capped sweep below. True when every registered entry has a pollfd: the map/used
// count comparison below is exactly that, since poll_set_add_fd() is invoked for every
// registered entry that has any fd at all, composed or fully authoritative.
//
// A composed entry -- fd-backed but not fd-authoritative (e.g. SSL: SOURCE_FD without
// FD_IS_READINESS), readiness resolved by its own MP_STREAM_POLL ioctl -- qualifies for the
// same deadline block as a fully authoritative one, given two things elsewhere in this loop
// that make it safe: its own fd is already in poll_set->pollfds regardless of authoritative-
// ness (poll_obj_set_events() puts it there unconditionally), so new kernel activity on it
// wakes poll() directly, and its readiness can only change as the result of this loop's own
// ioctl call -- never spontaneously while nothing is polling it -- so there is nothing for a
// deadline block to miss between one wake and the next. What it cannot do on its own is
// surface readiness that already existed *before* this wait began (e.g. a TLS record larger
// than the caller's last read() left a remainder decrypted-but-unread, with nothing new at
// the fd level to signal it): that has no fd signal to wake a block on, which is exactly why
// poll_set_poll_until_ready_or_timeout() below asks every composed entry once, unconditionally,
// before ever computing this gate's timeout -- not only after poll() returns.
//
// An entry with no fd at all still cannot qualify, and does not reach the reasoning above: it
// has nothing in poll_set->pollfds to wake it in the first place, composed or not, so the
// count check alone already excludes it.
static bool poll_set_all_are_fds(poll_set_t *poll_set) {
    #if MICROPY_PY_SELECT_EVENT_SOURCE
    // poll_set->used counts the reserved wake-event slot at pollfds[0] (see poll_set_init),
    // which is not a registered object, so map.used is one less than used whenever every
    // registered entry also has a pollfd.
    return poll_set->map.used == (mp_uint_t)(poll_set->used - 1);
    #else
    return poll_set->map.used == poll_set->used;
    #endif
}

#else

static inline mp_uint_t poll_obj_get_events(poll_obj_t *poll_obj) {
    return poll_obj->events;
}

static inline void poll_obj_set_events(poll_obj_t *poll_obj, mp_uint_t events) {
    poll_obj->events = events;
}

static inline mp_uint_t poll_obj_get_revents(poll_obj_t *poll_obj) {
    return poll_obj->revents;
}

static inline void poll_obj_set_revents(poll_obj_t *poll_obj, mp_uint_t revents) {
    poll_obj->revents = revents;
}

#endif

static void poll_set_add_obj(poll_set_t *poll_set, const mp_obj_t *obj, mp_uint_t obj_len, mp_uint_t events, bool or_events) {
    for (mp_uint_t i = 0; i < obj_len; i++) {
        mp_map_elem_t *elem = mp_map_lookup(&poll_set->map, mp_obj_id(obj[i]), MP_MAP_LOOKUP_ADD_IF_NOT_FOUND);
        if (elem->value == MP_OBJ_NULL) {
            // object not found; get its ioctl and add it to the poll list

            // If an exception is raised below when adding the new object then the map entry for that
            // object remains unpopulated, and methods like poll() may crash.  This case is not handled.

            poll_obj_t *poll_obj = m_new_obj(poll_obj_t);
            poll_obj->obj = obj[i];

            #if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
            int fd = -1;
            #if MICROPY_PY_SELECT_EVENT_SOURCE
            poll_obj->event_fd_events = 0;
            poll_obj->event_flags = 0;
            #endif
            if (mp_obj_is_int(obj[i])) {
                // A file descriptor integer passed in as the object, so use it directly.
                fd = mp_obj_get_int(obj[i]);
                if (fd < 0) {
                    mp_raise_ValueError(NULL);
                }
                poll_obj->ioctl = NULL;
            } else {
                // An object passed in.  Check if it has a file descriptor.
                const mp_stream_p_t *stream_p = mp_get_stream_raise(obj[i], MP_STREAM_OP_IOCTL);
                poll_obj->ioctl = stream_p->ioctl;
                int err;
                mp_uint_t res = stream_p->ioctl(obj[i], MP_STREAM_GET_FILENO, 0, &err);
                if (res != MP_STREAM_ERROR) {
                    fd = res;
                }
                #if MICROPY_PY_SELECT_EVENT_SOURCE
                // The Register cadence (py/stream.h): called once, here, alongside the
                // GET_FILENO probe above. A stream that does not implement the ioctl
                // returns MP_STREAM_ERROR and poll_obj->event_flags stays 0, which is the
                // same compatibility path as a stream that never implements it at all.
                // A Python-level ioctl() (the IOBase trampoline in py/modio.c) can raise
                // instead of returning MP_STREAM_ERROR for a request it does not
                // recognise; caught here for the same reason, so an unadapted stream
                // never crashes registration. cb/ctx are stored by the stream only if it
                // declares SOURCE_SIGNAL; poll_signal_cb carries no per-poll-set identity
                // (see its definition), so ctx is NULL.
                mp_stream_event_source_t source = {
                    .cb = poll_signal_cb, .ctx = NULL, .events = events, .op = MP_STREAM_EVENT_OP_REGISTER,
                    .fd = -1, .fd_events = 0, .flags = 0,
                };
                nlr_buf_t nlr;
                if (nlr_push(&nlr) == 0) {
                    mp_uint_t source_res = stream_p->ioctl(obj[i], MP_STREAM_SET_EVENT_SOURCE, (uintptr_t)&source, &err);
                    if (source_res != MP_STREAM_ERROR) {
                        poll_obj->event_fd_events = source.fd_events;
                        poll_obj->event_flags = source.flags;
                        if ((source.flags & MP_STREAM_EVENT_SOURCE_FD) != 0 && source.fd >= 0) {
                            // The Register cadence's own answer for which fd is a wake
                            // source is authoritative over the plain GET_FILENO probe
                            // above: a driver may answer GET_FILENO with a fd that is not
                            // the one its wake events arrive on (e.g. a control fd
                            // distinct from a data fd), and only this ioctl knows which
                            // one to give poll() a stake in.
                            fd = source.fd;
                        }
                    }
                    nlr_pop();
                }
                #endif
            }
            if (fd >= 0) {
                // Object has a file descriptor so add it to pollfds.
                poll_obj->pollfd = poll_set_add_fd(poll_set, fd);
            } else {
                // Object doesn't have a file descriptor.
                poll_obj->pollfd = NULL;
            }
            #else
            const mp_stream_p_t *stream_p = mp_get_stream_raise(obj[i], MP_STREAM_OP_IOCTL);
            poll_obj->ioctl = stream_p->ioctl;
            #endif

            poll_obj_set_events(poll_obj, events);
            poll_obj_set_revents(poll_obj, 0);
            elem->value = MP_OBJ_FROM_PTR(poll_obj);
        } else {
            // object exists; update its events
            poll_obj_t *poll_obj = (poll_obj_t *)MP_OBJ_TO_PTR(elem->value);
            #if MICROPY_PY_SELECT_SELECT
            if (or_events) {
                events |= poll_obj_get_events(poll_obj);
            }
            #else
            (void)or_events;
            #endif
            poll_obj_set_events(poll_obj, events);
        }
    }
}

#if !MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
// For each object in the poll set, poll it once. Only used by the non-POSIX wait loop
// below; the POSIX-optimised path resolves readiness via poll_set_resolve_readiness()
// instead, which additionally accounts for objects with a wake-source-only fd.
static mp_uint_t poll_set_poll_once(poll_set_t *poll_set, size_t *rwx_num) {
    mp_uint_t n_ready = 0;
    for (mp_uint_t i = 0; i < poll_set->map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&poll_set->map, i)) {
            continue;
        }

        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set->map.table[i].value);

        int errcode;
        mp_int_t ret = poll_obj->ioctl(poll_obj->obj, MP_STREAM_POLL, poll_obj_get_events(poll_obj), &errcode);
        poll_obj_set_revents(poll_obj, ret);

        if (ret == -1) {
            // error doing ioctl
            mp_raise_OSError(errcode);
        }

        if (ret != 0) {
            // object is ready
            n_ready += 1;
            #if MICROPY_PY_SELECT_SELECT
            if (rwx_num != NULL) {
                if (ret & MP_STREAM_POLL_RD) {
                    rwx_num[0] += 1;
                }
                if (ret & MP_STREAM_POLL_WR) {
                    rwx_num[1] += 1;
                }
                if ((ret & ~(MP_STREAM_POLL_RD | MP_STREAM_POLL_WR)) != 0) {
                    rwx_num[2] += 1;
                }
            }
            #else
            (void)rwx_num;
            #endif
        }
    }
    return n_ready;
}
#endif // !MICROPY_PY_SELECT_POSIX_OPTIMISATIONS

#if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS && MICROPY_PY_SELECT_EVENT_SOURCE
// The Refresh cadence (py/stream.h): re-queries fd_events for every entry that declared
// FD_DYNAMIC, immediately before the poll() call that can block on it. Only fd_events is
// consulted; cb/ctx are never touched here, per the ioctl's Refresh contract. Writes
// pollfd->events only when the direction actually changed, matching libev/libuv/Zephyr,
// since FD_DYNAMIC re-queries every cycle and the answer is usually identical.
static void poll_set_refresh_fd_events(poll_set_t *poll_set) {
    for (mp_uint_t i = 0; i < poll_set->map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&poll_set->map, i)) {
            continue;
        }

        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set->map.table[i].value);
        if (poll_obj->pollfd == NULL || !(poll_obj->event_flags & MP_STREAM_EVENT_FD_DYNAMIC)) {
            continue;
        }

        mp_stream_event_source_t source = {
            .cb = NULL, .ctx = NULL, .events = poll_obj->events, .op = MP_STREAM_EVENT_OP_REFRESH,
            .fd = -1, .fd_events = 0, .flags = 0,
        };
        int errcode;
        mp_uint_t res = poll_obj->ioctl(poll_obj->obj, MP_STREAM_SET_EVENT_SOURCE, (uintptr_t)&source, &errcode);
        if (res != MP_STREAM_ERROR && poll_obj->pollfd->events != source.fd_events) {
            poll_obj->pollfd->events = source.fd_events;
        }
    }
}
#endif

#if MICROPY_PY_SELECT_EVENT_SOURCE
// Clears every registered entry's resolved readiness back to "nothing known yet this
// call". Called once, before the pre-block ask below has a chance to return without ever
// running poll(): without this, an entry whose readiness this call never touches before
// that early return (a fd_is_readiness entry, resolved only by the poll() syscall further
// down this same loop and so not yet run this call, or a fd-less entry that
// poll_set_resolve_pre_block() below does not ask) would still carry whatever non-zero
// value poll() left in it from a previous, unrelated call to this same poll set. The
// caller then walks every registered entry and collects every non-zero one into a list
// sized for the n_ready this function returned, so a stale non-zero here is a mismatched
// count, not just a wrong answer -- one entry too many for the list's allocation.
static void poll_set_reset_revents(poll_set_t *poll_set) {
    for (mp_uint_t i = 0; i < poll_set->map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&poll_set->map, i)) {
            continue;
        }
        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set->map.table[i].value);
        if (poll_obj->pollfd != NULL) {
            poll_obj->pollfd->revents = 0;
        }
        poll_obj_set_revents(poll_obj, 0);
    }
}

// Ask every non-fd-authoritative entry that needs it before
// poll_set_poll_until_ready_or_timeout() computes its first timeout, not only after poll()
// returns. Two cadences share this function because they are asked here for two different
// reasons, and an entry may qualify for either or both:
//
// - A composed entry (fd-backed but not fd-authoritative, e.g. SSL) is asked once,
//   unconditionally, on `first_pass`. Its readiness can only change as the result of this
//   loop's own ioctl call, never spontaneously, so the one thing this ask has to catch is
//   readiness left over from before this call began (e.g. a TLS record larger than the
//   caller's last read() left a remainder decrypted-but-unread), which has no fd signal to
//   filter on. Nothing changes for it between one pass and the next beyond what its own fd
//   firing already reports, so `first_pass` is enough; asking it again on `signalled` would
//   just be extra ioctls for an answer poll_set_resolve_readiness() already has.
// - A SOURCE_SIGNAL entry (fd-backed or not) is asked on `first_pass`, for the same
//   leftover-readiness reason, and again whenever `signalled` is true. Its readiness can
//   change from an ISR or another thread at any point, including while this loop is
//   between passes running scheduled callbacks; the intervening work can drain the wake
//   token that would otherwise have carried news of the raise (mp_hal_wake_event_wait_tv()
//   is a counting drain, not one this loop controls), so the counter checked via
//   `signalled` is the only thing left that still catches it.
//
// See poll_set_all_are_fds()'s doc comment for why the composed-entry ask in particular
// has to happen before the deadline-sleep gate is consulted.
static mp_uint_t poll_set_resolve_pre_block(poll_set_t *poll_set, bool first_pass, bool signalled) {
    mp_uint_t n_ready = 0;
    for (mp_uint_t i = 0; i < poll_set->map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&poll_set->map, i)) {
            continue;
        }
        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set->map.table[i].value);
        if (poll_obj_fd_is_readiness(poll_obj)) {
            continue;
        }
        bool is_signal = (poll_obj->event_flags & MP_STREAM_EVENT_SOURCE_SIGNAL) != 0;
        bool composed_fd = poll_obj->pollfd != NULL;
        if (!((composed_fd && first_pass) || (is_signal && (first_pass || signalled)))) {
            continue;
        }
        int errcode;
        mp_int_t ret = poll_obj->ioctl(poll_obj->obj, MP_STREAM_POLL, poll_obj_get_events(poll_obj), &errcode);
        if (ret == -1) {
            mp_raise_OSError(errcode);
        }
        poll_obj_set_revents(poll_obj, ret);
        if (poll_obj->revents != 0) {
            n_ready += 1;
        }
    }
    return n_ready;
}
#endif

#if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
// Resolve readiness for every entry in the poll set from whichever source is authoritative
// for it, called once poll() has produced fresh kernel state for every fd in the set. Each
// entry takes its readiness from exactly one place, decided by poll_obj_fd_is_readiness():
// a plain read of pollfd->revents (cheap, never filtered), or its own MP_STREAM_POLL ioctl,
// filtered by poll_obj_should_be_asked() so a wake-source-only fd is asked exactly when its
// own fd fired, or a SOURCE_SIGNAL entry is asked exactly when it declared or its fd fired.
// An entry with no fd at all is always asked: poll_obj_should_be_asked() returns true
// unconditionally for it, since it has no fd signal to filter on and no other caller ever
// asks it first (poll_set_resolve_pre_block() above skips a fd-less non-signal entry).
static mp_uint_t poll_set_resolve_readiness(poll_set_t *poll_set) {
    mp_uint_t n_ready = 0;
    #if MICROPY_PY_SELECT_EVENT_SOURCE
    bool signalled = poll_set_take_signal(poll_set);
    #else
    bool signalled = false;
    #endif
    for (mp_uint_t i = 0; i < poll_set->map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&poll_set->map, i)) {
            continue;
        }

        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set->map.table[i].value);

        if (poll_obj_fd_is_readiness(poll_obj)) {
            if (poll_obj->pollfd->revents != 0) {
                n_ready += 1;
            }
            continue;
        }

        if (poll_obj_should_be_asked(poll_obj, signalled)) {
            int errcode;
            mp_int_t ret = poll_obj->ioctl(poll_obj->obj, MP_STREAM_POLL, poll_obj_get_events(poll_obj), &errcode);
            if (ret == -1) {
                mp_raise_OSError(errcode);
            }
            poll_obj_set_revents(poll_obj, ret);
        }
        // Else skipped: poll_obj->revents keeps the value from the previous pass, which is
        // necessarily 0; a non-zero value here would have made the previous pass return
        // rather than call poll() and loop back round.

        if (poll_obj->revents != 0) {
            n_ready += 1;
        }
    }
    return n_ready;
}
#endif // MICROPY_PY_SELECT_POSIX_OPTIMISATIONS

static mp_uint_t poll_set_poll_until_ready_or_timeout(poll_set_t *poll_set, size_t *rwx_num, mp_uint_t timeout) {
    mp_uint_t start_ticks = mp_hal_ticks_ms();
    bool has_timeout = timeout != (mp_uint_t)-1;

    mp_handle_pending(true);

    #if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS

    // select.select() cannot coexist with these optimisations (see the #error above), so
    // rwx_num is always NULL here; only the non-POSIX loop below uses it.
    (void)rwx_num;

    #if MICROPY_PY_SELECT_EVENT_SOURCE
    // Whether this is the first iteration of the loop below, used only by the pre-block
    // ask: composed and signal entries need a leftover-readiness check before the first
    // poll() of this call, never on later passes for the composed half (see
    // poll_set_resolve_pre_block()'s doc comment). Meaningless without event sources, since
    // nothing else in this loop varies by iteration count.
    bool first_pass = true;
    #endif

    for (;;) {
        #if MICROPY_PY_SELECT_EVENT_SOURCE
        poll_set_refresh_fd_events(poll_set);

        bool signalled = poll_set_take_signal(poll_set);
        if (first_pass || signalled) {
            if (first_pass) {
                poll_set_reset_revents(poll_set);
            }
            mp_uint_t n_ready = poll_set_resolve_pre_block(poll_set, first_pass, signalled);
            if (n_ready > 0) {
                return n_ready;
            }
        }
        #endif

        #if MICROPY_PY_SELECT_EVENT_SOURCE
        // Refresh the reserved slot immediately before poll() blocks on it. -1 when this
        // thread may not act on the wake event (mp_sched_thread_can_run_callbacks(), see the
        // caveat at its definition): registering the real fd here on a thread that will
        // never drain it would be a lost wakeup for whichever thread actually needed it, so
        // this thread's poll() falls back to the periodic sweep below instead.
        //
        // KNOWN GAP: on a GIL build, mp_sched_thread_can_run_callbacks() returns true for
        // every thread, not just the one that owns this wait. Two threads each running their
        // own select.poll() with the wake fd both injected would race to drain it, and
        // whichever loses never sees its own wakeup. Fine for this branch's single-threaded
        // asyncio measurement; not a resolved answer for increment 1 in general (unix-sleep-
        // process-pending flagged this, 2026-07-29).
        poll_set->pollfds[0].fd = mp_sched_thread_can_run_callbacks() ? mp_hal_wake_event_fd() : -1;
        poll_set->pollfds[0].events = POLLIN;
        poll_set->pollfds[0].revents = 0;
        #endif

        MP_THREAD_GIL_EXIT();

        // Compute the timeout.
        int t = MICROPY_PY_SELECT_IOCTL_CALL_PERIOD_MS;
        if (poll_set_all_are_fds(poll_set)) {
            // All our pollables are file descriptors, so we can use a blocking
            // poll and let it (the underlying system) handle the timeout.
            if (timeout == (mp_uint_t)-1) {
                t = -1;
            } else {
                mp_uint_t delta = mp_hal_ticks_ms() - start_ticks;
                if (delta >= timeout) {
                    t = 0;
                } else {
                    t = timeout - delta;
                }
            }
        }

        // Call system poll for those objects that have a file descriptor.
        int n = poll(poll_set->pollfds, poll_set->max_used, t);

        MP_THREAD_GIL_ENTER();

        // The call to poll() may have been interrupted, but per PEP 475 we must retry if the
        // signal is EINTR (this implements a special case of calling MP_HAL_RETRY_SYSCALL()).
        if (n == -1) {
            int err = errno;
            if (err != EINTR) {
                mp_raise_OSError(err);
            }
        }

        #if MICROPY_PY_SELECT_EVENT_SOURCE
        // Drain only after polling readable, never before: the event is level-triggered and
        // counting, so draining ahead of the wait it was meant to end discards the raise and
        // leaves that wait with nothing to wake it (mp_hal_wake_event_fd()'s contract).
        if (poll_set->pollfds[0].revents != 0) {
            mp_hal_wake_event_drain();
        }
        #endif

        // Resolve readiness for every entry now that poll() has produced fresh state. On the
        // first iteration, the pre-block ask above already asked every composed entry once;
        // this call still asks any entry whose own fd just fired (poll_obj_should_be_asked()),
        // composed or not, and always asks any entry with no fd at all.
        mp_uint_t n_ready = poll_set_resolve_readiness(poll_set);

        // Return if an object is ready, or if the timeout expired.
        if (n_ready > 0 || (has_timeout && mp_hal_ticks_ms() - start_ticks >= timeout)) {
            return n_ready;
        }

        // Drain before pumping: mp_sched_unlock() raises for any callbacks left queued
        // after a pump, and draining afterwards would eat that token. The wake-event drain
        // above already happened before this point, so this is only the queued-callback pump.
        //
        // This would be mp_event_wait_ms() but the call to poll() above already includes a
        // delay.
        mp_event_handle_nowait();
        #if MICROPY_PY_SELECT_EVENT_SOURCE
        first_pass = false;
        #endif
    }

    #else

    for (;;) {
        // poll the objects
        mp_uint_t n_ready = poll_set_poll_once(poll_set, rwx_num);
        uint32_t elapsed = mp_hal_ticks_ms() - start_ticks;
        if (n_ready > 0 || (has_timeout && elapsed >= timeout)) {
            return n_ready;
        }
        if (has_timeout) {
            mp_event_wait_ms(timeout - elapsed);
        } else {
            mp_event_wait_indefinite();
        }
    }

    #endif
}

#if MICROPY_PY_SELECT_SELECT
// select(rlist, wlist, xlist[, timeout])
static mp_obj_t select_select(size_t n_args, const mp_obj_t *args) {
    // get array data from tuple/list arguments
    size_t rwx_len[3];
    mp_obj_t *r_array, *w_array, *x_array;
    mp_obj_get_array(args[0], &rwx_len[0], &r_array);
    mp_obj_get_array(args[1], &rwx_len[1], &w_array);
    mp_obj_get_array(args[2], &rwx_len[2], &x_array);

    // get timeout
    mp_uint_t timeout = -1;
    if (n_args == 4) {
        if (args[3] != mp_const_none) {
            #if MICROPY_PY_BUILTINS_FLOAT
            float timeout_f = mp_obj_get_float_to_f(args[3]);
            if (timeout_f >= 0) {
                timeout = (mp_uint_t)(timeout_f * 1000);
            }
            #else
            timeout = mp_obj_get_int(args[3]) * 1000;
            #endif
        }
    }

    // merge separate lists and get the ioctl function for each object
    poll_set_t poll_set;
    poll_set_init(&poll_set, rwx_len[0] + rwx_len[1] + rwx_len[2]);
    poll_set_add_obj(&poll_set, r_array, rwx_len[0], MP_STREAM_POLL_RD, true);
    poll_set_add_obj(&poll_set, w_array, rwx_len[1], MP_STREAM_POLL_WR, true);
    poll_set_add_obj(&poll_set, x_array, rwx_len[2], MP_STREAM_POLL_ERR | MP_STREAM_POLL_HUP, true);

    // poll all objects
    rwx_len[0] = rwx_len[1] = rwx_len[2] = 0;
    poll_set_poll_until_ready_or_timeout(&poll_set, rwx_len, timeout);

    // one or more objects are ready, or we had a timeout
    mp_obj_t list_array[3];
    list_array[0] = mp_obj_new_list(rwx_len[0], NULL);
    list_array[1] = mp_obj_new_list(rwx_len[1], NULL);
    list_array[2] = mp_obj_new_list(rwx_len[2], NULL);
    rwx_len[0] = rwx_len[1] = rwx_len[2] = 0;
    for (mp_uint_t i = 0; i < poll_set.map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&poll_set.map, i)) {
            continue;
        }
        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(poll_set.map.table[i].value);
        if (poll_obj->revents & MP_STREAM_POLL_RD) {
            ((mp_obj_list_t *)MP_OBJ_TO_PTR(list_array[0]))->items[rwx_len[0]++] = poll_obj->obj;
        }
        if (poll_obj->revents & MP_STREAM_POLL_WR) {
            ((mp_obj_list_t *)MP_OBJ_TO_PTR(list_array[1]))->items[rwx_len[1]++] = poll_obj->obj;
        }
        if ((poll_obj->revents & ~(MP_STREAM_POLL_RD | MP_STREAM_POLL_WR)) != 0) {
            ((mp_obj_list_t *)MP_OBJ_TO_PTR(list_array[2]))->items[rwx_len[2]++] = poll_obj->obj;
        }
    }
    poll_set_deinit(&poll_set);
    return mp_obj_new_tuple(3, list_array);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mp_select_select_obj, 3, 4, select_select);
#endif // MICROPY_PY_SELECT_SELECT

typedef struct _mp_obj_poll_t {
    mp_obj_base_t base;
    poll_set_t poll_set;
    short iter_cnt;
    short iter_idx;
    int flags;
    // callee-owned tuple
    mp_obj_t ret_tuple;
} mp_obj_poll_t;

// register(obj[, eventmask])
static mp_obj_t poll_register(size_t n_args, const mp_obj_t *args) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(args[0]);
    mp_uint_t events;
    if (n_args == 3) {
        events = mp_obj_get_int(args[2]);
    } else {
        events = MP_STREAM_POLL_RD | MP_STREAM_POLL_WR;
    }
    poll_set_add_obj(&self->poll_set, &args[1], 1, events, false);
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(poll_register_obj, 2, 3, poll_register);

// unregister(obj)
static mp_obj_t poll_unregister(mp_obj_t self_in, mp_obj_t obj_in) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(self_in);
    mp_map_elem_t *elem = mp_map_lookup(&self->poll_set.map, mp_obj_id(obj_in), MP_MAP_LOOKUP_REMOVE_IF_FOUND);

    #if MICROPY_PY_SELECT_POSIX_OPTIMISATIONS
    if (elem != NULL) {
        poll_obj_t *poll_obj = (poll_obj_t *)MP_OBJ_TO_PTR(elem->value);
        #if MICROPY_PY_SELECT_EVENT_SOURCE
        bool is_signal = (poll_obj->event_flags & MP_STREAM_EVENT_SOURCE_SIGNAL) != 0;
        #endif
        if (poll_obj->pollfd != NULL) {
            poll_obj->pollfd->fd = -1;
            --self->poll_set.used;
            #if MICROPY_PY_SELECT_EVENT_SOURCE
            // Cleared so nothing can reach this pollfd through poll_obj between here and
            // the Unregister ioctl below, which can re-enter Python. Not needed without
            // that ioctl call: no code below this point ever reads poll_obj->pollfd again.
            poll_obj->pollfd = NULL;
            #endif
        }
        elem->value = MP_OBJ_NULL;
        #if MICROPY_PY_SELECT_EVENT_SOURCE
        // Issued only for a stream that declared SOURCE_SIGNAL, after every poll-set
        // mutation above is complete: mp_map_lookup() with MP_MAP_LOOKUP_REMOVE_IF_FOUND
        // has already cleared this slot's key and decremented map->used, so poll_obj is
        // already invisible to poll_set_add_fd()'s realloc fixup loop, and elem->value and
        // pollfd are cleared above, so a driver that re-enters Python from its own
        // unregister handling can only observe a poll set that is already fully consistent
        // without this entry.
        if (is_signal) {
            mp_stream_event_source_t source = {
                .cb = NULL, .ctx = NULL, .events = 0, .op = MP_STREAM_EVENT_OP_UNREGISTER,
                .fd = -1, .fd_events = 0, .flags = 0,
            };
            int errcode;
            poll_obj->ioctl(poll_obj->obj, MP_STREAM_SET_EVENT_SOURCE, (uintptr_t)&source, &errcode);
        }
        #endif
    }
    #else
    (void)elem;
    #endif

    // TODO raise KeyError if obj didn't exist in map
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_2(poll_unregister_obj, poll_unregister);

// modify(obj, eventmask)
static mp_obj_t poll_modify(mp_obj_t self_in, mp_obj_t obj_in, mp_obj_t eventmask_in) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(self_in);
    mp_map_elem_t *elem = mp_map_lookup(&self->poll_set.map, mp_obj_id(obj_in), MP_MAP_LOOKUP);
    if (elem == NULL) {
        mp_raise_OSError(MP_ENOENT);
    }
    poll_obj_set_events((poll_obj_t *)MP_OBJ_TO_PTR(elem->value), mp_obj_get_int(eventmask_in));
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_3(poll_modify_obj, poll_modify);

static mp_uint_t poll_poll_internal(uint n_args, const mp_obj_t *args) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(args[0]);

    // work out timeout (its given already in ms)
    mp_uint_t timeout = -1;
    int flags = 0;
    if (n_args >= 2) {
        if (args[1] != mp_const_none) {
            mp_int_t timeout_i = mp_obj_get_int(args[1]);
            if (timeout_i >= 0) {
                timeout = timeout_i;
            }
        }
        if (n_args >= 3) {
            flags = mp_obj_get_int(args[2]);
        }
    }

    self->flags = flags;

    return poll_set_poll_until_ready_or_timeout(&self->poll_set, NULL, timeout);
}

static mp_obj_t poll_poll(size_t n_args, const mp_obj_t *args) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(args[0]);
    mp_uint_t n_ready = poll_poll_internal(n_args, args);

    // one or more objects are ready, or we had a timeout
    mp_obj_list_t *ret_list = MP_OBJ_TO_PTR(mp_obj_new_list(n_ready, NULL));
    n_ready = 0;
    for (mp_uint_t i = 0; i < self->poll_set.map.alloc; ++i) {
        if (!mp_map_slot_is_filled(&self->poll_set.map, i)) {
            continue;
        }
        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(self->poll_set.map.table[i].value);
        if (poll_obj_get_revents(poll_obj) != 0) {
            mp_obj_t tuple[2] = {poll_obj->obj, MP_OBJ_NEW_SMALL_INT(poll_obj_get_revents(poll_obj))};
            ret_list->items[n_ready++] = mp_obj_new_tuple(2, tuple);
        }
    }
    return MP_OBJ_FROM_PTR(ret_list);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(poll_poll_obj, 1, 2, poll_poll);

static mp_obj_t poll_ipoll(size_t n_args, const mp_obj_t *args) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(args[0]);

    if (self->ret_tuple == MP_OBJ_NULL) {
        self->ret_tuple = mp_obj_new_tuple(2, NULL);
    }

    int n_ready = poll_poll_internal(n_args, args);
    self->iter_cnt = n_ready;
    self->iter_idx = 0;

    return args[0];
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(poll_ipoll_obj, 1, 3, poll_ipoll);

static mp_obj_t poll_iternext(mp_obj_t self_in) {
    mp_obj_poll_t *self = MP_OBJ_TO_PTR(self_in);

    if (self->iter_cnt == 0) {
        return MP_OBJ_STOP_ITERATION;
    }

    self->iter_cnt--;

    for (mp_uint_t i = self->iter_idx; i < self->poll_set.map.alloc; ++i) {
        self->iter_idx++;
        if (!mp_map_slot_is_filled(&self->poll_set.map, i)) {
            continue;
        }
        poll_obj_t *poll_obj = MP_OBJ_TO_PTR(self->poll_set.map.table[i].value);
        if (poll_obj_get_revents(poll_obj) != 0) {
            mp_obj_tuple_t *t = MP_OBJ_TO_PTR(self->ret_tuple);
            t->items[0] = poll_obj->obj;
            t->items[1] = MP_OBJ_NEW_SMALL_INT(poll_obj_get_revents(poll_obj));
            if (self->flags & FLAG_ONESHOT) {
                // Don't poll next time, until new event mask will be set explicitly
                poll_obj_set_events(poll_obj, 0);
            }
            return MP_OBJ_FROM_PTR(t);
        }
    }

    assert(!"inconsistent number of poll active entries");
    self->iter_cnt = 0;
    return MP_OBJ_STOP_ITERATION;
}

static const mp_rom_map_elem_t poll_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_register), MP_ROM_PTR(&poll_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_unregister), MP_ROM_PTR(&poll_unregister_obj) },
    { MP_ROM_QSTR(MP_QSTR_modify), MP_ROM_PTR(&poll_modify_obj) },
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&poll_poll_obj) },
    { MP_ROM_QSTR(MP_QSTR_ipoll), MP_ROM_PTR(&poll_ipoll_obj) },
};
static MP_DEFINE_CONST_DICT(poll_locals_dict, poll_locals_dict_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    mp_type_poll,
    MP_QSTR_poll,
    MP_TYPE_FLAG_ITER_IS_ITERNEXT,
    iter, poll_iternext,
    locals_dict, &poll_locals_dict
    );

// poll()
static mp_obj_t select_poll(void) {
    mp_obj_poll_t *poll = mp_obj_malloc(mp_obj_poll_t, &mp_type_poll);
    poll_set_init(&poll->poll_set, 0);
    poll->iter_cnt = 0;
    poll->ret_tuple = MP_OBJ_NULL;
    return MP_OBJ_FROM_PTR(poll);
}
MP_DEFINE_CONST_FUN_OBJ_0(mp_select_poll_obj, select_poll);

static const mp_rom_map_elem_t mp_module_select_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_select) },
    #if MICROPY_PY_SELECT_SELECT
    { MP_ROM_QSTR(MP_QSTR_select), MP_ROM_PTR(&mp_select_select_obj) },
    #endif
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&mp_select_poll_obj) },
    { MP_ROM_QSTR(MP_QSTR_POLLIN), MP_ROM_INT(MP_STREAM_POLL_RD) },
    { MP_ROM_QSTR(MP_QSTR_POLLOUT), MP_ROM_INT(MP_STREAM_POLL_WR) },
    { MP_ROM_QSTR(MP_QSTR_POLLERR), MP_ROM_INT(MP_STREAM_POLL_ERR) },
    { MP_ROM_QSTR(MP_QSTR_POLLHUP), MP_ROM_INT(MP_STREAM_POLL_HUP) },
};

static MP_DEFINE_CONST_DICT(mp_module_select_globals, mp_module_select_globals_table);

const mp_obj_module_t mp_module_select = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&mp_module_select_globals,
};

MP_REGISTER_EXTENSIBLE_MODULE(MP_QSTR_select, mp_module_select);

#endif // MICROPY_PY_SELECT
